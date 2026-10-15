import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse, quote
import uuid
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from opencc import OpenCC
from transcript import extract_urls, to_csv, to_srt
from titles import fallback_title, usable_title
from correction import correct_segments, remember_corrections

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('SCRIBE_DATA_DIR', str(ROOT / '.data'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
JOBS = {}
LOCK = threading.RLock()
POOL = ThreadPoolExecutor(max_workers=1)
PROCESSES = {}
TERMINAL = {'done', 'error', 'cancelled'}
PUBLIC_TOKEN = os.environ.get('SCRIBE_PUBLIC_TOKEN', '')
ALLOWED = ('douyin.com', 'xiaohongshu.com', 'xhslink.com', 'bilibili.com', 'b23.tv', 'youtube.com', 'youtu.be', 'tiktok.com', 'vimeo.com')
SIMPLIFIER = OpenCC('t2s')


def simplify_job(job):
    changed = False
    for key in ('title', 'text'):
        value = job.get(key)
        if isinstance(value, str):
            converted = SIMPLIFIER.convert(value)
            if converted != value:
                job[key] = converted
                changed = True
    for segment in job.get('segments') or []:
        value = segment.get('text')
        if isinstance(value, str):
            converted = SIMPLIFIER.convert(value)
            if converted != value:
                segment['text'] = converted
                changed = True
    if job.get('status') == 'done' and job.get('url') and not usable_title(job.get('title')):
        job['title'] = fallback_title(job.get('segments'))
        job['title_source'] = '自动生成（未读取到原视频标题）'
        changed = True
    elif job.get('title_source') == '逐字稿内容生成' and not str(job.get('title', '')).startswith('【自动生成】'):
        job['title'] = '【自动生成】' + str(job.get('title') or '未命名逐字稿')
        job['title_source'] = '自动生成（未读取到原视频标题）'
        changed = True
    if (job.get('status') == 'done' and job.get('smart_correct') and
            '本地语音识别' in job.get('source', '') and job.get('lexicon_version', 0) < 2):
        original = deepcopy(job.get('raw_segments') or job.get('segments') or [])
        corrected = correct_segments(original, job.get('title', ''))
        if corrected != original:
            job.setdefault('raw_segments', original)
            job.setdefault('raw_text', '\n'.join(segment['text'] for segment in original))
            job['segments'] = corrected
            job['text'] = '\n'.join(segment['text'] for segment in corrected)
        job['lexicon_version'] = 2
        changed = True
    return changed


def save(job):
    simplify_job(job)
    path = DATA / (job['id'] + '.json')
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


for path in DATA.glob('*.json'):
    try:
        item = json.loads(path.read_text(encoding='utf-8'))
        changed = simplify_job(item)
        if item['status'] not in TERMINAL:
            item.update(status='error', message='上次运行已中断，请重新提交。')
            changed = True
        if changed:
            save(item)
        JOBS[item['id']] = item
    except (ValueError, KeyError):
        pass
# Reuse an original platform title across repeated submissions of the same URL.
known_titles = {item.get('url'): item.get('title') for item in JOBS.values()
                if item.get('url') and usable_title(item.get('title')) and
                item.get('title_source') in (None, '平台页面', '同链接历史记录')}
for item in JOBS.values():
    known = known_titles.get(item.get('url'))
    if known and item.get('title') != known:
        item['title'] = known
        item['title_source'] = '同链接历史记录'
        save(item)
# Interrupted workers can leave source media and credentials behind; remove only our temporary directory.
TEMP = DATA / 'temporary'
if TEMP.exists():
    shutil.rmtree(TEMP)
TEMP.mkdir(exist_ok=True)


@asynccontextmanager
async def lifespan(app):
    yield
    with LOCK:
        for job in JOBS.values():
            if job['status'] not in TERMINAL:
                job.update(status='cancelled', message='应用已关闭')
                save(job)
        for process in PROCESSES.values():
            if process.poll() is None:
                process.kill()
    POOL.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title='逐字 · Video Scribe', lifespan=lifespan)
PUBLIC_HOST_SUFFIXES = ('.trycloudflare.com', '.ngrok-free.dev')
app.add_middleware(TrustedHostMiddleware, allowed_hosts=[
    '127.0.0.1', 'localhost', 'testserver', '*.trycloudflare.com', '*.ngrok-free.dev'])


@app.middleware('http')
async def same_origin(request: Request, call_next):
    public_host = (request.url.hostname or '').endswith(PUBLIC_HOST_SUFFIXES)
    if public_host and PUBLIC_TOKEN:
        query_token = request.query_params.get('access', '')
        cookie_token = request.cookies.get('video_scribe_access', '')
        valid_query = bool(PUBLIC_TOKEN and secrets.compare_digest(query_token, PUBLIC_TOKEN))
        valid_cookie = bool(PUBLIC_TOKEN and secrets.compare_digest(cookie_token, PUBLIC_TOKEN))
        if valid_query and request.method in ('GET', 'HEAD'):
            response = RedirectResponse(request.url.path or '/', status_code=302)
            response.set_cookie('video_scribe_access', PUBLIC_TOKEN, secure=True, httponly=True,
                                samesite='strict', max_age=86400)
            response.headers['Referrer-Policy'] = 'no-referrer'
            return response
        if not valid_cookie:
            return JSONResponse({'detail': '访问链接无效或已经过期。'}, status_code=401)
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': '只允许从本机应用页面提交。'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': '不允许跨站请求。'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Referrer-Policy'] = 'no-referrer'
    return response


def update(identifier, **changes):
    with LOCK:
        job = JOBS.get(identifier)
        if not job:
            return
        if job['status'] == 'cancelled':
            return
        job.update(changes)
        save(job)


def execute(config):
    identifier = config['id']
    directory = Path(config['directory'])
    process = None
    try:
        with LOCK:
            if identifier not in JOBS or JOBS[identifier]['status'] == 'cancelled':
                return
            update(identifier, status='running', message='正在启动')
            config_path = directory / 'config.json'
            config_path.write_text(json.dumps(config), encoding='utf-8')
            process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'worker.py'), str(config_path)],
                                       cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       encoding='utf-8', env={**os.environ, 'PYTHONUTF8': '1'},
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            PROCESSES[identifier] = process
        timer = threading.Timer(7200, process.kill)
        timer.daemon = True
        timer.start()
        try:
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event['kind'] == 'status':
                    update(identifier, message=event['message'])
                elif event['kind'] == 'metadata':
                    update(identifier, **{k: v for k, v in event.items() if k != 'kind'})
                elif event['kind'] == 'result':
                    update(identifier, status='done', message='逐字稿已生成', segments=event['segments'],
                           text='\n'.join(s['text'] for s in event['segments']), source=event['source'], language=event['language'])
                elif event['kind'] == 'error':
                    update(identifier, status='error', message=event['message'],
                           error_code=event.get('error_code'), technical_detail=event.get('technical_detail', ''))
            process.wait()
            with LOCK:
                if JOBS[identifier]['status'] not in TERMINAL:
                    update(identifier, status='error', message='处理进程意外退出或超时。请尝试较小模型、较短文件，或检查网络。')
        finally:
            timer.cancel()
    except Exception as exc:
        update(identifier, status='error', message=str(exc)[:1200])
    finally:
        if process and process.poll() is None:
            process.kill()
            process.wait()
        with LOCK:
            PROCESSES.pop(identifier, None)
        shutil.rmtree(directory, ignore_errors=True)


