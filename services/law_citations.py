"""Law citation recognition. Source text and offsets are never rewritten.

No disposition, fine amount, database, or network dependency. Unbound citations
remain diagnostic data; they are never assigned to a nearby statute by guessing.
"""
import re
import html
import unicodedata
ALIASES = {'장애인등편의법': '장애인·노인·임산부 등의 편의증진 보장에 관한 법률', '장애인등편의증진보장에관한법': '장애인·노인·임산부 등의 편의증진 보장에 관한 법률', '장애인노인임산부등의편의증진보장에관한법률': '장애인·노인·임산부 등의 편의증진 보장에 관한 법률', '환경친화적자동차의개발및보급촉진에관한법률': '환경친화적 자동차의 개발 및 보급 촉진에 관한 법률', '도로교통법': '도로교통법', '장애인복지법': '장애인복지법', '자동차관리법': '자동차관리법', '질서위반행위규제법': '질서위반행위규제법', '행정절차법': '행정절차법', '국세징수법': '국세징수법', '폐기물관리법': '폐기물관리법', '자동차손해배상보장법': '자동차손해배상보장법', '건설기계관리법': '건설기계관리법', '옥외광고물등의관리와옥외광고산업진흥에관한법률': '옥외광고물등의관리와옥외광고산업진흥에관한법률', '소음진동관리법': '소음진동관리법', '소음·진동관리법': '소음·진동관리법', '도로법': '도로법', '장애인등의편의증진보장에관한법': '장애인·노인·임산부 등의 편의증진 보장에 관한 법률', '장애인노인임산부등의편의증진보장에관한법': '장애인·노인·임산부 등의 편의증진 보장에 관한 법률', '교통약자이동편의증진법': '교통약자이동편의증진법', '친환경자동차법': '환경친화적 자동차의 개발 및 보급 촉진에 관한 법률', '형법': '형법', '교통단속처리지침': '교통단속 처리지침', '자동차등록번호판등의기준에관한고시': '자동차 등록번호판 등의 기준에 관한 고시', '고양시폐기물관리에관한조례': '고양시 폐기물관리에 관한 조례', '부천시폐기물관리에관한조례': '부천시 폐기물관리에 관한 조례', '자동차튜닝에관한규정': '자동차 튜닝에 관한 규정', '2025년경찰청공익신고접수기준표': '2025년 경찰청 공익신고 접수기준표'}
names_re = re.compile('|'.join(sorted(map(re.escape, ALIASES), key=len, reverse=True)))
aliases = ALIASES
# Shared with contracts/law-recognition.json and lib/services/law_recognition_data.dart.
# Digits are ASCII only ([0-9]) so Python and Dart agree on non-ASCII digits.
# The optional single space before 항/호 is the digit boundary kept by
# normalized_law_copy (e.g. '제32조의2 1항' must not become '제32조의21').
ARTICLE_PATTERN = '제?(?P<a>[0-9]+)조(?P<sub>의[0-9]+)?(?:\\([^()]*\\))?(?: ?제?(?P<p>[0-9]+)항)?(?: ?제?(?P<i>[0-9]+)호(?P<isub>의[0-9]+)?)?(?:[\\(「]?(?P<l>[가-하])[\\)」]?목)?'
SHORT_PATTERN = '제?(?P<p>[0-9]+)항(?:제?(?P<i>[0-9]+)호)?|제?(?P<i_only>[0-9]+)호(?P<isub>의[0-9]+)?'
article = re.compile(ARTICLE_PATTERN)
short = re.compile(SHORT_PATTERN)
annex = re.compile('별표 ?(?:제)?(?P<n>[0-9]+)')
reference = re.compile('(같은법|동법|(?<![가-힣])법)(시행령|시행규칙)?[」』】\\]"”’\\\']*')
quotes = re.compile('[「」『』【】\\[\\]"“”’‘\\\']*')
connector = re.compile('(?:및|,|、|와|과|또는)+')

