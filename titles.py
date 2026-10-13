"""Choose a useful display title when a platform page omits its metadata."""
import re


GENERIC_TITLES = ('在抖音记录美好生活', '抖音-记录美好生活', '小红书', 'xiaohongshu',
                  '【自动生成】', '原视频标题未读取到')


def usable_title(title):
    value = (title or '').strip()
    return bool(value and not value.startswith(('http://', 'https://')) and
                not any(marker.lower() in value.lower() for marker in GENERIC_TITLES))


def transcript_title(segments, limit=38):
    parts = []
    for segment in segments or []:
        text = re.sub(r'^【[^】]+】\s*', '', str(segment.get('text') or '')).strip()
        text = re.sub(r'\s+', ' ', text)
        if text:
            parts.append(text)
        if len('，'.join(parts)) >= limit:
            break
    value = '，'.join(parts).strip('，。！？,.!? ')
    if len(value) > limit:
        value = value[:limit].rstrip('，。！？,.!? ') + '…'
    return value or '未命名逐字稿'


def fallback_title(segments):
    """A useful label that is visibly different from platform metadata."""
    return '【自动生成】' + transcript_title(segments)
