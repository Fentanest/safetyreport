"""Backward-compatible readers for scalar and newline-delimited law TEXT."""
from functools import lru_cache
import re
import unicodedata
from services.law_citations import ALIASES, decode_entities

_TRIM_CHARS = " \t\n\x0b\x0c\r\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"
_LINES = re.compile(r'\r\n?|\n|\u2028|\u2029')
def _trim(value):
    return ('' if value is None else str(value)).strip(_TRIM_CHARS)

# ASCII digits only: Dart/JS RegExp \d is ASCII, Python's str \d is Unicode.
_KEY = re.compile(r'(.+?)제?([0-9]+)조(의[0-9]+)?(?:제?([0-9]+)항)?(?:제?([0-9]+)호(의[0-9]+)?)?(?:([가-하])목)?')
# A law name never contains another article or a list separator. Such text
# (e.g. '도로교통법 제32조, 제33조', '도로교통법 제32조 및 제33조') is not one
# structured key; it stays one raw key with its original spacing.
_BAD_NAME = re.compile(r'[,&]|[0-9]조')
_QUOTES = re.compile(r'''[「」『』【】\[\]“”‘’"']''')
_NOTES = re.compile(r'\((?:약칭|이하같음|경찰청지침)\)')
_SPACE = re.compile(r'[\s\ufeff·ㆍ･・ᆞ]')


def _decode(value) -> str:
    """HTML5 references with a required ';' (Dart html / browser rule).

    Shared with law_citations so numeric references are bounded before int()
    and decoded exactly like the parser's recognition copy and Dart.
    """
    return decode_entities(str(value or ''))


def _compact(value):
    return _SPACE.sub('', unicodedata.normalize('NFKC', _decode(value)))


def _structured(compact_text: str):
    """Full (backtracking) match of one article key, or None. Same rule in Dart/JS."""
    match = _KEY.fullmatch(compact_text)
    if not match:
        return None
    name = match.group(1)
    if name == '제' or re.fullmatch('[0-9]+', name) or _BAD_NAME.search(name):
        return None
    return match


def _law_name(name: str) -> str:
    name = _NOTES.sub('', _QUOTES.sub('', name))
    suffix = next((s for s in ('시행규칙', '시행령') if name.endswith(s)), '')
    base = name[:-len(suffix)] if suffix else name
    base = _NOTES.sub('', _QUOTES.sub('', base))
    if not base:
        return ''
    return ALIASES.get(base, base) + (' ' + suffix if suffix else '')


@lru_cache(maxsize=4096)
def canonical_law(value: str) -> str:
    raw = _trim(value)
    match = _structured(_compact(raw))
    if not match:
        return raw
    name, article, sub, paragraph, item, subitem, letter = match.groups()
    name = _law_name(name)
    if not name:
        return raw
    return (f'{name} 제{article}조' + (sub or '')
            + (f'제{paragraph}항' if paragraph else '')
            + (f'제{item}호' if item else '') + (subitem or '') + (letter + '목' if letter else ''))

@lru_cache(maxsize=8192)
def read_laws(value: str | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(canonical_law(line) for line in _LINES.split('' if value is None else str(value)) if _trim(line)))

def primary_law(value) -> str:
    return next((_trim(line) for line in _LINES.split('' if value is None else str(value)) if _trim(line)), '')

def supplement_law(answer: str, legacy: str) -> str:
    return parse_law_values(answer, legacy)


def _term_matches(keys: tuple[str, ...], term: str, *, partial: bool) -> bool:
    if _trim(term) in keys:
        return True
    article_query = re.fullmatch(r'제?([0-9]+)조(의[0-9]+)?(?:제?([0-9]+)항)?(?:제?([0-9]+)호(의[0-9]+)?)?(?:([가-하])목)?', _compact(term))
    if article_query:
        a, sub, p, i, si, letter = article_query.groups()
        tail = f'제{a}조' + (sub or '') + (f'제{p}항' if p else '') + (f'제{i}호' if i else '') + (si or '') + (letter + '목' if letter else '')
        return any(parent.split(' ', 1)[-1] == tail or parent.endswith(' ' + tail)
                   for key in keys for parent in ancestor_laws(key))
    query = canonical_law(term)
    compact_query = _compact(query)
    structured = _structured(compact_query) is not None
    name_query = _QUOTES.sub('', compact_query)
    suffix = next((s for s in ('시행규칙', '시행령') if name_query.endswith(s)), '')
    base_name = name_query[:-len(suffix)] if suffix else name_query
    name = (ALIASES[base_name] + (' ' + suffix if suffix else '')) if base_name in ALIASES else None
    for key in keys:
        if key == query:
            return True
        # Descendants of the same article/paragraph are included; an article
        # inserted with '의' is a separate article, not a string prefix match.
        if structured and query in ancestor_laws(key):
            return True
        if name and key.startswith(name + ' 제'):
            return True
        if partial and not structured and not name and compact_query.lower() in _compact(key).lower():
            return True
    return False


