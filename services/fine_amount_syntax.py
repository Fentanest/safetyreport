"""Explicit fine *amount syntax*, independent of disposition and minimum estimates.

Returns a won number token for existing number/typo handling. Ambiguous totals,
conditional amounts and general limits are not converted to a stated amount.
Keep lib/services/fine_amount_syntax.dart and contracts/explicit-fine-vectors.json
aligned; every character class is explicit so Python and Dart agree.
"""
import re

_TRANSLATE = str.maketrans('０１２３４５６７８９，．：（）', '0123456789,.:()')
# Union of Python and Dart whitespace (incl. U+001C-U+001F, U+0085, U+FEFF).
_SPACE_SET = '\\t\\n\\x0b\\x0c\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff'
_S = '[' + _SPACE_SET + ']'
# Followed by whitespace, a comma or the end of text.
_END = '(?![^,' + _SPACE_SET + '])'
_MAX_DIGITS = 15  # Longer digit runs are not amounts (and never reach int()).
_FINE = re.compile('과태료' + _S + '*:?' + _S + '*(\\()?' + _S + '*([0-9,.]+)' + _S + '*(만)?' + _S + '*원')
_MONEY = re.compile('(?<![0-9,.])([0-9,.]+)' + _S + '*(만|천|백|억)?' + _S + '*원')
_LIMIT = re.compile(
    '최대|상한|이하|이상|미만|초과|(?:미이행|불이행|불응)' + _S + '*(?:시|할' + _S + '*경우)|재위반|않을' + _S
    + '*(?:경우|시)|경우에?는?|부과하지|부과할' + _S + '*수' + _S + '*없'
    # Future conditions ("다시 적발될 시", "적발되면", "~할 시", "~하면").
    + '|(?:될|할|적발|위반)' + _S + '*시(?:에는?|에도)?' + _END + '|(?:하|되|않으)면' + _END)
_PREVIOUS_LIMIT = re.compile('(?:최대|상한|(?:미이행|불이행|불응)' + _S + '*시|않을' + _S + '*경우)' + _S + '*[:：]?' + _S + '*$')
_FOLLOWING_LIMIT = re.compile(_S + '*\\)?' + _S + '*(?:이하|이상|미만|초과)')
_CLOSE = re.compile(_S + '*\\)')
# Sentence ends and post-predicate clause connectives ("부과할 예정이며, ...").
_SENTENCE = re.compile('\\n|。|(?<=[다요])[.!?]')
_CLAUSE = re.compile('(?:며|으나|지만|는데|(?:였|했|하|되)고)(?=,|' + _S + ')')


def _number(token, unit):
    if len(re.sub('[^0-9]', '', token)) > _MAX_DIGITS:
        return None
    if not unit:
        return token  # Preserve broken grouping for the existing legal-candidate repair.
    if unit == '만' and re.fullmatch('[0-9]+', token):
        return str(int(token) * 10000)
    return None  # Decimal/mixed Korean units require an explicit supported grammar.


def _identity(n):
    return re.sub('[,.]', '', n) if n is not None else None


def explicit_fine_number(text):
    """Unique explicitly connected amount token, or None. Never infers disposition.

    A condition/limit qualifies only the clause that holds the amount. Generic
    guidance in another clause, sentence or line (a limit footer, a reduction
    notice) neither negates an explicit amount nor counts as a second amount.
    """
    text = str(text or '').translate(_TRANSLATE)
    matches = list(_FINE.finditer(text))
    if not matches:
        return None
    cuts = sorted({0, len(text)} | {m.end() for m in _SENTENCE.finditer(text)}
                  | {m.end() for m in _CLAUSE.finditer(text)})

    def clause(start, end):
        a = max(c for c in cuts if c <= start)
        b = min(c for c in cuts if c >= end)
        return a, b

    candidates = []
    skipped = []
    for match in matches:
        if match[1] and not _CLOSE.match(text, match.end()):
            return None
        a, b = clause(match.start(), match.end())
        before = text[:a]
        if before.endswith('\n'):
            before = before[:-1]
        previous = before.split('\n')[-1] if a else ''
        if (_LIMIT.search(text[a:b]) or _PREVIOUS_LIMIT.search(previous)
                or _FOLLOWING_LIMIT.match(text, match.end())):
            skipped.append(match.span())
            continue
        value = _number(match[2], match[3])
        if value is None:
            return None
        candidates.append(value)
    if len(set(candidates)) != 1:
        return None
    # Other monetary values (e.g. before/after reduction) make the statement
    # ambiguous, even when the second amount does not repeat the fine label.
    for money in _MONEY.finditer(text):
        if _identity(_number(money[1], money[2])) == _identity(candidates[0]):
            continue
        if any(x <= money.start() < y for x, y in skipped):
            continue
        a, b = clause(money.start(), money.end())
        if _LIMIT.search(text[a:b]):
            continue
        return None
    return candidates[0]
