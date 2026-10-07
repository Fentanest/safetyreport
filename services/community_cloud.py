"""Durable cloud cooldown and verified offline authorization, separate from personal DB.

Emergency consent policy permits owned local data regardless of consent. Upload
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
import sqlite3
import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime

from services.community_auth_store import _FileLock, atomic_write

_now = time.time
MIN_RETRY_SECONDS = 300
_locks = {}
_locks_lock = threading.Lock()


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
    """
    with _state() as state:
        key = _scope(base)
        row = state['cloud'].get(key, {})
        until = row.get('next_attempt_at', 0)
        if until > _now():
            raise CloudDeferred(until)
        try:
            response = send()
        except Exception:
            state['cloud'][key] = {'next_attempt_at': _now() + MIN_RETRY_SECONDS, 'error': 'network_error'}
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
            if transient:
                state['cloud'][key] = {'next_attempt_at': _now() + max(MIN_RETRY_SECONDS, retry_after(headers, body)),
                                      'error': 'rate_limited' if status == 429 else 'server_unavailable'}
            else:
                state['cloud'].pop(key, None)
    if failed:
        raise CloudDeferred(_now() + MIN_RETRY_SECONDS)
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
        row = state['accounts'].get(_account_key(service.config().supabase_url, current.get('user_id')), {})
        row.pop('deny', None)


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
        if result == 'active' and grant and source == 'status':
            row.setdefault('jobs', {}).setdefault(grant, {'state': 'pending', 'phase': 'reshare', 'created_at': _now()})
        if prior.get('state') == result and (result != 'active' or prior.get('grant_id') in (None, grant)):
            if grant:
                row['consent'] = {**prior, 'grant_id': grant, 'policy_version': consent.get('policy_version')}
            return
        event = {'state': result, 'grant_id': grant, 'policy_version': consent.get('policy_version'),
                 'at': _now(), 'source': source}
        row['consent'] = event
        row.setdefault('history', []).append(event)
        if result == 'active' and grant and source == 'status':
            row.setdefault('jobs', {}).setdefault(grant, {'state': 'pending', 'phase': 'reshare', 'created_at': _now()})


def suspension(service):
    """Consent relaxation never waives an authoritative contributor suspension."""
    current = service.store.load().get('current') or {}
    try:
        row = _read_state()['accounts'].get(_account_key(service.config().supabase_url, current.get('user_id')), {})
        return bool(row.get('suspended')) or (row.get('deny') or {}).get('reason') == 'suspended'
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return False  # local_allowed / cloud.view fail closed for unreadable state


def local_allowed(service, current, owner, dataset_key):
    if not current or not owner or current.get('kakao_id') != owner:
        return False
    try:
        state = _read_state()
        row = state['accounts'].get(_account_key(service.config().supabase_url, current.get('user_id')), {})
        if row.get('suspended') or (row.get('deny') or {}).get('reason') in ('suspended', 'db_owner_mismatch', 'official_account_mismatch'):
            return False
        bound = row.get('binding')
        return not bound or bound == dataset_key
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return False


def record_status(service, current, status, state_name):
    observe(service, status, user=current.get('user_id'))
    account = _account_key(service.config().supabase_url, current.get('user_id'))
    with _state() as data:
        row = data['accounts'].setdefault(account, {})
        consent = status.get('consent') or {}
        deny = row.get('deny') or {}
        if deny.get('reason') == 'consent_revoked' and consent.get('state') == 'active' and consent.get('grant_id') and consent.get('grant_id') != deny.get('grant_id'):
            row.pop('deny', None)
        bound = (status.get('official_account') or {}).get('dataset_key')
        if bound:
            row['binding'] = bound
        if state_name == 'suspended':
            row['suspended'] = {'at': _now()}
        elif (status.get('contributor') or {}).get('status') == 'active':
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