# Explicit whitespace set shared with Dart (Python str.isspace/\s and Dart \s
# differ on U+001C-U+001F, U+0085 and U+FEFF; this is their union).
SPACE_CHARS = '\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \x85\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'
SPACE_CLASS = '[\\t\\n\\x0b\\x0c\\r\\x1c-\\x20\\x85\\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff]'
_DOTS = '\xb7\u318d\uff65\u30fb\u119e'
_DIGITS = '0123456789'
ENTITY = re.compile('&(?:#[xX][0-9a-fA-F]+|#[0-9]+|[a-zA-Z]+);')
_TOKEN = re.compile('&(?:#[xX][0-9a-fA-F]+|#[0-9]+|[a-zA-Z]+);|.', re.S)
# HTML5 numeric character reference replacements (same table as the Dart html
# package tokenizer and browsers).
_NUMERIC_REPLACEMENTS = {0x00: '�', 0x0d: '\r', 0x80: '€', 0x81: '\x81', 0x82: '‚', 0x83: 'ƒ', 0x84: '„', 0x85: '…', 0x86: '†', 0x87: '‡', 0x88: 'ˆ', 0x89: '‰', 0x8a: 'Š', 0x8b: '‹', 0x8c: 'Œ', 0x8d: '\x8d', 0x8e: 'Ž', 0x8f: '\x8f', 0x90: '\x90', 0x91: '‘', 0x92: '’', 0x93: '“', 0x94: '”', 0x95: '•', 0x96: '–', 0x97: '—', 0x98: '˜', 0x99: '™', 0x9a: 'š', 0x9b: '›', 0x9c: 'œ', 0x9d: '\x9d', 0x9e: 'ž', 0x9f: 'Ÿ'}


_BLOCK = re.compile('.+?(?=\\n' + SPACE_CLASS + '*\\n|\\Z)', re.S)
_INHERITED_PARAGRAPH = re.compile('제' + SPACE_CLASS + '*([0-9]+)' + SPACE_CLASS + '*항(?=에)')
_SENTENCE_GAP = re.compile('[。!?]|[다요]\\.|\\n' + SPACE_CLASS + '*\\n')


def decode_entity(token):
    """Decode one ``&...;`` token like the Dart html parser and a browser textarea.

    The token grammar requires a semicolon, so legacy forms such as ``&nbsp``
    stay literal. Numeric references use the HTML5 table; surrogates and values
    above U+10FFFF become U+FFFD; other control/noncharacter code points are
    kept (``html.unescape`` would drop them). Long digit runs are bounded
    before conversion.
    """
    if token.startswith('&#'):
        hexa = token[2:3] in ('x', 'X')
        digits = (token[3:-1] if hexa else token[2:-1]).lstrip('0') or '0'
        if len(digits) > 8:
            return '�'
        number = int(digits, 16 if hexa else 10)
        if number in _NUMERIC_REPLACEMENTS:
            return _NUMERIC_REPLACEMENTS[number]
        if 0xD800 <= number <= 0xDFFF or number > 0x10FFFF:
            return '�'
        return chr(number)
    # Named reference with ';': html.unescape applies the same WHATWG table and
    # longest-prefix rule as the Dart html tokenizer.
    return html.unescape(token)


def decode_entities(text):
    return ENTITY.sub(lambda m: decode_entity(m[0]), text)


def normalized_law_copy(s):
    """Whitespace/dot-free recognition copy with a source-offset map.

    A whitespace run between two ASCII digits is kept as one ' ' so a branch
    number and a following paragraph number stay separate ('제32조의2 1항').
    All other whitespace is removed.
    """
    chars, mapping = ([], [])
    gap = None
    for match in _TOKEN.finditer(s):
        token = match[0]
        decoded = decode_entity(token) if len(token) > 1 else token
        for char in unicodedata.normalize('NFKC', decoded):
            if char in SPACE_CHARS:
                if gap is None:
                    gap = (match.start(), match.end())
                continue
            if char in _DOTS:
                continue
            if gap is not None and chars and chars[-1] in _DIGITS and char in _DIGITS:
                chars.append(' ')
                mapping.append(gap)
            gap = None
            chars.append(char)
            mapping.append((match.start(), match.end()))
    return (''.join(chars), mapping)

