"""교환 DB와 기존 완료 마커에 영향을 주지 않는 실행별 outcome sidecar."""
import json
import os
import re
import uuid
from datetime import datetime, timezone
import settings.settings as settings
from core.utils.atomic_file import write_bytes

ENV_KEY = 'SAFETYREPORT_CRAWL_RUN_ID'
TERMINAL = frozenset(('succeeded', 'partial', 'failed', 'cancelled', 'unknown'))


def _path(run_id):
    if not isinstance(run_id, str) or not re.fullmatch('[a-f0-9]{32}', run_id):
        raise ValueError('invalid crawl run id')
    return os.path.join(settings.datapath, 'crawl_runs', run_id + '.json')


def read(run_id):
    try:
        with open(_path(run_id), encoding='utf-8') as source:
            state = json.load(source)
    except FileNotFoundError:
        return None
    if not isinstance(state, dict) or state.get('run_id') != run_id or state.get('attempt') != 1:
        raise ValueError('invalid crawl outcome')
    return state


def write(run_id, state, **fields):
    payload = {'run_id': run_id, 'attempt': 1, 'state': state,
               'updated_at': datetime.now(timezone.utc).isoformat(), **fields}
    write_bytes(_path(run_id), json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    return payload


def create():
    run_id = uuid.uuid4().hex
    write(run_id, 'accepted')
    write_bytes(os.path.join(settings.datapath, 'crawl_runs', 'latest'), run_id.encode('ascii'))
    return run_id


def completion(run_id, return_code):
    try:
        result = read(run_id) if isinstance(run_id, str) else None
    except (OSError, ValueError):
        result = None
    if return_code is not None and isinstance(return_code, int) and return_code < 0:
        return {'state': 'cancelled', 'run_id': run_id}
    if result and result['state'] in TERMINAL:
        if result['state'] == 'succeeded' and return_code != 0:
            return {**result, 'state': 'unknown'}
        return result
    return {'state': 'failed' if isinstance(return_code, int) and return_code != 0 else 'unknown',
            'run_id': run_id if isinstance(run_id, str) else None}


def latest():
    try:
        with open(os.path.join(settings.datapath, 'crawl_runs', 'latest'), encoding='ascii') as source:
            run_id = source.read(33)
        return read(run_id)
    except (OSError, ValueError, UnicodeError):
        return None
