"""Subtitle conversion shared by the worker, exports and tests."""
import html
import csv
import io
import json
import re
import webvtt


def clean_text(text):
    return html.unescape(re.sub(r'<[^>]*>', '', text)).strip()


def normalize(cues):
    result = []
    for cue in cues:
        text = clean_text(cue['text'])
        start, end = max(0, float(cue['start'])), float(cue['end'])
        if not text or end <= start:
            continue
        # Remove repeated rolling caption lines only when their times overlap.
        if result and start < result[-1]['end']:
            previous = result[-1]['text']
            if text == previous:
                result[-1]['end'] = max(end, result[-1]['end'])
                continue
            lines = text.splitlines()
            old_lines = previous.splitlines()
            while lines and lines[0] in old_lines:
                lines.pop(0)
            text = '\n'.join(lines).strip()
        if text:
            result.append({'start': round(start, 3), 'end': round(end, 3), 'text': text})
    return result


def parse_subtitles(raw, ext):
    if ext == 'json3':
        cues = []
        for event in json.loads(raw).get('events', []):
            if 'segs' not in event:
                continue
            start = event.get('tStartMs', 0) / 1000
            cues.append({'start': start, 'end': start + event.get('dDurationMs', 0) / 1000,
                         'text': ''.join(s.get('utf8', '') for s in event['segs'])})
        return normalize(cues)
    reader = webvtt.from_buffer(io.StringIO(raw), format='srt' if ext == 'srt' else 'vtt')
    return normalize([{'start': c.start_in_seconds, 'end': c.end_in_seconds, 'text': c.text} for c in reader])


def timestamp(seconds):
    millis = max(0, round(float(seconds) * 1000))
    hours, millis = divmod(millis, 3600000)
    minutes, millis = divmod(millis, 60000)
    secs, millis = divmod(millis, 1000)
    return f'{hours:02}:{minutes:02}:{secs:02},{millis:03}'


def to_srt(segments):
    return '\n\n'.join(f"{i}\n{timestamp(s['start'])} --> {timestamp(s['end'])}\n{s['text']}"
                       for i, s in enumerate(segments, 1)) + '\n'


def to_csv(segments, title='', url=''):
    output = io.StringIO(newline='')
    writer = csv.writer(output)
    writer.writerow(['视频标题', '视频链接', '序号', '开始时间', '结束时间', '逐字内容'])
    for i, segment in enumerate(segments, 1):
        writer.writerow([title, url, i, timestamp(segment['start']).replace(',', '.'),
                         timestamp(segment['end']).replace(',', '.'), segment['text']])
    return output.getvalue()


def extract_urls(text):
    # Handles pasted share text, Markdown links, and adjacent https:// URLs.
    found = re.findall(r'https?://.*?(?=https?://|[\s<>\[\]()\"\u3000]|$)', text.strip())
    return list(dict.fromkeys(u.rstrip('.,;!，。；！') for u in found))
