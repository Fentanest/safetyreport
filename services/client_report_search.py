"""Versioned mobile predicates, evaluated by SQLite before COUNT/LIMIT.

Only referenced columns enter the exact UDF, never full report objects. No count
cache: every count/page shares one read snapshot, including dedupe and settings.
Do not reuse stats' casefold/sql_candidates: the Dart list uses lower(), not casefold.
"""
from functools import lru_cache
import math
import re
from sqlalchemy import func
from services import report_policy
from services.law_values import matches_law_keys, matches_laws
from services.report_filter_spec import RANGE_FIELDS, range_key
from services.agency_registry import resolve_display_agency

VERSION = 1
CAPABILITY = 'report_search_v1'
# 법규 검색 v2(2026-10-16, 하위호환 추가): 저장 법규를 개별 키로 정규화해 판정하고, 검색 필터의
# `lawExact`(저장 키 소속 판정 = 통계 막대·상세 링크)를 받는다. v3.3.0 이하는 저장값 완전 일치만 본다.
LAW_SEARCH_CAPABILITY = 'law_search_v2'
LAW_SEARCH_VERSION = 2
TEXT_FIELDS = {
    'name': '신고명', 'reportNumber': '신고번호', 'id': 'ID',
    'ratingCause': '별점사유', 'agency': '처리기관', 'manager': '담당자',
    'carNumber': '차량번호', 'location': '위반장소', 'fine': '범칙금_과태료',
    'supplementCount': '보완횟수', 'reportContent': '신고내용', 'processContent': '처리내용',
}
STRING_KEYS = {*TEXT_FIELDS, 'law', 'pollStatus',
               *(prefix + suffix for prefix, *_ in RANGE_FIELDS for suffix in ('Start', 'End'))}
BOOL_KEYS = {'excludePolice', 'onlyPolice', 'lawExact'}
LIST_KEYS = {'ratings', 'statuses'}


def validate_filter(value):
    if not isinstance(value, dict) or value.keys() - (STRING_KEYS | BOOL_KEYS | LIST_KEYS):
        raise ValueError('Unknown search filter')
    for key, item in value.items():
        valid = (isinstance(item, str) if key in STRING_KEYS else
                 type(item) is bool if key in BOOL_KEYS else
                 isinstance(item, list) and all(isinstance(v, str) for v in item))
        if not valid:
            raise ValueError(f'Invalid search filter: {key}')
    return value


_DART_WS = report_policy.TRIM_CHARS + '\u0085'
_DART_INT = re.compile(r'([+-]?)(?:0[xX]([0-9a-fA-F]+)|([0-9]+))')
_DART_DOUBLE = re.compile(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?')


def dart_int_or_zero(value) -> int:
    """모바일 `Report.fromJson` 의 `_toIntOrNull(v) ?? 0` 과 같은 정수. 읽을 수 없는 값은 0(예전에는 500)."""
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else 0
    if not isinstance(value, str):
        return 0
    text = value.strip(_DART_WS)
    if not text:
        return 0
    match = _DART_INT.fullmatch(text)
    if match:
        sign, hexa, digits = match.groups()
        number = int(hexa, 16) if hexa else int(digits)
        return -number if sign == '-' else number
    if _DART_DOUBLE.fullmatch(text):
        number = float(text)
        return int(number) if math.isfinite(number) else 0
    return 0


def _lower(value):
    # Dart uses simple Unicode lowercasing (no casefold or contextual final sigma).
    return ''.join(c.lower() if c != '\u0130' else 'i' for c in report_policy.norm(value))


def compile_predicate(filters):
    """Return minimal column names + exact row predicate (also used by vectors)."""
    f = validate_filter(filters)
    columns, checks = [], []

    def add(names, check):
        for name in names:
            if name not in columns:
                columns.append(name)
        checks.append(check)

    # Bounded per-request registry lookup; never carries results across registry updates.
    @lru_cache(maxsize=512)
    def agency(code, name):
        return resolve_display_agency(code, name)[0] or name or ''

    for key, column in TEXT_FIELDS.items():
        groups = [[_lower(t) for t in g.split('&') if report_policy.norm(t)]
                  for g in f.get(key, '').split(',')]
        groups = [g for g in groups if g]
        if not groups:
            continue
        def check(row, key=key, column=column, groups=groups):
            value = (agency(row.get('처리기관코드'), row.get(column)) if key == 'agency' else
                     str(dart_int_or_zero(row.get(column))) if key == 'supplementCount' else row.get(column))
            text = _lower(value)
            return any(all(t in text for t in group) for group in groups)
        add([column, '처리기관코드'] if key == 'agency' else [column], check)
    if f.get('ratings'):
        add(['별점'], lambda r: ('__none__' if (r['별점'] or 0) <= 0 else str(int(r['별점']))) in f['ratings'])
    if f.get('statuses'):
        add(['처리상태', '상태'], lambda r: report_policy.display_status(
            r['처리상태'] if r['처리상태'] is not None else r['상태']) in f['statuses'])
    if f.get('law'):
        law = '' if f['law'] == '__없음__' else report_policy.norm(f['law'])
        law = '__없음__' if f['law'] == '__없음__' else law
        # lawExact: 통계 막대·상세 링크가 넘긴 저장 키 그대로(서버 통계와 같은 소속 판정). 없으면 검색 의미(쉼표 OR, & AND).
        match = matches_law_keys if f.get('lawExact') else matches_laws
        add(['위반법규'], lambda r: match(r['위반법규'], law))
    if f.get('pollStatus'):
        add(['만족도조사여부'], lambda r: (r['만족도조사여부'] if r['만족도조사여부'] is not None else '답변 대기').strip(report_policy.TRIM_CHARS + '\u0085') == f['pollStatus'])
    for prefix, column, _, time in RANGE_FIELDS:
        start, end = f.get(prefix + 'Start', ''), f.get(prefix + 'End', '')
        if start or end:
            def check(row, column=column, start=start, end=end, time=time):
                raw = row[column] or ''
                # Dart RegExp \d is ASCII; Python's default \d is Unicode.
                pattern = r'^[0-9]{2}:[0-9]{2}' if time else r'^[0-9]{4}-[0-9]{2}-[0-9]{2}'
                key = range_key(raw, time) if re.match(pattern, raw) else None
                return key is not None and (not start or start <= key) and (not end or key <= end)
            add([column], check)
    if f.get('excludePolice') or f.get('onlyPolice'):
        def check(row):
            police = '경찰' in agency(row['처리기관코드'], row['처리기관'])
            return not (f.get('excludePolice') and police or f.get('onlyPolice') and not police)
        add(['처리기관', '처리기관코드'], check)
    def matches(*values):
        row = dict(zip(columns, values))
        return int(all(check(row) for check in checks))
    return columns, matches


def install_predicate(conn, table, filters):
    columns, predicate = compile_predicate(filters)
    conn.connection.driver_connection.create_function('sr_client_search', -1, predicate, deterministic=True)
    return func.sr_client_search(*(table.c[name] for name in columns)) == 1
