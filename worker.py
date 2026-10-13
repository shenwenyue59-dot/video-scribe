"""Isolated, cancellable media extraction and local speech recognition."""
import json
import os
from pathlib import Path
import sys
import traceback
from transcript import parse_subtitles
from browser_auth import add_browser_cookies, friendly_error
from bridge_media import browser_media, download_media, is_browser_source
from correction import correct_segments, whisper_hotwords
from titles import transcript_title, usable_title

ROOT = Path(__file__).resolve().parent
DLL_HANDLES = []
if os.name == 'nt':
    # App-local runtime: no administrator rights or system-wide VC++ install needed.
    DLL_HANDLES = [os.add_dll_directory(sys.prefix), os.add_dll_directory(str(Path(sys.prefix) / 'Scripts'))]
os.environ.setdefault('HF_HOME', str(ROOT / '.models'))
os.environ.setdefault('HF_HUB_DISABLE_PROGRESS_BARS', '1')


def emit(kind, **data):
    print(json.dumps({'kind': kind, **data}, ensure_ascii=False), flush=True)


class QuietLogger:
    auth_warning = False
    def debug(self, msg, **kwargs): pass
    def info(self, msg, **kwargs): pass
    def warning(self, msg, **kwargs):
        if any(word in msg.lower() for word in ('decrypt', 'dpapi', 'app-bound', 'v20')):
            self.auth_warning = True
    def error(self, msg, **kwargs): pass


def subtitle_choices(info, language):
    choices = []
    for source, label in [('subtitles', '视频字幕'), ('automatic_captions', '平台自动字幕')]:
        for lang, formats in (info.get(source) or {}).items():
            if lang == 'live_chat':
                continue
            if language != 'auto' and not lang.lower().startswith(language):
                continue
            for fmt in formats:
                if fmt.get('ext') not in ('json3', 'vtt', 'srt'):
                    continue
                language_rank = 0 if lang.lower().startswith('zh') else 1 if lang.startswith('en') else 2
                source_rank = 0 if source == 'subtitles' else 1
                choices.append(((language_rank, source_rank), fmt, lang, label))
    return sorted(choices, key=lambda x: x[0])