def options(language, model, mode):
    if language not in ('auto', 'zh', 'en', 'ja', 'ko') or model not in ('tiny', 'base', 'small') or mode not in ('auto', 'captions', 'speech'):
        raise HTTPException(400, '无效的识别选项')
    with LOCK:
        if sum(j['status'] not in TERMINAL for j in JOBS.values()) >= 30:
            raise HTTPException(429, '队列已满，请等待现有任务完成。')


def create_job(title, url, language, model, mode, smart_correct=True):
    if url:
        with LOCK:
            previous = next((item.get('title') for item in JOBS.values()
                             if item.get('url') == url and usable_title(item.get('title')) and
                             item.get('title_source') in (None, '平台页面', '同链接历史记录')), None)
        title = previous or title
    identifier = uuid.uuid4().hex
    directory = TEMP / identifier
    directory.mkdir()
    job = {'id': identifier, 'title': title, 'url': url, 'created': datetime.now(timezone.utc).isoformat(),
           'status': 'queued', 'message': '等待处理', 'segments': [], 'text': '', 'source': '',
           'language': language, 'model': model, 'mode': mode, 'smart_correct': smart_correct, 'duration': None}
    config = {'id': identifier, 'directory': str(directory), 'url': url, 'language': language,
              'model': model, 'mode': mode, 'smart_correct': smart_correct, 'title': title}
    return job, config


def submit(job, config):
    with LOCK:
        JOBS[job['id']] = job
        save(job)
    POOL.submit(execute, config)