def parse_laws(s):
    t, mapping = normalized_law_copy(s)
    names = list(names_re.finditer(t))
    refs = []
    covered = []
    pending = []
    plates = [m.span() for m in re.finditer('(?<![0-9])[0-9]{2,3}[가-힣][0-9*]{2,4}', t)]

    def source(a, b):
        return [mapping[a][0], mapping[b - 1][1]]

    def add(law, g, start, finish, name_span, kind='article'):
        key = law + ' 별표 ' + g['n'] if kind == 'annex' else law + ' 제' + g['a'] + '조' + (g['sub'] or '') + ('제' + g['p'] + '항' if g['p'] else '') + ('제' + g['i'] + '호' + (g['isub'] or '') if g['i'] else '') + (g['l'] + '목' if g['l'] else '')
        span = source(start, finish)
        covered.append((start, finish))
        refs.append({'key': key, 'law': law, 'kind': kind, 'structure': dict(g), 'source_span': span, 'source_text': s[slice(*span)], 'name_span': name_span})
    for ni, n in enumerate(names):
        base = aliases[n[0]]
        law = base
        pos = n.end()
        end = names[ni + 1].start() if ni + 1 < len(names) else len(t)
        name_span = source(n.start(), n.end())
        sf = re.match('(시행령|시행규칙)', t[pos:])
        if sf:
            law += ' ' + sf[0]
            pos += len(sf[0])
        for _ in range(2):
            pos += len(quotes.match(t, pos)[0])
            qual = re.match('\\((?:약칭|이하같음|경찰청지침)\\)', t[pos:])
            if qual:
                pos += len(qual[0])
        a = article.match(t, pos) or annex.match(t, pos)
        previous = None
        last_finish = pos
        while True:
            if a and a.end() <= end:
                if a.re is annex:
                    add(law, a.groupdict(), a.start(), a.end(), name_span, 'annex')
                    previous = None
                else:
                    if a.re is article:
                        g = a.groupdict()
                    else:
                        v = a.groupdict()
                        g = dict(previous)
                        if v.get('p'):
                            g.update(p=v['p'], i=v.get('i'), isub=None, l=None)
                        else:
                            g.update(i=v['i_only'], isub=v.get('isub'), l=None)
                    add(law, g, a.start(), a.end(), name_span)
                    previous = g
                pos = a.end()
                last_finish = pos
                if re.match('(?:부터|내지|~|∼|～)', t[pos:]):
                    pending.append({'type': 'range_not_expanded', 'span': source(a.start(), a.end())})
                    break
                sep = connector.match(t, pos)
                if sep:
                    pos = sep.end()
                    direct = article.match(t, pos) or annex.match(t, pos) or (short.match(t, pos) if previous else None)
                    if direct:
                        a = direct
                        continue
            rr = reference.search(t, pos, end)
            if not rr:
                break
            gap = t[last_finish:rr.start()]
            if re.search('[「『][^」』]{2,100}(?:법|법률|조례|고시)[」』]', gap):
                pending.append({'type': 'ambiguous_reference', 'span': source(rr.start(), rr.end())})
                break
            if rr[1] == '법' and len({aliases[x[0]] for x in names}) != 1:
                pos = rr.end()
                a = None
                continue
            law = base + (' ' + rr[2] if rr[2] else '')
            pos = rr.end()
            a = article.match(t, pos) or annex.match(t, pos)
            previous = None
            if not a:
                pos = rr.end()
    for block in _BLOCK.finditer(s):
        seg_start, seg_end = block.span()
        seg = block[0]
        parents = [r for r in refs if r['kind'] == 'article' and seg_start <= r['source_span'][0] < seg_end]
        identities = {(r['law'], r['structure']['a'], r['structure']['sub']) for r in parents}
        if len(identities) != 1:
            continue
        for m in _INHERITED_PARAGRAPH.finditer(seg):
            span = [seg_start + m.start(), seg_start + m.end()]
            if any((r['source_span'][0] <= span[0] < r['source_span'][1] for r in parents)):
                continue
            before = [r for r in parents if r['source_span'][1] <= span[0]]
            if not before:
                continue
            parent = before[-1]
            g = dict(parent['structure'])
            g.update(p=m[1], i=None, isub=None, l=None)
            key = parent['law'] + ' 제' + g['a'] + '조' + (g['sub'] or '') + '제' + g['p'] + '항'
            refs.append({'key': key, 'law': parent['law'], 'kind': 'article', 'structure': g, 'source_span': span, 'source_text': s[slice(*span)], 'name_span': parent['name_span'], 'inherited_article_span': parent['source_span']})
    for m in re.finditer('\\[별표\\]([0-9]+)호([가-하])목', t):
        span = source(m.start(), m.end())
        before = [r for r in refs if r['source_span'][1] <= span[0]]
        if not before:
            continue
        parent = max(before, key=lambda r: r['source_span'][1])
        gap = s[parent['source_span'][1]:span[0]]
        if _SENTENCE_GAP.search(gap):
            continue
        refs.append({'key': parent['law'] + ' 별표 제' + m[1] + '호' + m[2] + '목', 'law': parent['law'], 'kind': 'annex', 'structure': {'n': None, 'i': m[1], 'l': m[2]}, 'source_span': span, 'source_text': s[slice(*span)], 'name_span': parent['name_span']})
    refs.sort(key=lambda r: r['source_span'][0])
    for a in article.finditer(t):
        if any((x <= a.start() < y for x, y in covered)):
            continue
        if any((x <= a.start() < y for x, y in plates)):
            continue
        pending.append({'type': 'unbound_article', 'span': source(a.start(), a.end()), 'text': s[slice(*source(a.start(), a.end()))]})
    unique = {}
    for r in refs:
        if r['key'] not in unique:
            unique[r['key']] = {k: v for k, v in r.items() if k not in ('source_span', 'source_text', 'name_span', 'inherited_article_span')}
            unique[r['key']]['occurrences'] = []
        unique[r['key']]['occurrences'].append({k: r[k] for k in ('source_span', 'source_text', 'name_span', 'inherited_article_span') if k in r})
    return (list(unique.values()), pending)