def process(config):
    media = config.get('media')
    context_title = config.get('title') or ''
    if config.get('url'):
        # Douyin protects its media API with browser-generated signatures. When the
        # user has connected Kimi Browser Extension, use the real playable page
        # instead of copying encrypted browser cookie databases.
        if is_browser_source(config['url']) and not config.get('cookies') and not config.get('browser'):
            emit('status', message='正在通过 Kimi 浏览器扩展打开内容')
            browser = browser_media(config['url'])
            if usable_title(browser.get('title')):
                context_title = browser['title']
            emit('metadata', title=browser.get('title') or config['url'], duration=browser.get('duration'))
            if browser.get('src'):
                media = str(Path(config['directory']) / 'browser-video.mp4')
                emit('status', message='已取得播放器地址，正在下载视频')
                download_media(browser['src'], media, browser.get('pageUrl') or config['url'],
                               browser.get('platform', 'douyin'))
                emit('status', message='视频下载完成，准备识别语音')
            else:
                emit('status', message='正在读取帖子正文和配图')
                result = []
                post_text = (browser.get('postText') or '').strip()
                if post_text:
                    result.append({'start': 0, 'end': 1, 'text': '【帖子内容】\n' + post_text})
                image_urls = browser.get('imageUrls') or []
                if image_urls:
                    emit('status', message=f'正在识别图片文字 · 0 / {len(image_urls)} 张')
                    from rapidocr_onnxruntime import RapidOCR
                    ocr = RapidOCR()
                    for index, image_url in enumerate(image_urls, 1):
                        image_path = str(Path(config['directory']) / f'image-{index:02}.jpg')
                        download_media(image_url, image_path, browser.get('pageUrl') or config['url'],
                                       'xiaohongshu', limit=25 * 1024 * 1024)
                        lines, _ = ocr(image_path)
                        texts = []
                        for item in lines or []:
                            text = str(item[1]).strip()
                            confidence = float(item[2])
                            if text and confidence >= 0.45 and text not in texts:
                                texts.append(text)
                        if texts:
                            result.append({'start': index, 'end': index + 1,
                                           'text': f'【图片 {index} 文字】\n' + '\n'.join(texts)})
                        emit('status', message=f'正在识别图片文字 · {index} / {len(image_urls)} 张')
                if not result:
                    raise RuntimeError('页面已打开，但没有读取到帖子正文或可识别的图片文字。')
                emit('result', segments=result, source='帖子内容 + 图片文字', language='zh')
                return
        else:
            media = None
    if config.get('url') and not media:
        import yt_dlp
        last_update = [0]
        def hook(data):
            import time
            if time.monotonic() - last_update[0] < 1:
                return
            last_update[0] = time.monotonic()
            total = data.get('total_bytes') or data.get('total_bytes_estimate')
            percent = round(data.get('downloaded_bytes', 0) / total * 100) if total else None
            emit('status', message='正在下载音频' + (f' · {percent}%' if percent is not None else ''))
            if data.get('downloaded_bytes', 0) > 500 * 1024 * 1024:
                raise RuntimeError('视频文件超过 500 MB，请上传较小的音频文件。')
        opts = {'quiet': True, 'no_warnings': True, 'logger': QuietLogger(), 'noplaylist': True,
                'socket_timeout': 25, 'retries': 2, 'fragment_retries': 2,
                'outtmpl': str(Path(config['directory']) / 'media.%(ext)s'),
                'format': 'bestaudio/best', 'max_filesize': 500 * 1024 * 1024,
                'progress_hooks': [hook], 'cachedir': False, 'restrictfilenames': True}
        if config.get('cookies'):
            opts['cookiefile'] = config['cookies']
        emit('status', message='正在读取视频信息和字幕')
        with yt_dlp.YoutubeDL(opts) as ydl:
            if config.get('browser'):
                emit('status', message='正在按本次授权读取所选浏览器的平台 Cookies')
                add_browser_cookies(ydl, config['browser'], config['url'], QuietLogger())
            info = ydl.extract_info(config['url'], download=False)
            if not info or info.get('_type') in ('playlist', 'multi_video') or info.get('is_live'):
                raise RuntimeError('请使用单条已发布视频的链接；暂不支持播放列表或直播。')
            if usable_title(info.get('title')):
                context_title = info['title']
            emit('metadata', title=context_title or config['url'], duration=info.get('duration'))
            if (info.get('duration') or 0) > 7200:
                raise RuntimeError('单条视频最长支持 2 小时，请分段上传。')
            if config['mode'] != 'speech':
                for _, subtitle, lang, label in subtitle_choices(info, config['language']):
                    try:
                        if subtitle.get('data'):
                            raw = subtitle['data']
                        else:
                            with ydl.urlopen(subtitle['url']) as response:
                                raw = response.read(10 * 1024 * 1024 + 1)
                            if len(raw) > 10 * 1024 * 1024:
                                continue
                            raw = raw.decode('utf-8-sig')
                        segments = parse_subtitles(raw, subtitle['ext'])
                        if segments:
                            emit('result', segments=segments, source=label, language=lang)
                            return
                    except Exception:
                        continue
                if config['mode'] == 'captions':
                    raise RuntimeError('没有读取到可用字幕。请选择“自动”或“语音识别”重新提交。')
            emit('status', message='未使用平台字幕，正在获取音频')
            info = ydl.extract_info(config['url'], download=True)
            media = ydl.prepare_filename(info)
            if not Path(media).is_file():
                raise RuntimeError('音频未下载成功，可能超出大小限制；请改用文件上传。')
    emit('status', message='正在加载本地模型；首次使用需要联网下载，可能需要数分钟')
    from faster_whisper import WhisperModel
    smart_correct = bool(config.get('smart_correct', True))
    glossary = config.get('glossary') or []
    model_name = 'small' if smart_correct and config['model'] in ('tiny', 'base') else config['model']
    if smart_correct and model_name != config['model']:
        emit('status', message='智能校正已开启，正在加载精细中文模型')
    model = WhisperModel(model_name, device='cpu', compute_type='int8',
                         cpu_threads=max(1, min(8, (os.cpu_count() or 4) // 2)),
                         download_root=str(ROOT / '.models'))
    emit('status', message='正在识别语音')
    prompt = whisper_hotwords(context_title, glossary) if smart_correct else None
    segments, info = model.transcribe(
        media, language=None if config['language'] == 'auto' else config['language'],
        beam_size=8 if smart_correct else 5, vad_filter=True,
        condition_on_previous_text=smart_correct, initial_prompt=prompt, hotwords=prompt)
    if info.duration > 7200:
        raise RuntimeError('单个文件最长支持 2 小时，请分段上传。')
    emit('metadata', duration=info.duration)
    result = []
    for segment in segments:
        text = segment.text.strip()
        start = max(0, min(segment.start, info.duration))
        end = max(start, min(segment.end, info.duration))
        if text and end > start:
            result.append({'start': round(start, 3), 'end': round(end, 3), 'text': text})
        emit('status', message=f'正在识别语音 · {int(end)} / {int(info.duration)} 秒')
    if not result:
        raise RuntimeError('没有检测到可识别的人声，请检查音轨或换一个文件。')
    if smart_correct and info.language.startswith('zh'):
        emit('status', message='正在结合全文语境校正同音词')
        result = correct_segments(result, context_title, glossary)
    if config.get('url') and not usable_title(context_title):
        context_title = transcript_title(result)
        emit('metadata', title=context_title, title_source='逐字稿内容生成')
    source = '本地语音识别 · 上下文智能校正' if smart_correct else '本地语音识别'
    emit('result', segments=result, source=source, language=info.language)


if __name__ == '__main__':
    try:
        process(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
    except Exception as exc:
        code, message = friendly_error(str(exc))
        emit('error', message=message, error_code=code, technical_detail=str(exc)[-1600:])
        sys.exit(1)
