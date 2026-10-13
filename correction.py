"""Conservative Chinese ASR corrections using context and title vocabulary."""
import re
from pathlib import Path
from functools import lru_cache

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
    (r'([开养守盘接进到])电', r'\1店'),
    (r'把电(?=[翻换改做])', '把店'),
    (r'电(?=[里内面租员长主铺面])', '店'),
    (r'一粒模型', '盈利模型'),
)

LEXICON_DIR = Path(__file__).resolve().parent / 'lexicons'


@lru_cache(maxsize=1)
def _built_in_terms():
    terms = []
    for path in sorted(LEXICON_DIR.glob('*_zh.txt')):
        for term in path.read_text(encoding='utf-8').splitlines():
            term = term.strip()
            if term and term not in terms:
                terms.append(term)
    return tuple(terms)


def commerce_terms(extra_terms=()):
    terms = list(_built_in_terms())
    for term in extra_terms:
        term = str(term).strip()
        if 1 < len(term) <= 24 and term not in terms:
            terms.insert(0, term)
    return terms


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


def _lexicon_vocabulary(extra_terms=()):
    vocabulary = {}
    for word in commerce_terms(extra_terms):
        if 2 <= len(word) <= 8:
            vocabulary.setdefault((len(word), tuple(lazy_pinyin(word))), word)
    return vocabulary


def correct_chinese_text(text, title='', extra_terms=()):
    corrected = text
    for pattern, replacement in COMMON_ASR_FIXES:
        corrected = re.sub(pattern, replacement, corrected)
    vocabulary = _lexicon_vocabulary(extra_terms)
    vocabulary.update(_title_vocabulary(title))
    if vocabulary:
        for size in range(8, 1, -1):
            pattern = re.compile(rf'[\u4e00-\u9fff]{{{size}}}')
            corrected = pattern.sub(
                lambda match: vocabulary.get((size, tuple(lazy_pinyin(match.group(0)))), match.group(0)),
                corrected,
            )
    return corrected


def correct_segments(segments, title='', extra_terms=()):
    return [{**segment, 'text': correct_chinese_text(segment['text'], title, extra_terms)} for segment in segments]


def whisper_hotwords(title='', extra_terms=(), platform='', limit=72):
    selected = list(extra_terms)
    selected.extend(re.findall(r'[\u4e00-\u9fff]{2,16}', title or ''))
    terms = commerce_terms()
    title_chars = set(re.sub(r'[^\u4e00-\u9fff]', '', title or ''))
    if title_chars:
        selected.extend(sorted(terms, key=lambda term: len(title_chars.intersection(term)), reverse=True)[:80])
    platform_name = ('douyin_official_zh.txt' if platform == 'douyin' else
                     'taobao_tmall_official_zh.txt' if platform in ('taobao', 'tmall') else '')
    platform_file = LEXICON_DIR / platform_name if platform_name else None
    if platform_file and platform_file.is_file():
        selected.extend(platform_file.read_text(encoding='utf-8').splitlines())
    selected.extend(terms)
    # faster-whisper shares a 448-token prompt budget between the previous
    # context, initial prompt and hotwords. Some Chinese characters expand to
    # several tokens, so keep a deliberately small character ceiling.
    return '，'.join(dict.fromkeys(term for term in selected if term))[:limit].rstrip('，')
