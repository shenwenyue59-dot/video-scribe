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
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse, quote
import uuid
import zipfile

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from transcript import extract_urls, to_csv, to_srt

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get('SCRIBE_DATA_DIR', str(ROOT / '.data'))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
JOBS = {}
LOCK = threading.RLock()
POOL = ThreadPoolExecutor(max_workers=1)
PROCESSES = {}
TERMINAL = {'done', 'error', 'cancelled'}
ALLOWED = ('douyin.com', 'xiaohongshu.com', 'xhslink.com', 'bilibili.com', 'b23.tv', 'youtube.com', 'youtu.be', 'tiktok.com', 'vimeo.com')


def save(job):
    path = DATA / (job['id'] + '.json')
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(job, ensure_ascii=False), encoding='utf-8')
    temp.replace(path)


for path in DATA.glob('*.json'):
    try:
        item = json.loads(path.read_text(encoding='utf-8'))
        if item['status'] not in TERMINAL:
            item.update(status='error', message='上次运行已中断，请重新提交。')
            save(item)
        JOBS[item['id']] = item
    except (ValueError, KeyError):
        pass
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
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])


@app.middleware('http')
async def same_origin(request: Request, call_next):
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        origin = request.headers.get('origin')
        if origin and origin != str(request.base_url).rstrip('/'):
            return JSONResponse({'detail': '只允许从本机应用页面提交。'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': '不允许跨站请求。'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Cache-Control'] = 'no-store'
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


def create_job(title, url, language, model, mode):
    identifier = uuid.uuid4().hex
    directory = TEMP / identifier
    directory.mkdir()
    job = {'id': identifier, 'title': title, 'url': url, 'created': datetime.now(timezone.utc).isoformat(),
           'status': 'queued', 'message': '等待处理', 'segments': [], 'text': '', 'source': '',
           'language': language, 'model': model, 'mode': mode, 'duration': None}
    config = {'id': identifier, 'directory': str(directory), 'url': url, 'language': language, 'model': model, 'mode': mode}
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
        return [{k: v for k, v in j.items() if k not in ('segments', 'text')}
                for j in sorted(JOBS.values(), key=lambda j: j['created'], reverse=True)]


@app.get('/api/jobs/{identifier}')
def detail(identifier: str):
    with LOCK:
        if identifier not in JOBS:
            raise HTTPException(404, '任务不存在')
        return deepcopy(JOBS[identifier])


@app.post('/api/links')
async def links(links: str = Form(...), language: str = Form('auto'), model: str = Form('base'),
                mode: str = Form('auto'), cookies: UploadFile | None = File(None),
                browser: str = Form('none'), browser_consent: bool = Form(False)):
    options(language, model, mode)
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
        job, config = create_job(url, url, language, model, mode)
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
async def upload(file: UploadFile = File(...), language: str = Form('auto'), model: str = Form('base')):
    options(language, model, 'speech')
    suffix = Path(file.filename or '').suffix.lower()
    if suffix not in ('.mp4', '.m4a', '.mp3', '.wav', '.webm', '.mov', '.mkv', '.aac', '.flac', '.ogg'):
        raise HTTPException(400, '不支持该文件格式，请上传视频或音频。')
    job, config = create_job(Path(file.filename).name, '', language, model, 'speech')
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


def filename(job):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', job['title'])[:90].strip(' .') or '逐字稿'


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


app.mount('/', StaticFiles(directory=ROOT / 'static', html=True), name='static')
