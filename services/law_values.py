"""Backward-compatible readers for scalar and newline-delimited law TEXT."""
from functools import lru_cache
import re
import html
import unicodedata
from services.law_citations import ALIASES

_KEY = re.compile(r'(.+?)제?(\d+)조(의\d+)?(?:제?(\d+)항)?(?:제?(\d+)호(의\d+)?)?(?:([가-하])목)?')

def _compact(value):
    return re.sub(r'[\s·ㆍ･・ᆞ]', '', unicodedata.normalize('NFKC', html.unescape(str(value or ''))))

@lru_cache(maxsize=4096)
def canonical_law(value: str) -> str:
    raw = str(value or '').strip()
    text = _compact(raw)
    match = _KEY.fullmatch(text)
    if not match:
        return raw
    name, article, sub, paragraph, item, subitem, letter = match.groups()
    suffix = next((s for s in ('시행규칙', '시행령') if name.endswith(s)), '')
    base = name[:-len(suffix)] if suffix else name
    name = ALIASES.get(base, base) + (' ' + suffix if suffix else '')
    return (f'{name} 제{article}조' + (sub or '')
            + (f'제{paragraph}항' if paragraph else '')
            + (f'제{item}호' if item else '') + (subitem or '') + (letter + '목' if letter else ''))

@lru_cache(maxsize=8192)
def read_laws(value: str | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(canonical_law(line) for line in str(value or '').splitlines() if line.strip()))

def primary_law(value) -> str:
    return next((line.strip() for line in str(value or '').splitlines() if line.strip()), '')

def supplement_law(answer: str, legacy: str) -> str:
    from services.law_selection import choose
    key = canonical_law(legacy)
    result = choose(answer, key)
    selected = result['selected']
    if not selected and legacy:
        old_refs = [r for r in result['refs'] if r['key'] == key or r['key'].startswith(key + '제')]
        if not any(r['decision'] in ('excluded', 'procedure') for r in old_refs):
            return legacy
    return legacy if selected == key else selected