def matches_laws(value, query, *, partial: bool = False) -> bool:
    return _matches_law_text('' if value is None else str(value), '' if query is None else str(query), partial)

@lru_cache(maxsize=16384)
def _matches_law_text(value: str, query: str, partial: bool) -> bool:
    text = _trim(query)
    if not text:
        return True
    keys = read_laws(value)
    if text == '__없음__':
        return not keys
    # A query equal to one stored key matches it as a whole, even if that raw
    # key itself contains ',' or '&' (e.g. a user-edited '제32조, 제33조').
    if text in keys:
        return True
    # Legacy clients may send the whole displayed multiline field.
    if _LINES.search(text):
        return all(_term_matches(keys, term, partial=partial) for term in read_laws(text))
    groups = [[_trim(t) for t in group.split('&') if _trim(t)] for group in text.split(',')]
    groups = [group for group in groups if group]
    return not groups or any(all(_term_matches(keys, term, partial=partial) for term in group) for group in groups)


def available_laws(values) -> list[str]:
    return sorted({law for value in values for law in read_laws(value)})


def _selected_keys(result, legacy_key):
    selected = result['selected']
    if not selected:
        return []
    refs = result['refs']
    primary = next((r for r in refs if r['key'] == selected), None)
    if primary is None:
        return [selected]
    rank = primary['rank']
    # Keep all validated bases of the same highest evidence class. A later
    # remedy or general procedure must not replace a confirmed violation.
    candidates = [r for r in refs if r['kind'] == 'article' and r['rank'] == rank]
    if rank == 3:
        # A legacy parser can establish a shared citation list such as
        # '제32조, 제33조, 제34조 위반' without a fine-decision sentence.
        groups = {e['group'] for e in primary['evidence'] if e['role'] == 'unknown'}
        candidates = [r for r in refs if r['kind'] == 'article' and r['decision'] == 'unknown'
                      and any(e['role'] == 'unknown' and e['group'] in groups for e in r['evidence'])]
    return list(dict.fromkeys([selected] + [r['key'] for r in candidates]))


def parse_law_values(answer: str, legacy: str, *, multiple=True) -> str:
    from services.law_selection import choose
    key = canonical_law(legacy)
    result = choose(answer, key)
    selected = result['selected']
    if not selected and legacy:
        old_refs = [r for r in result['refs'] if r['key'] == key or r['key'].startswith(key + '제')]
        if not any(r['decision'] in ('excluded', 'procedure', 'unresolved', 'conflict', 'general') for r in old_refs):
            return legacy
    keys = _selected_keys(result, key) if multiple else ([selected] if selected else [])
    if len(keys) == 1 and keys[0] == key:
        return legacy
    return '\n'.join(keys)


def ancestor_laws(key: str) -> tuple[str, ...]:
    match = _structured(_compact(key))
    if not match or ' 제' not in key:
        return (key,)
    _, article, sub, paragraph, item, subitem, _ = match.groups()
    root = key.split(' 제', 1)[0] + f' 제{article}조' + (sub or '')
    values = [key, root]
    if paragraph:
        root += f'제{paragraph}항'
        values.append(root)
    if item:
        values.append(root + f'제{item}호' + (subitem or ''))
    return tuple(dict.fromkeys(values))


def law_memberships(value, universe) -> tuple[str, ...]:
    keys = read_laws(value)
    return tuple(sorted({parent for key in keys for parent in ancestor_laws(key) if parent in universe}))


def matches_law_keys(value, query) -> bool:
    """Stored-key membership: the predicate behind every per-law statistic.

    `query` is one stored key (or several, one per line = AND) exactly as the
    statistics/detail links pass it. A row belongs to key K when one of its keys
    is K or a descendant paragraph/item of K (`ancestor_laws`) — the same rule as
    `law_memberships`, so a law bar's count equals the rows this returns. Unlike
    `matches_laws` there is no ','/'&' splitting and no law-name prefix search.
    '__없음__' selects rows without any law.
    """
    return _matches_law_keys('' if value is None else str(value), '' if query is None else str(query))


@lru_cache(maxsize=16384)
def _matches_law_keys(value: str, query: str) -> bool:
    text = _trim(query)
    if not text:
        return True
    keys = read_laws(value)
    if text == '__없음__':
        return not keys
    wanted = read_laws(text)
    parents = {parent for key in keys for parent in ancestor_laws(key)}
    return all(key in parents for key in wanted)
