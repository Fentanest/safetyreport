"""Durable cloud cooldown and verified offline authorization, separate from personal DB.

Verified owners may use local data for 72 hours during status outages. Upload
permission remains server-authoritative. Consent transitions are encrypted using
the installation key outside the personal report DB; unknown is not a transition.
All Supabase HTTP boundaries share this lock/deadline (including other processes).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import random
import sqlite3
import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

from services.community_auth_store import _FileLock, atomic_write
from services.community_gate_policy import offline_grace

_now = time.time
_rand = random.random  # 시험에서 결정적으로 고정하는 지점(0.5 → 지터 배율 정확히 1.0)
MIN_RETRY_SECONDS = 300
MAX_RETRY_SECONDS = 1800  # 연속 실패 점증 상한(30분)
JITTER_RATIO = 0.2  # ±20%
_locks = {}
_locks_lock = threading.Lock()


def _consecutive_wait(failures: int) -> float:
    """연속 실패 `failures`(1부터)의 전역 쿨다운 기본값: 300·2^(n-1) 상한 1800초, ±20% 지터(균등).

    여러 PC 가 같은 장애에서 동시에 300초 뒤 다시 몰아치지 않게 한다(2026-10-11 cloud-retry).
    서버 Retry-After 가 더 길면 호출부에서 `max(base, hint)`로 따로 반영한다(이 함수는 지시를 모른다)."""
    failures = max(1, int(failures))
    base = min(MAX_RETRY_SECONDS, MIN_RETRY_SECONDS * (2 ** min(failures - 1, 30)))
    jitter = (1.0 - JITTER_RATIO) + (2 * JITTER_RATIO) * _rand()
    return base * jitter


class CloudDeferred(RuntimeError):
    def __init__(self, until):
        super().__init__('cloud_unavailable')
        self.until = until
        self.retry_after = max(0, until - _now())


def _directory():
    from services import community_auth_service as cas
    return cas.get_service().store.auth_dir


def _scope(base):
    return hashlib.sha256(base.rstrip('/').encode()).hexdigest()


def _identity(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


def _accounts(directory):
    from services import community_auth_service as cas
    path = os.path.join(directory, 'community_consent.enc')
    try:
        with open(path, 'rb') as stream:
            raw = stream.read()
    except FileNotFoundError:
        return {}
    cipher = cas.get_service().store._fernet(create=False)
    if cipher is None:
        raise ValueError('consent key missing')
    try:
        value = json.loads(cipher.decrypt(raw))
    except Exception:
        raise ValueError('consent store unreadable') from None
    if not isinstance(value, dict):
        raise ValueError('consent store invalid')
    return value


def _read_state():
    directory = _directory()
    path = os.path.join(directory, 'community_cloud.db')
    state = {'version': 1, 'cloud': {}}
    if os.path.exists(path):
        conn = sqlite3.connect('file:' + path + '?mode=ro', uri=True, timeout=1)
        try:
            row = conn.execute('SELECT payload FROM cloud_state WHERE id=1').fetchone()
            if row:
                state = json.loads(row[0])
        finally:
            conn.close()
    state['accounts'] = _accounts(directory)
    return state


@contextlib.contextmanager
def _state():
    directory = _directory()
    os.makedirs(directory, mode=0o700, exist_ok=True)
    path = os.path.join(directory, 'community_cloud.db')
    with _locks_lock:
        lock = _locks.setdefault(path, threading.RLock())
    with lock:
        file_lock = _FileLock(path + '.lock')
        file_lock.acquire()
        conn = None
        try:
            conn = sqlite3.connect(path, timeout=30)
            if os.name == 'posix':
                os.chmod(path, 0o600)
            conn.execute("CREATE TABLE IF NOT EXISTS cloud_state (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)")
            conn.commit()
            state = _read_state()
            original = json.dumps(state, sort_keys=True)
            original_accounts = json.dumps(state['accounts'], sort_keys=True)
            yield state
            if json.dumps(state['accounts'], sort_keys=True) != original_accounts:
                from services import community_auth_service as cas
                cipher = cas.get_service().store._fernet(create=True)
                atomic_write(os.path.join(directory, 'community_consent.enc'), cipher.encrypt(json.dumps(state['accounts'], sort_keys=True).encode()))
            if json.dumps(state, sort_keys=True) != original:
                payload = {k: v for k, v in state.items() if k != 'accounts'}
                conn.execute("INSERT INTO cloud_state(id,payload) VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (json.dumps(payload, sort_keys=True),))
                conn.commit()
        finally:
            if conn is not None:
                conn.close()
            file_lock.release()


def retry_after(headers=None, body=None, now=None):
    """Maximum valid body/header instruction, including HTTP dates and server Date."""
    now = _now() if now is None else now
    headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    values = []
    raw = headers.get('retry-after')
    if raw:
        try:
            values.append(float(raw))
        except ValueError:
            try:
                date = parsedate_to_datetime(raw)
                date = date if date.tzinfo else date.replace(tzinfo=timezone.utc)
                base = now
                if headers.get('date'):
                    base_date = parsedate_to_datetime(headers['date'])
                    base_date = base_date if base_date.tzinfo else base_date.replace(tzinfo=timezone.utc)
                    base = base_date.timestamp()
                values.append(date.timestamp() - base)
            except (ValueError, TypeError, OverflowError):
                pass
    err = body.get('error') if isinstance(body, dict) else None
    if isinstance(err, dict):
        for key in ('retryAfterSeconds', 'retry_after_seconds'):
            value = err.get(key)
            if isinstance(value, (float, int)) and not isinstance(value, bool):
                values.append(value)
    return max((v for v in values if math.isfinite(v) and v > 0), default=0)


def view(base=None):
    if base is None:
        from services import community_auth_service as cas
        base = cas.get_service().config().supabase_url
    try:
        row = _read_state()['cloud'].get(_scope(base), {})
        return dict(row)
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        # Never overwrite an unreadable authorization/deadline store.
        return {'next_attempt_at': _now() + MIN_RETRY_SECONDS, 'error': 'state_unreadable'}


def remaining(base=None):
    return max(0, view(base).get('next_attempt_at', 0) - _now())


def run(base, send):
    """Serialize actual HTTP calls; failures persist before any waiting caller runs.

    send returns requests.Response or (status, bytes, headers). No auth/store lock
    is taken here: callers acquire their token before entering this boundary.

    연속 실패마다 쿨다운이 300·2^(n-1)(상한 1800초)로 늘고 ±20% 지터가 섞인다. 성공(비일시 장애 응답)하면
    이 base 의 행을 지워 연속 실패 횟수를 0으로 되돌린다. 서버 Retry-After 가 점증값보다 길면 그 값을 쓴다
    (절대 줄이지 않음, 2026-10-11 cloud-retry).
    """
    deferred_until = None
    maintenance = None
    with _state() as state:
        key = _scope(base)
        row = state['cloud'].get(key, {})
        until = row.get('next_attempt_at', 0)
        if until > _now():
            raise CloudDeferred(until)
        failures = int(row.get('consecutive_failures', 0)) + 1
        try:
            response = send()
        except Exception:
            deferred_until = _now() + _consecutive_wait(failures)
            state['cloud'][key] = {'next_attempt_at': deferred_until, 'error': 'network_error',
                                  'consecutive_failures': failures}
            # Commit before re-raising outside the context manager.
            failed = True
            response = None
        else:
            failed = False
            if isinstance(response, tuple):
                status, raw, headers = response
            else:
                status, raw, headers = response.status_code, response.text, response.headers
            try:
                body = json.loads(raw)
            except (TypeError, ValueError, UnicodeError):
                body = {}
            err = body.get('error') if isinstance(body, dict) else None
            transient = status == 429 or status >= 500 or (isinstance(err, dict) and
                (err.get('retryable') is True or err.get('code') in ('rate_limited', 'busy', 'server_error')))
            if status == 503 and isinstance(err, dict) and err.get('code') == 'maintenance':
                # 업로드 일시 중지(2-9): 서버는 정상이다 — Supabase 전체를 멈추는 전역 쿨다운을 걸지 않는다
                # (status 확인은 계속해야 조기 재개·연장을 본다). 중지 저장·재개 예약은 community_upload_pause 가 맡는다.
                transient = False
                maintenance = (body, headers)
            if transient:
                deferred_until = _now() + max(_consecutive_wait(failures), retry_after(headers, body))
                state['cloud'][key] = {'next_attempt_at': deferred_until,
                                      'error': 'rate_limited' if status == 429 else 'server_unavailable',
                                      'consecutive_failures': failures}
            else:
                state['cloud'].pop(key, None)
    if failed:
        raise CloudDeferred(deferred_until)
    if maintenance is not None:
        try:
            from services import community_upload_pause
            community_upload_pause.note_maintenance(*maintenance)
        except Exception as exc:
            from core.utils.fallback import note_fallback
            note_fallback('community_cloud.maintenance', exc)
    return response


def _account_key(base, user):
    return _identity(base.rstrip('/') + '|' + str(user))


def deny(reason, *, service=None, user=None, sticky=False):
    from services import community_auth_service as cas
    service = service or cas.get_service()
    record = service.store.load()
    current = record.get('current') or record.get('reauth') or {}
    user = user or current.get('user_id')
    if not user:
        return
    with _state() as state:
        row = state['accounts'].setdefault(_account_key(service.config().supabase_url, user), {})
        previous = row.get('deny') or {}
        if previous.get('reason') == 'suspended':
            row['suspended'] = {'at': previous.get('at', _now())}
        if reason == 'suspended':
            row['suspended'] = {'at': _now()}
        row['deny'] = {'reason': reason, 'at': _now(), 'sticky': sticky or bool(previous.get('sticky')), 'grant_id': (row.get('consent') or {}).get('grant_id')}


def denial(service=None, user=None):
    from services import community_auth_service as cas
    service = service or cas.get_service()
    current = service.store.load().get('current') or {}
    user = user or current.get('user_id')
    try:
        state = _read_state()
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return {'reason': 'state_unreadable', 'sticky': True}
    return dict(state['accounts'].get(_account_key(service.config().supabase_url, user), {}).get('deny') or {})


def consent_accepted(service=None):
    """Called only after the explicit consent POST succeeded, never by status."""
    from services import community_auth_service as cas
    service = service or cas.get_service()
    current = service.store.load().get('current') or {}
    with _state() as state:
        row = state['accounts'].setdefault(_account_key(service.config().supabase_url, current.get('user_id')), {})
        row.pop('deny', None)
        row['catchup_requested'] = True


def observe(service, status=None, *, kind=None, source='status', user=None):
    """Only actual state/grant transitions. Transport failure preserves history."""
    current = service.store.load().get('current') or service.store.load().get('reauth') or {}
    consent = (status or {}).get('consent') or {}
    result = kind or consent.get('state') or 'unknown'
    user = user or current.get('user_id')
    if result == 'unknown' or not user:
        return
    account = _account_key(service.config().supabase_url, user)
    with _state() as state:
        row = state['accounts'].setdefault(account, {})
        prior = row.get('consent') or {}
        grant = consent.get('grant_id')
        # First status (including a later grant ID fill-in) is a baseline.
        # Keep a successful explicit consent request until status confirms its
        # grant, even if the intervening status request fails or we restart.
        changed = (prior.get('state') is not None and
                   (prior.get('state') != 'active' or
                    (prior.get('grant_id') is not None and prior['grant_id'] != grant)))
        if (result == 'active' and grant and source == 'status' and
                (row.get('catchup_requested') is True or changed)):
            # lineage_changed: 비활성(철회·미동의·정책 불일치)에서 active 로 돌아온 계보 — v2 는 원본을 새 run 으로 전부 다시 보낸다.
            # active 유지 중 grant 갱신(정책 개정 재동의)은 이미 보낸 원본을 다시 보내지 않는다(최종 검수 4).
            row.setdefault('jobs', {}).setdefault(grant, {'state': 'pending', 'phase': 'reshare', 'created_at': _now(),
                                                          'lineage_changed': prior.get('state') != 'active'})
            row.pop('catchup_requested', None)
        if prior.get('state') == result and (result != 'active' or prior.get('grant_id') in (None, grant)):
            if grant:
                row['consent'] = {**prior, 'grant_id': grant, 'policy_version': consent.get('policy_version')}
            return
        event = {'state': result, 'grant_id': grant, 'policy_version': consent.get('policy_version'),
                 'at': _now(), 'source': source}
        row['consent'] = event
        row.setdefault('history', []).append(event)


def suspension(service):
    """Consent relaxation never waives an authoritative contributor suspension."""
    current = service.store.load().get('current') or {}
    try:
        row = _read_state()['accounts'].get(_account_key(service.config().supabase_url, current.get('user_id')), {})
        return bool(row.get('suspended')) or (row.get('deny') or {}).get('reason') == 'suspended'
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return False  # local_allowed / cloud.view fail closed for unreadable state


_BLOCKING_DENY_REASONS = ('consent_revoked', 'account_removed', 'suspended', 'db_owner_mismatch', 'official_account_mismatch')


def blocking_deny(row):
    """로컬 사용·시드를 막는 거부인가: sticky 이거나 사유가 위 목록일 때만. 로그아웃 같은 non-sticky 표시는 막지 않는다
    (재로그인 뒤에도 남아 실시간 업로드가 조용히 멈추는 결함, 최종 검수 1)."""
    deny = (row or {}).get('deny') or {}
    return bool(deny) and bool(deny.get('sticky') or deny.get('reason') in _BLOCKING_DENY_REASONS)


def _clear_logout_deny(row):
    deny = row.get('deny') or {}
    if deny and not deny.get('sticky') and deny.get('reason') == 'logout':
        row.pop('deny', None)


def logout_confirmed_login(service=None):
    """카카오 로그인 확정 때: 이전 로그아웃이 남긴 non-sticky deny('logout') 를 지운다."""
    from services import community_auth_service as cas
    service = service or cas.get_service()
    user = (service.store.load().get('current') or {}).get('user_id')
    if not user:
        return
    with _state() as state:
        row = state['accounts'].get(_account_key(service.config().supabase_url, user))
        if row:
            _clear_logout_deny(row)


def local_allowed(service, current, owner, dataset_key):
    if not current or not owner or current.get('kakao_id') != owner:
        return False
    try:
        state = _read_state()
        row = state['accounts'].get(_account_key(service.config().supabase_url, current.get('user_id')), {})
        if row.get('suspended') or blocking_deny(row):
            return False
        if (row.get('consent') or {}).get('state') != 'active':
            return False
        if seed_g1(service, current, owner, dataset_key):
            row = _read_state()['accounts'].get(_account_key(service.config().supabase_url, current.get('user_id')), {})
        verified = row.get('last_g1') or {}
        at = verified.get('at')
        if (not isinstance(at, (int, float)) or not offline_grace(at, _now())
                or verified.get('kakao_id') != current.get('kakao_id')
                or verified.get('dataset_key') != dataset_key):
            return False
        bound = row.get('binding')
        return not bound or bound == dataset_key
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return False


def reset_evidence(service, current):
    with _state() as state:
        row=state['accounts'].setdefault(_account_key(service.config().supabase_url,current.get('user_id')), {})
        row.pop('last_g1',None)
        row['reset_completed_at']=_now()


def seed_g1(service, current, owner, dataset_key):
    """업그레이드 첫 실행 때 한 번만: 동의 이력은 전이만 기록하므로 '마지막 확인 시각'이 없다.
    last_g1 이 한 번도 없고 이력의 최신 결과가 active 이며 같은 회원·DB 주인·dataset 이면 지금을 last_g1 으로 시드한다(표시를 남겨 다시 하지 않음)."""
    if not current or not owner or current.get('kakao_id')!=owner:return False
    key=_account_key(service.config().supabase_url,current.get('user_id'))
    def eligible(row):
        if not row:return False
        if row.get('last_g1') or row.get('g1_seeded') or row.get('reset_completed_at') or blocking_deny(row) or row.get('suspended'):return False
        return (row.get('consent') or {}).get('state')=='active' and row.get('binding')==dataset_key
    if not eligible(_read_state()['accounts'].get(key)):return False  # 읽기만으로 먼저 거른다 — 저장마다 파일 잠금·복호화를 두 번 하지 않게(검수 10)
    with _state() as state:
        row=state['accounts'].get(key, {})
        if not eligible(row):return False
        row['last_g1']={'at':_now(),'kakao_id':owner,'dataset_key':dataset_key}
        row['g1_seeded']=True
        return True


def mark_g1(service, current, dataset_key):
    """Only after an authoritative active status AND owner/binding verification."""
    with _state() as state:
        row = state['accounts'].setdefault(_account_key(service.config().supabase_url, current.get('user_id')), {})
        previous=row.get('last_g1') or {}
        _clear_logout_deny(row)  # 서버가 active 를 확인했다 — 이전 로그아웃 표시는 더 이상 막지 않는다
        session=current.get('connected_at')
        if previous.get('kakao_id')==current.get('kakao_id') and previous.get('dataset_key')==dataset_key and 0<=_now()-previous.get('at',0)<600 and previous.get('session')==session:return
        row['last_g1'] = {'at': _now(), 'kakao_id': current.get('kakao_id'), 'dataset_key': dataset_key, 'grant_id':(row.get('consent') or {}).get('grant_id'), 'session': session}
        row.pop('reset_completed_at',None)


def g1_session_is_fresh(service, current):
    """마지막 G1 을 확인한 세션과 다른 새 로그인 세션이면 True. 시드 G1(session 기록 없음)은 False. 리셋이 last_g1 을 지우기 전에 읽는다."""
    row = _read_state()['accounts'].get(_account_key(service.config().supabase_url, current.get('user_id')), {})
    then=(row.get('last_g1') or {}).get('session');now=(current or {}).get('connected_at')
    return bool(then and now and then!=now)


def had_g1(service, current):
    row = _read_state()['accounts'].get(_account_key(service.config().supabase_url, current.get('user_id')), {})
    return (row.get('last_g1') or {}).get('kakao_id') == current.get('kakao_id') and bool(current.get('kakao_id'))


def record_status(service, current, status, state_name):
    observe(service, status, user=current.get('user_id'))
    account = _account_key(service.config().supabase_url, current.get('user_id'))
    with _state() as data:
        row = data['accounts'].setdefault(account, {})
        consent = status.get('consent') or {}
        deny = row.get('deny') or {}
        if deny.get('reason') in ('consent_revoked', 'account_removed') and consent.get('state') == 'active' and consent.get('grant_id') and consent.get('grant_id') != deny.get('grant_id'):
            row.pop('deny', None)
        bound = (status.get('official_account') or {}).get('dataset_key')
        if bound:
            row['binding'] = bound
        if state_name == 'suspended':
            row['suspended'] = {'at': _now()}
        elif (status.get('contributor') or {}).get('status') == 'active':
            if consent.get('state') == 'active':
                _clear_logout_deny(row)
            row.pop('suspended', None)
            if (row.get('deny') or {}).get('reason') == 'suspended':
                row.pop('deny', None)


def capture_context(context):
    """Keep owner provenance even before consent; never borrow another user's writer."""
    from services import community_auth_service as cas, community_gate as gate, account_data
    service = cas.get_service()
    current = service.store.load().get('current') or {}
    owner = account_data.db_owner()
    dkey = gate.dataset_key(gate.official_username())
    if not local_allowed(service, current, owner, dkey):
        return None, 'unverified_owner'
    fp = gate.account_fingerprint(current['user_id'])
    if not context or context.get('contributor_fingerprint') != fp or context.get('dataset_key') != dkey:
        context = {'contributor_fingerprint': fp, 'dataset_key': dkey, 'state': 'inactive'}
    row = _read_state()['accounts'].get(_account_key(service.config().supabase_url, current['user_id']), {})
    consent = (row.get('consent') or {}).get('state') or 'unknown'
    if consent == 'active' and (remaining() > 0 or context.get('state') != 'active'):
        consent = 'unknown'
    if (row.get('deny') or {}).get('sticky'):
        consent = 'revoked'
        context = {**context, 'state': 'inactive'}
    return context, consent


def current_job():
    from services import community_auth_service as cas
    service = cas.get_service()
    user = (service.store.load().get('current') or {}).get('user_id')
    key = _account_key(service.config().supabase_url, user)
    row = _read_state()['accounts'].get(key, {})
    grant = (row.get('consent') or {}).get('grant_id')
    job = (row.get('jobs') or {}).get(grant)
    if job and job.get('state') != 'succeeded':
        return key, grant, dict(job)
    return None


def update_job(key, grant, **fields):
    with _state() as state:
        job = state['accounts'][key]['jobs'][grant]
        job.update(fields, updated_at=_now())
