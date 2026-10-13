"""Opt-in browser cookies, restricted to the requested video platform."""
from urllib.parse import urlparse

PLATFORMS = {
    'douyin.com': ('douyin.com',),
    'bilibili.com': ('bilibili.com',),
    'b23.tv': ('bilibili.com', 'b23.tv'),
    'youtube.com': ('youtube.com',),
    'youtu.be': ('youtube.com', 'youtu.be'),
    'tiktok.com': ('tiktok.com',),
    'vimeo.com': ('vimeo.com',),
}


def platform_domains(url):
    host = (urlparse(url).hostname or '').lower()
    for domain, domains in PLATFORMS.items():
        if host == domain or host.endswith('.' + domain):
            return domains
    return ()


def add_browser_cookies(ydl, browser, url, logger):
    from yt_dlp.cookies import extract_cookies_from_browser
    if browser not in ('edge', 'chrome', 'firefox'):
        raise ValueError('不支持的浏览器')
    domains = platform_domains(url)
    jar = extract_cookies_from_browser(browser, logger=logger)
    count = 0
    for cookie in jar:
        domain = cookie.domain.lstrip('.').lower()
        if any(domain == allowed or domain.endswith('.' + allowed) for allowed in domains):
            ydl.cookiejar.set_cookie(cookie)
            count += 1
    if not count:
        if getattr(logger, 'auth_warning', False):
            raise RuntimeError('BROWSER_DECRYPT: 浏览器 Cookies 无法解密。')
        raise RuntimeError('NO_SITE_COOKIES: 在所选浏览器中没有找到该平台的有效 Cookies。请先用这个浏览器打开视频，再重试。')


def friendly_error(message):
    lower = message.lower()
    if 'bridge_unavailable' in lower:
        return 'bridge_unavailable', 'Kimi 浏览器扩展未连接。请在 Edge 的 Kimi 扩展中开启“本地代理远程控制”，然后重试。'
    if 'bridge_media_missing' in lower:
        return 'bridge_media_missing', '页面已经打开，但没有取得可读取的视频或图文内容。请在 Edge 中确认帖子可以正常浏览后重试。'
    if 'bridge_media_invalid' in lower or 'bridge_download_failed' in lower:
        return 'bridge_download_failed', '已经打开视频，但媒体地址无法安全下载。请重试或改用文件上传。'
    if any(word in lower for word in ('could not copy', 'database is locked', 'permission denied', 'being used by another process')):
        return 'browser_locked', '浏览器的 Cookies 文件正在被占用。请保存浏览器中的工作并完全退出所选浏览器，再重试；也可上传视频文件。'
    if any(word in lower for word in ('decrypt', 'dpapi', 'app-bound', 'app bound', 'v20')):
        return 'browser_encrypted', '浏览器的加密保护阻止了 Cookies 读取。请改用 Cookies 文件或上传视频 / 音频，无需修改浏览器安全设置。'
    if 'no_site_cookies' in lower or ('could not find' in lower and ('cookies' in lower or 'browser' in lower)):
        return 'browser_missing', '没有找到该平台的浏览器 Cookies。请用所选浏览器打开原视频后重试；多个浏览器账号时，也可使用 Cookies 文件。'
    if any(word in lower for word in ('fresh cookies', 'sign in', 'login', 'cookies are needed', '403', '429')):
        return 'platform_auth', '视频尚未读取：平台要求有效的浏览器 Cookies。点击“设置后重试”选择浏览器登录状态，或改用文件上传。'
    return 'processing_error', message[-1600:]
