"""Versioned mobile predicates, evaluated by SQLite before COUNT/LIMIT.

Only referenced columns enter the exact UDF, never full report objects. No count
cache: every count/page shares one read snapshot, including dedupe and settings.
Do not reuse stats' casefold/sql_candidates: the Dart list uses lower(), not casefold.
"""
from functools import lru_cache
import re
from sqlalchemy import func
from services import report_policy
from services.law_values import matches_laws
from services.report_filter_spec import RANGE_FIELDS, range_key
from services.agency_registry import resolve_display_agency

VERSION = 1
CAPABILITY = 'report_search_v1'
TEXT_FIELDS = {
    'name': '신고명', 'reportNumber': '신고번호', 'id': 'ID',
    'ratingCause': '별점사유', 'agency': '처리기관', 'manager': '담당자',
    'carNumber': '차량번호', 'location': '위반장소', 'fine': '범칙금_과태료',
    'supplementCount': '보완횟수', 'reportContent': '신고내용', 'processContent': '처리내용',
}
STRING_KEYS = {*TEXT_FIELDS, 'law', 'pollStatus',
               *(prefix + suffix for prefix, *_ in RANGE_FIELDS for suffix in ('Start', 'End'))}
BOOL_KEYS = {'excludePolice', 'onlyPolice'}
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
                     str(int(row.get(column) or 0)) if key == 'supplementCount' else row.get(column))
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
        add(['위반법규'], lambda r: matches_laws(r['위반법규'], '__없음__' if f['law'] == '__없음__' else law))
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
