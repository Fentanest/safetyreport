"""Explicit fine *amount syntax*, independent of disposition and minimum estimates.

Returns a won number token for existing number/typo handling. Ambiguous totals,
conditional amounts and general limits are not converted to a stated amount.
"""
import re

_TRANSLATE = str.maketrans('０１２３４５６７８９，．：（）', '0123456789,.:()')
_FINE = re.compile(r'과태료\s*:?\s*(\()?\s*([0-9,.]+)\s*(만)?\s*원')
_MONEY = re.compile(r'(?<![0-9,.])([0-9,.]+)\s*(만|천|백|억)?\s*원')
_LIMIT = re.compile(r'최대|상한|이하|이상|미만|초과|(?:미이행|불이행|불응)\s*(?:시|할\s*경우)|재위반|않을\s*(?:경우|시)|경우에?는?|부과하지|부과할\s*수\s*없')


def _number(token, unit):
    if not unit:
        return token  # Preserve broken grouping for the existing legal-candidate repair.
    if unit == '만' and re.fullmatch(r'[0-9]+', token):
        return str(int(token) * 10000)
    return None  # Decimal/mixed Korean units require an explicit supported grammar.


def explicit_fine_number(text):
    """Unique explicitly connected amount token, or None. Never infers disposition."""
    text = str(text or '').translate(_TRANSLATE)
    matches = list(_FINE.finditer(text))
    if not matches:
        return None
    candidates = []
    for match in matches:
        if match[1] and not re.match(r'\s*\)', text[match.end():]):
            return None
        # A paragraph/sentence may qualify the number; do not use generic policy
        # footers on later lines to negate an explicit amount in an earlier line.
        start = max(text.rfind('\n', 0, match.start()), text.rfind('。', 0, match.start())) + 1
        finish = text.find('\n', match.end())
        context = text[start:finish if finish >= 0 else len(text)]
        previous = text[:max(0, start - 1)].split('\n')[-1] if start else ''
        if (_LIMIT.search(context)
                or re.search(r'(?:최대|상한|(?:미이행|불이행|불응)\s*시|않을\s*경우)\s*[:：]?\s*$', previous)
                or re.match(r'\s*\)?\s*(?:이하|이상|미만|초과)', text[match.end():])):
            return None
        value = _number(match[2], match[3])
        if value is None:
            return None
        candidates.append(value)
    if len(set(candidates)) != 1:
        return None
    # Other monetary values (e.g. before/after reduction) make the statement
    # ambiguous, even when the second amount does not repeat the fine label.
    money = [_number(m[1], m[2]) for m in _MONEY.finditer(text)]
    identity = lambda n: re.sub(r'[,.]', '', n) if n is not None else None
    if any(identity(n) != identity(candidates[0]) for n in money):
        return None
    return candidates[0]