@app.get('/api/health')
def health():
    return {'app': 'video-scribe', 'status': 'ok', 'engine': 'faster-whisper / CPU', 'local': True}


@app.get('/api/jobs')
def jobs():
    with LOCK:
        return [{k: v for k, v in j.items() if k not in ('segments', 'text', 'raw_segments', 'raw_text')}
                for j in sorted(JOBS.values(), key=lambda j: j['created'], reverse=True)]


@app.get('/api/jobs/{identifier}')
def detail(identifier: str):
    with LOCK:
        if identifier not in JOBS:
            raise HTTPException(404, '任务不存在')
        return deepcopy(JOBS[identifier])


@app.put('/api/jobs/{identifier}/text')
async def edit_transcript(identifier: str, request: Request):
    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(400, '修改内容格式无效')
    text = payload.get('text') if isinstance(payload, dict) else None
    remember = bool(payload.get('remember')) if isinstance(payload, dict) else False
    if not isinstance(text, str) or not text.strip() or len(text) > 100000:
        raise HTTPException(400, '逐字稿内容为空或过长')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    with LOCK:
        job = JOBS.get(identifier)
        if not job:
            raise HTTPException(404, '任务不存在')
        if job.get('status') != 'done':
            raise HTTPException(409, '任务尚未完成')
        segments = job.get('segments') or []
        if len(lines) != len(segments):
            raise HTTPException(400, f'请保留原有的 {len(segments)} 行，每行对应一个时间段')
        pairs = [(segment.get('text', ''), line) for segment, line in zip(segments, lines)
                 if segment.get('text', '').strip() != line]
        if remember and pairs:
            remember_corrections(pairs)
        job.setdefault('raw_segments', deepcopy(segments))
        job.setdefault('raw_text', job.get('text', ''))
        for segment, line in zip(segments, lines):
            segment['text'] = line
        job['text'] = '\n'.join(lines)
        job['manually_corrected'] = True
        save(job)
        return {'ok': True, 'changed': len(pairs), 'remembered': bool(remember and pairs)}


@app.post('/api/links')
async def links(links: str = Form(...), language: str = Form('auto'), model: str = Form('base'),
                mode: str = Form('auto'), cookies: UploadFile | None = File(None),
                browser: str = Form('none'), browser_consent: bool = Form(False),
                smart_correct: bool = Form(True), glossary: str = Form('')):
    options(language, model, mode)
    glossary_terms = [term.strip() for term in re.split(r'[,，\n]', glossary) if term.strip()]
    if len(glossary) > 5000 or len(glossary_terms) > 200 or any(len(term) > 24 for term in glossary_terms):
        raise HTTPException(400, '自定义词库最多 200 个词，每个词不超过 24 个字。')
    if browser not in ('none', 'edge', 'chrome', 'firefox'):
        raise HTTPException(400, '请选择支持的浏览器')
    if browser != 'none' and not browser_consent:
        raise HTTPException(400, '请勾选同意本次任务读取所选浏览器 Cookies，或选择“不使用”。')
    if browser != 'none' and cookies and cookies.filename:
        raise HTTPException(400, '浏览器登录状态和 Cookies 文件请选择一种。')
    urls = extract_urls(links)
    if not 1 <= len(urls) <= 10:
        raise HTTPException(400, '请粘贴 1–10 个视频链接。支持直接粘贴分享文案。')
    for url in urls:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        try:
            port = parsed.port
        except ValueError:
            raise HTTPException(400, '链接中的端口无效')
        if parsed.username or parsed.password or port not in (None, 80, 443) or not any(host == d or host.endswith('.' + d) for d in ALLOWED):
            raise HTTPException(400, '目前支持抖音、小红书、B站、YouTube、TikTok 和 Vimeo；其他来源请上传文件。')
    with LOCK:
        if sum(j['status'] not in TERMINAL for j in JOBS.values()) + len(urls) > 30:
            raise HTTPException(429, '添加后队列会超过 30 个任务，请等待现有任务完成。')
    cookie_data = None
    if cookies and cookies.filename:
        cookie_data = await cookies.read(2 * 1024 * 1024 + 1)
        await cookies.close()
        if len(cookie_data) > 2 * 1024 * 1024 or b'Cookie File' not in cookie_data[:200]:
            raise HTTPException(400, 'Cookies 文件必须是 Netscape 格式，且小于 2 MB。')
    identifiers = []
    for url in urls:
        job, config = create_job(url, url, language, model, mode, smart_correct)
        config['glossary'] = glossary_terms
        if browser != 'none':
            config['browser'] = browser
        if cookie_data:
            cookie_path = Path(config['directory']) / 'cookies.txt'
            cookie_path.write_bytes(cookie_data)
            config['cookies'] = str(cookie_path)
        submit(job, config)
        identifiers.append(job['id'])
    return {'ids': identifiers}


