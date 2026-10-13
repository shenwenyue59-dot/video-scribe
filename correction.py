"""Conservative Chinese ASR corrections using context and title vocabulary."""
import re

from pypinyin import lazy_pinyin


COMMON_ASR_FIXES = (
    (r'家蒙(?=商|就|费|店|品牌|项目)', '加盟'),
    (r'老家蒙商', '老加盟商'),
    (r'没惊艳', '没经验'),
    (r'友好模型', '友好模式'),
    (r'总不派人', '总部派人'),
    (r'降料配笔', '酱料配比'),
    (r'初删流程', '出餐流程'),
    (r'不用大除', '不用大厨'),
    (r'手把手胶', '手把手教'),
    (r'租金天空', '租金天坑'),
    (r'文文起步', '稳稳起步'),
)


def _title_vocabulary(title):
    phrases = re.findall(r'[\u4e00-\u9fff]{2,16}', title or '')
    vocabulary = {}
    for phrase in phrases:
        for size in range(2, min(8, len(phrase)) + 1):
            for offset in range(len(phrase) - size + 1):
                word = phrase[offset:offset + size]
                key = tuple(lazy_pinyin(word))
                vocabulary.setdefault((size, key), word)
    return vocabulary


def correct_chinese_text(text, title=''):
    corrected = text
    for pattern, replacement in COMMON_ASR_FIXES:
        corrected = re.sub(pattern, replacement, corrected)
    vocabulary = _title_vocabulary(title)
    if vocabulary:
        for size in range(8, 1, -1):
            pattern = re.compile(rf'[\u4e00-\u9fff]{{{size}}}')
            corrected = pattern.sub(
                lambda match: vocabulary.get((size, tuple(lazy_pinyin(match.group(0)))), match.group(0)),
                corrected,
            )
    return corrected


def correct_segments(segments, title=''):
    return [{**segment, 'text': correct_chinese_text(segment['text'], title)} for segment in segments]
