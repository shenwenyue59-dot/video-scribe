"""Fetch playable social video through the user's connected Kimi browser extension."""
import json
import os
from pathlib import Path
import time
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

import httpx


BRIDGE_URL = os.environ.get('KIMI_BRIDGE_URL', 'http://127.0.0.1:10086/command')
SESSION = 'video-scribe-app'
PLATFORMS = {
    'douyin': {
        'pages': ('douyin.com',),
        'media': ('douyinvod.com', 'bytecdn.cn', 'douyinstatic.com'),
    },
    'xiaohongshu': {
        'pages': ('xiaohongshu.com', 'xhslink.com'),
        'media': ('xhscdn.com',),
    },
}


def _host_matches(host, domains):
    host = (host or '').lower()
    return any(host == domain or host.endswith('.' + domain) for domain in domains)


def is_douyin(url):
    return _host_matches(urlparse(url).hostname, ('douyin.com',))


def browser_platform(url):
    host = urlparse(url).hostname
    for name, config in PLATFORMS.items():
        if _host_matches(host, config['pages']):
            return name
    return None


def is_browser_source(url):
    return browser_platform(url) is not None


def validate_media_url(url, platform='douyin'):
    parsed = urlparse(url or '')
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise RuntimeError('BRIDGE_MEDIA_INVALID: 浏览器返回了无效的媒体地址。')
    allowed = PLATFORMS.get(platform, {}).get('media', ())
    if not _host_matches(parsed.hostname, allowed):
        raise RuntimeError('BRIDGE_MEDIA_INVALID: 媒体地址不属于受支持的视频域名。')
    return url


def command(action, args, timeout=35):
    payload = json.dumps({'action': action, 'args': args, 'session': SESSION}, ensure_ascii=False).encode('utf-8')
    request = Request(BRIDGE_URL, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=timeout) as response:
            envelope = json.loads(response.read().decode('utf-8'))
    except Exception as exc:
        raise RuntimeError(f'BRIDGE_UNAVAILABLE: 无法连接 Kimi 浏览器扩展：{exc}') from exc
    if not envelope.get('ok'):
        message = (envelope.get('error') or {}).get('message') or '未知错误'
        raise RuntimeError(f'BRIDGE_UNAVAILABLE: Kimi 浏览器扩展未就绪：{message}')
    return envelope.get('data') or {}


def browser_media(url):
    platform = browser_platform(url)
    if not platform:
        raise RuntimeError('BRIDGE_MEDIA_INVALID: 当前链接不支持浏览器读取。')
    tabs = command('list_tabs', {}).get('tabs') or []
    command('navigate', {'url': url, 'newTab': not bool(tabs), 'group_title': '逐字稿自动取视频'})
    script = """(() => { const v=document.querySelector('video'); const src=v?.currentSrc||v?.src||''; const d=document.querySelector('meta[name=\"description\"]')?.content||''; return JSON.stringify({src,title:document.title||d||'',duration:Number.isFinite(v?.duration)?v.duration:null,pageUrl:location.href}); })()"""
    last = {}
    for _ in range(24):
        result = command('evaluate', {'code': script})
        try:
            last = json.loads(result.get('value') or '{}')
        except (TypeError, ValueError):
            last = {}
        if last.get('src') and not last['src'].startswith('blob:'):
            last['src'] = validate_media_url(last['src'], platform)
            last['platform'] = platform
            return last
        time.sleep(.75)
    raise RuntimeError('BRIDGE_MEDIA_MISSING: 视频页面已打开，但播放器没有返回媒体地址。请在 Edge 中确认视频可以正常播放。')


def download_media(source_url, destination, referer, platform='douyin', limit=500 * 1024 * 1024):
    current = validate_media_url(source_url, platform)
    headers = {'Referer': referer, 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36'}
    with httpx.Client(timeout=httpx.Timeout(60, read=120), headers=headers) as client:
        for _ in range(4):
            with client.stream('GET', current) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get('location')
                    if not location:
                        raise RuntimeError('BRIDGE_DOWNLOAD_FAILED: 视频下载跳转缺少目标地址。')
                    current = validate_media_url(urljoin(current, location), platform)
                    continue
                response.raise_for_status()
                declared = int(response.headers.get('content-length') or 0)
                if declared > limit:
                    raise RuntimeError('视频文件超过 500 MB，请上传较小的文件。')
                size = 0
                with Path(destination).open('wb') as output:
                    for chunk in response.iter_bytes(1024 * 1024):
                        size += len(chunk)
                        if size > limit:
                            raise RuntimeError('视频文件超过 500 MB，请上传较小的文件。')
                        output.write(chunk)
                if not size:
                    raise RuntimeError('BRIDGE_DOWNLOAD_FAILED: 下载到的视频文件为空。')
                return size
        raise RuntimeError('BRIDGE_DOWNLOAD_FAILED: 视频下载跳转次数过多。')