@app.post('/api/upload')
async def upload(file: UploadFile = File(...), language: str = Form('auto'), model: str = Form('base'),
                 smart_correct: bool = Form(True), glossary: str = Form('')):
    options(language, model, 'speech')
    glossary_terms = [term.strip() for term in re.split(r'[,，\n]', glossary) if term.strip()]
    if len(glossary) > 5000 or len(glossary_terms) > 200 or any(len(term) > 24 for term in glossary_terms):
        raise HTTPException(400, '自定义词库最多 200 个词，每个词不超过 24 个字。')
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in ('.mp4', '.m4a', '.mp3', '.wav', '.webm', '.mov', '.mkv', '.aac', '.flac', '.ogg'):
        raise HTTPException(400, '不支持该文件格式，请上传视频或音频。')
    job, config = create_job(Path(file.filename).name, '', language, model, 'speech', smart_correct)
    config['glossary'] = glossary_terms
    media = Path(config['directory']) / ('upload' + suffix)
    try:
        size = 0
        with media.open('wb') as target:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > 500 * 1024 * 1024:
                    raise HTTPException(413, '文件超过 500 MB，请压缩或分段。')
                target.write(chunk)
        if not size:
            raise HTTPException(400, '文件为空')
    except Exception:
        shutil.rmtree(Path(config['directory']), ignore_errors=True)
        raise
    finally:
        await file.close()
    config['media'] = str(media)
    submit(job, config)
    return {'ids': [job['id']]}


@app.post('/api/jobs/{identifier}/cancel')
def cancel(identifier: str):
    with LOCK:
        job = JOBS.get(identifier)
        if not job:
            raise HTTPException(404, '任务不存在')
        if job['status'] not in TERMINAL:
            job.update(status='cancelled', message='已取消')
            save(job)
            process = PROCESSES.get(identifier)
            if process and process.poll() is None:
                process.kill()
        return {'ok': True}


@app.delete('/api/jobs/{identifier}')
def delete(identifier: str):
    with LOCK:
        job = JOBS.get(identifier)
        if not job:
            raise HTTPException(404, '任务不存在')
        if job['status'] not in TERMINAL or identifier in PROCESSES:
            raise HTTPException(409, '请先取消任务，等待处理停止后再删除。')
        JOBS.pop(identifier)
        (DATA / (identifier + '.json')).unlink(missing_ok=True)
    return {'ok': True}


@app.delete('/api/delete-selected')
def delete_selected(ids: str):
    requested = list(dict.fromkeys(item for item in ids.split(',') if item))
    if not requested or len(requested) > 100 or any(not re.fullmatch(r'[a-f0-9]{32}', item) for item in requested):
        raise HTTPException(400, '删除选择无效')
    with LOCK:
        jobs = [JOBS.get(item) for item in requested]
        if any(not job for job in jobs):
            raise HTTPException(404, '所选记录中有任务不存在')
        if any(job['status'] not in TERMINAL or job['id'] in PROCESSES for job in jobs):
            raise HTTPException(409, '处理中或排队中的任务不能批量删除，请先取消。')
        for job in jobs:
            JOBS.pop(job['id'], None)
            (DATA / (job['id'] + '.json')).unlink(missing_ok=True)
    return {'ok': True, 'deleted': len(jobs)}


def filename(job):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', job['title'])[:90].strip(' .') or '逐字稿'


def selected_jobs(ids=None):
    with LOCK:
        if ids:
            requested = list(dict.fromkeys(item for item in ids.split(',') if item))
            if len(requested) > 100 or any(not re.fullmatch(r'[a-f0-9]{32}', item) for item in requested):
                raise HTTPException(400, '导出选择无效')
            completed = deepcopy([JOBS[item] for item in requested
                                  if item in JOBS and JOBS[item]['status'] == 'done'])
            if len(completed) != len(requested):
                raise HTTPException(400, '所选任务包含不存在或尚未完成的内容')
        else:
            completed = deepcopy([j for j in JOBS.values() if j['status'] == 'done'])
    if not completed:
        raise HTTPException(400, '还没有已完成的逐字稿')
    for job in completed:
        simplify_job(job)
    return completed


