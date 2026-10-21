"""v2 immutable raw/supplement snapshots. No network calls or report logging."""
import hashlib
import json


def canonical(value):
    if isinstance(value, dict):
        keys=sorted(value,key=lambda k:k.encode('utf-16-be'))
        return '{'+','.join(json.dumps(k,ensure_ascii=False)+':'+canonical(value[k]) for k in keys)+'}'
    if value is None or isinstance(value,(str,bool)) or type(value) is int:
        return json.dumps(value,ensure_ascii=False,separators=(',',':'))
    raise ValueError('canonical_value')


def sha(value): return hashlib.sha256(canonical(value).encode()).hexdigest()


def v2_ready(status):
    if not isinstance(status,dict): return False
    consent=status.get('consent');ingest=status.get('ingest')
    if not isinstance(consent,dict) or not isinstance(ingest,dict) or not isinstance(ingest.get('contracts'),list): return False
    return (consent.get('state')=='active' and 2 in ingest['contracts']
            and bool(ingest.get('raw_policy_version')) and consent.get('policy_version')==ingest['raw_policy_version'])


def supplement(row):
    def text(k,limit):
        v=row.get(k)
        return v[:limit] if isinstance(v,str) and v else None
    def integer(k,lo,hi):
        v=row.get(k)
        return v if type(v) is int and lo<=v<=hi else None
    return dict(rating=integer('별점',1,5),rating_cause=text('별점사유',10000),survey_state=text('만족도조사여부',80),
        photo_first_at=text('사진_첫촬영',80),photo_last_at=text('사진_끝촬영',80),photo_count=integer('사진_촬영수',0,1000000),
        list=dict(title=text('신고명',1000),report_date=text('신고일',80),status=text('상태',80),entry_value=text('entry_value',200),ssnpc_cd=text('ssnpc_cd',32),ssnpc_type=text('ssnpc_type',32)))


def snapshot(body, fetched_at, source, fields):
    if isinstance(body,bytes):body=body.decode('utf-8')
    data=json.loads(body)
    if not isinstance(data,dict) or not isinstance(data.get('result'),dict):raise ValueError('raw_invalid')
    if any(k in data['result'] for k in ('C_NAME','C_EMAIL','C_PHONE','C_PHONE2','C_ID')):raise ValueError('raw_reporter_keys_present')
    supp=supplement(fields)
    return dict(raw=dict(body=body,sha256=hashlib.sha256(body.encode()).hexdigest(),size=len(body.encode()),fetched_at=fetched_at,source=source),
        supplement=supp,supplement_sha256=sha(supp))


def with_observation(raw, observation):
    return {**raw,'client_observation':observation,'client_observation_sha256':sha(observation)}


def content_key(event):
    return ((event.get('raw') or {}).get('sha256'),event.get('supplement_sha256'),event.get('client_observation_sha256'))

def load_current_snapshot(report_id, *, engine=None, fields=None):
    """Read verified stored bytes; never serialize the decoded original again."""
    from sqlalchemy import select
    from core.database import models
    from core.utils import raw_detail
    if engine is None:
        from core.database.engine import get_engine
        engine=get_engine()
    with engine.connect() as conn:
        record=conn.execute(select(models.report_detail_raw_table).where(models.report_detail_raw_table.c.ID==str(report_id))).mappings().first()
        row=conn.execute(select(models.report_table).where(models.report_table.c.ID==str(report_id))).mappings().first()
    if not record:return None
    try:
        body=raw_detail.verify(record['raw_zlib'],record['raw_sha256'])
        if len(body)!=record['raw_size']:return None
        return snapshot(body,record['fetched_at'],record['source'],{**dict(row or {}),**(fields or {})})
    except (ValueError,UnicodeError,TypeError,KeyError):return None

def snapshot_for_adapter(report_id,adapter):
    fields=dict(adapter.get('_raw_fields') or {})
    for name,key in [('photo_first_at','사진_첫촬영'),('photo_last_at','사진_끝촬영')]:
        if adapter.get(name) is not None:fields[key]=adapter[name]
    return load_current_snapshot(report_id,fields=fields)

def selected(store,status,context,tx=None):
    """Capability is authoritative on every fresh status; rollback to v1 is supported."""
    return bool(context and v2_ready(status) and (status.get('consent') or {}).get('grant_id')==context.get('consent_grant_id'))


def ingest_status(store):
    """저장된 capability 요약. 손상됐으면 빈 값(= v1) — 읽기 실패가 저장·전송을 막지 않는다."""
    try:
        value = json.loads(store.meta('ingest_status') or '{}')
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def compact(raw_event_json):
    """ACK 뒤 journal 에 남기는 비교용 축약본(content_key 와 v1 되돌림 판정에 필요한 해시만)."""
    try:
        event = json.loads(raw_event_json)
        return json.dumps({'compact': True, 'raw': {'sha256': (event.get('raw') or {}).get('sha256')},
                           'supplement_sha256': event.get('supplement_sha256'),
                           'client_observation_sha256': event.get('client_observation_sha256')}, separators=(',', ':'))
    except (TypeError, ValueError, AttributeError):
        return raw_event_json


def cached_status(status):
    """Only capability and consent scope; do not persist account names or remote dataset metadata."""
    consent=status.get('consent') or {}
    ingest=status.get('ingest') or {}
    return {'consent':{k:consent.get(k) for k in ('state','grant_id','policy_version')},
            'ingest':{k:ingest.get(k) for k in ('contracts','raw_policy_version')}}
