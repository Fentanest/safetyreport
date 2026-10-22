"""Validated, durable server guidance shared by status and ACK paths.

status 응답의 `client_notice` 가 정본이다(null/없음이면 안내를 지우고 426 표시도 푼다). 이 앱 버전이 min_version.pc 이상이면
보이지 않는다. 서버 문구가 없을 때(426 만 받은 경우)에는 버전을 넣지 않은 일반 문구만 쓴다.
"""
import json
import re
from urllib.parse import urlsplit

GENERIC_MESSAGE = '프로그램을 최신 버전으로 업데이트해 주세요.'
_VERSION = re.compile(r'^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?')


def valid(value):
    if not isinstance(value, dict) or value.get('level') not in ('info', 'warning', 'required'):return False
    if not isinstance(value.get('message'), str) or not 0 < len(value['message']) <= 1000:return False
    for group in ('min_version', 'url'):
        if not isinstance(value.get(group), dict):return False
        for client in ('pc', 'mobile'):
            v = value[group].get(client)
            if v is not None and (not isinstance(v, str) or not v):return False
            if group == 'url' and v is not None and (urlsplit(v).scheme != 'https' or not urlsplit(v).netloc):return False
    return value.get('deadline') is None or isinstance(value['deadline'], str)


def parse_version(text):
    """'3.6.0' -> (3, 6, 0). 숫자로 시작하지 않으면 None."""
    match = _VERSION.match(text) if isinstance(text, str) else None
    return tuple(int(g or 0) for g in match.groups()) if match else None


def app_version():
    try:
        from core.utils.path_utils import resource_path
        with open(resource_path('VERSION'), 'r', encoding='utf-8') as fh:
            return fh.read().strip()
    except OSError:
        return ''


def outdated(value, version=None):
    """이 앱(PC) 버전이 min_version.pc 보다 낮은가. 어느 쪽이든 읽을 수 없으면 False(보이지 않음)."""
    minimum = parse_version(((value or {}).get('min_version') or {}).get('pc'))
    current = parse_version(app_version() if version is None else version)
    return bool(minimum and current and current < minimum)


def save(store, value):
    """안내 저장. None 이면 지운다. 형식이 틀린 값은 무시한다(기존 안내를 지우지 않는다)."""
    with store.transaction() as tx:
        if value is None:tx.execute("DELETE FROM meta WHERE key='client_notice'")
        elif valid(value):store.set_meta('client_notice', json.dumps(value, ensure_ascii=False), tx)


def mark_upgrade_required(store):
    """HTTP 426. 표시는 이 앱 버전에서만 유효하다 — 업데이트 뒤(버전이 바뀐 뒤)에는 저절로 풀리고 행은 다시 보낸다."""
    store.set_meta('client_upgrade_required', app_version() or 'unknown')


def clear_upgrade(store):
    """426 표시를 풀고, 그 때문에 멈춘 행을 다시 보낼 수 있게 한다(영구 blocked 금지)."""
    with store.transaction() as tx:
        tx.execute("DELETE FROM meta WHERE key='client_upgrade_required'")
        tx.execute("UPDATE outbox SET state='pending', next_retry_at=NULL, last_error_code=NULL, lease_owner=NULL, lease_until=NULL"
                   " WHERE last_error_code='client_upgrade_required' AND state IN ('blocked','retry_wait')")
        tx.execute("UPDATE source_journal SET blocked_reason=NULL WHERE blocked_reason='blocked:client_upgrade_required'")


def apply_status(store, status):
    """status 응답이 정본: client_notice 가 없거나 null 이면 안내와 426 표시를 모두 푼다."""
    notice = status.get('client_notice') if isinstance(status, dict) else None
    if notice is not None and not valid(notice):
        return  # 형식이 틀린 안내는 무시 — 기존 상태를 그대로 둔다
    save(store, notice)
    if notice is None:clear_upgrade(store)


def apply_ack(store, present, notice):
    """성공 ACK = 서버가 이 버전을 받았다 → 426 표시 해제. 응답에 client_notice 키가 있으면 같이 갱신한다."""
    clear_upgrade(store)
    if present and (notice is None or valid(notice)):save(store, notice)


def upgrade_required(store):
    flag = store.meta('client_upgrade_required')
    if flag is None:return False
    if flag != (app_version() or 'unknown'):
        clear_upgrade(store)  # 다른 버전에서 받은 426 — 업데이트 뒤이므로 풀고 다시 시도한다
        return False
    return True


def read(store):
    """지금 보여 줄 안내 또는 None."""
    try:value = json.loads(store.meta('client_notice') or 'null')
    except ValueError:value = None
    if upgrade_required(store):
        base = value if valid(value) else dict(message=GENERIC_MESSAGE, min_version=dict(pc=None, mobile=None),
                                                url=dict(pc=None, mobile=None), deadline=None)
        return dict(base, level='required')
    if valid(value) and outdated(value):return value
    return None