def workbook_bytes(jobs):
    import xlsxwriter
    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(output, {'in_memory': True})
    title_format = workbook.add_format({'bold': True, 'font_size': 14, 'font_color': '#294F3C'})
    label_format = workbook.add_format({'bold': True, 'bg_color': '#EEF3E7', 'font_color': '#557046', 'border': 1})
    header_format = workbook.add_format({'bold': True, 'bg_color': '#315A42', 'font_color': 'white', 'border': 1, 'align': 'center'})
    cell_format = workbook.add_format({'text_wrap': True, 'valign': 'top', 'border': 1, 'border_color': '#DDE5D8'})
    time_format = workbook.add_format({'valign': 'top', 'border': 1, 'border_color': '#DDE5D8', 'align': 'center'})
    used = set()
    for number, job in enumerate(jobs, 1):
        base = re.sub(r'[\[\]:*?/\\]', '_', job['title']).strip(" '")[:31] or f'逐字稿{number}'
        sheet_name = base
        suffix = 2
        while sheet_name.lower() in used:
            marker = f'-{suffix}'
            sheet_name = base[:31-len(marker)] + marker
            suffix += 1
        used.add(sheet_name.lower())
        sheet = workbook.add_worksheet(sheet_name)
        sheet.hide_gridlines(2)
        sheet.set_column('A:A', 9)
        sheet.set_column('B:C', 15)
        sheet.set_column('D:D', 80)
        sheet.merge_range('A1:D1', job['title'], title_format)
        metadata = [('视频标题', job['title']), ('视频链接', job.get('url', '')), ('内容来源', job.get('source', ''))]
        for row, (label, value) in enumerate(metadata, 1):
            sheet.write(row, 0, label, label_format)
            if label == '视频链接' and value:
                sheet.write_url(row, 1, value, cell_format, value)
            else:
                sheet.merge_range(row, 1, row, 3, value, cell_format)
        header_row = 5
        for column, heading in enumerate(('序号', '开始时间', '结束时间', '逐字内容')):
            sheet.write(header_row, column, heading, header_format)
        for offset, segment in enumerate(job['segments'], 1):
            row = header_row + offset
            sheet.write_number(row, 0, offset, time_format)
            sheet.write(row, 1, f"{segment['start']:.3f}", time_format)
            sheet.write(row, 2, f"{segment['end']:.3f}", time_format)
            sheet.write(row, 3, segment['text'], cell_format)
        last_row = max(header_row + 1, header_row + len(job['segments']))
        sheet.autofilter(header_row, 0, last_row, 3)
        sheet.freeze_panes(header_row + 1, 1)
    workbook.close()
    return output.getvalue()


@app.get('/api/jobs/{identifier}/export/{extension}')
def export(identifier: str, extension: str):
    job = detail(identifier)
    if job['status'] != 'done':
        raise HTTPException(409, '任务尚未完成')
    if extension not in ('txt', 'srt', 'csv'):
        raise HTTPException(400, '仅支持 TXT、SRT 和 CSV')
    text = {'txt': job['text'], 'srt': to_srt(job['segments']),
            'csv': to_csv(job['segments'], job['title'], job.get('url', ''))}[extension]
    name = quote(filename(job) + '.' + extension)
    return Response(text.encode('utf-8-sig'), media_type='text/plain; charset=utf-8',
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{name}"})


@app.get('/api/export.zip')
def export_all(ids: str | None = None):
    completed = selected_jobs(ids)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for job in completed:
            base = filename(job) + '-' + job['id'][:6]
            archive.writestr(base + '.txt', job['text'].encode('utf-8-sig'))
            archive.writestr(base + '.srt', to_srt(job['segments']).encode('utf-8-sig'))
            archive.writestr(base + '.csv', to_csv(job['segments'], job['title'],
                                                   job.get('url', '')).encode('utf-8-sig'))
    archive_name = 'selected-transcripts.zip' if ids else 'transcripts.zip'
    return Response(output.getvalue(), media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="{archive_name}"'})


@app.get('/api/export.xlsx')
def export_workbook(ids: str | None = None):
    completed = selected_jobs(ids)
    name = 'selected-transcripts.xlsx' if ids else 'all-transcripts.xlsx'
    return Response(workbook_bytes(completed),
                    media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    headers={'Content-Disposition': f'attachment; filename="{name}"'})


app.mount('/', StaticFiles(directory=ROOT / 'static', html=True), name='static')
