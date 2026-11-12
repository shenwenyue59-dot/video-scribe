"""Extract large burned-in Chinese captions from video frames with local OCR."""
from difflib import SequenceMatcher
import re


CHINESE = re.compile(r'[\u4e00-\u9fff]')
SPACE = re.compile(r'\s+')


def _clean(text):
    return SPACE.sub('', str(text or '')).strip('丨|_—-~·•')


def _same_caption(left, right):
    left, right = _clean(left), _clean(right)
    if not left or not right:
        return False
    if left == right or (min(len(left), len(right)) >= 4 and (left in right or right in left)):
        return True
    return SequenceMatcher(None, left, right).ratio() >= 0.82


def select_caption_lines(items, frame_width, frame_height, crop_top=0):
    """Keep visible Chinese captions and label bottom subtitles vs decorative text."""
    selected = {'flower': [], 'bottom': []}
    for box, text, score in items or []:
        text = _clean(text)
        chinese_count = len(CHINESE.findall(text))
        if float(score) < 0.72 or chinese_count < 2:
            continue
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) + crop_top for point in box]
        width, height = max(xs) - min(xs), max(ys) - min(ys)
        center_x, center_y = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        if height < max(22, frame_height * 0.025):
            continue
        if not (frame_width * 0.08 <= center_x <= frame_width * 0.92):
            continue
        if not (frame_height * 0.10 <= center_y <= frame_height * 0.91):
            continue
        if width < frame_width * 0.10 and chinese_count < 4:
            continue
        # Dialogue subtitles normally form a modest line in the lower part of
        # the frame. Larger or higher text is treated as decorative on-screen
        # copy (花字). The label is kept in exports and copied plain text too.
        kind = 'bottom' if center_y >= frame_height * 0.62 and height <= frame_height * 0.09 else 'flower'
        selected[kind].append((min(ys), min(xs), text, float(score)))
    for group in selected.values():
        group.sort()
    if not any(selected.values()):
        return '', 0.0
    parts = []
    if selected['flower']:
        parts.append('【画面花字】' + ''.join(item[2] for item in selected['flower']))
    if selected['bottom']:
        parts.append('【底部字幕】' + ''.join(item[2] for item in selected['bottom']))
    all_items = selected['flower'] + selected['bottom']
    return '｜'.join(parts), sum(item[3] for item in all_items) / len(all_items)


def merge_observations(observations, sample_interval):
    segments = []
    current = None
    for timestamp, text, confidence in observations:
        text = _clean(text)
        if not text:
            if current and timestamp - current['last_seen'] > sample_interval * 1.6:
                current['end'] = min(timestamp, current['last_seen'] + sample_interval)
                segments.append(current)
                current = None
            continue
        if current and _same_caption(current['text'], text):
            current['last_seen'] = timestamp
            current['end'] = timestamp + sample_interval
            current['samples'] += 1
            if confidence > current['confidence'] or len(text) > len(current['text']):
                current['text'], current['confidence'] = text, confidence
            continue
        if current:
            current['end'] = min(timestamp, current['last_seen'] + sample_interval)
            segments.append(current)
        current = {'start': timestamp, 'end': timestamp + sample_interval, 'last_seen': timestamp,
                   'text': text, 'confidence': confidence, 'samples': 1}
    if current:
        segments.append(current)
    result = []
    for item in segments:
        text = item['text']
        # A single two-character observation is commonly a logo or transient UI label.
        if len(CHINESE.findall(text)) <= 2 and item['samples'] == 1:
            continue
        kinds = []
        if '【画面花字】' in text:
            kinds.append('画面花字')
        if '【底部字幕】' in text:
            kinds.append('底部字幕')
        result.append({'start': round(item['start'], 3), 'end': round(item['end'], 3),
                       'caption_type': ' + '.join(kinds), 'text': text})
    return result


def captions_are_usable(segments):
    """Prefer real dialogue subtitles; decorative copy alone is not a transcript."""
    bottom = [item for item in segments if '底部字幕' in item.get('caption_type', '')]
    bottom_chars = sum(len(CHINESE.findall(item.get('text', ''))) for item in bottom)
    if len(bottom) >= 3 and bottom_chars >= 20:
        return True
    # Short lifestyle/product videos may communicate entirely through a few
    # changing on-screen phrases and contain no spoken dialogue at all.
    all_chars = sum(len(CHINESE.findall(item.get('text', ''))) for item in segments)
    return len(segments) >= 3 and all_chars >= 12


def extract_screen_captions(media, progress=None, sample_interval=0.75):
    """Return OCR caption segments, or an empty list when coverage is insufficient."""
    import av
    from rapidocr_onnxruntime import RapidOCR

    container = av.open(str(media))
    stream = container.streams.video[0]
    duration = float(container.duration / 1_000_000) if container.duration else 0.0
    if not duration or duration > 1800:
        container.close()
        return []
    frame_width, frame_height = stream.width, stream.height
    # Scan almost the full clean video frame. Xiaohongshu commonly places its
    # main explanatory text in the upper third rather than at the bottom.
    crop_top, crop_bottom = int(frame_height * 0.08), int(frame_height * 0.92)
    ocr = RapidOCR()
    observations, next_sample, last_progress = [], 0.0, -10
    try:
        for frame in container.decode(video=0):
            timestamp = float(frame.time or 0)
            if timestamp + 0.001 < next_sample:
                continue
            next_sample = timestamp + sample_interval
            image = frame.to_ndarray(format='bgr24')[crop_top:crop_bottom, :]
            items, _ = ocr(image)
            text, confidence = select_caption_lines(items, frame_width, frame_height, crop_top)
            observations.append((timestamp, text, confidence))
            if progress and timestamp - last_progress >= 5:
                last_progress = timestamp
                progress(timestamp, duration)
    finally:
        container.close()
    segments = merge_observations(observations, sample_interval)
    # A few changing bottom lines are enough to avoid a redundant speech pass.
    # Decorative text alone still needs broad coverage before it can represent
    # the video's spoken content.
    if not captions_are_usable(segments):
        return []
    return segments
