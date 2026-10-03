"""제출 응답이 유실된 별점의 자동 재제출을 막는 서버 로컬 journal.

DB 교환 컬럼은 추가하지 않는다. 계정·전화번호가 달라지면 다른 scope다.
"""
import hashlib
import json
import os
from datetime import datetime, timezone
import settings.settings as settings
from core.utils.atomic_file import write_bytes


def _path(report_number):
    scope = json.dumps([settings.username, settings.phone_number, str(report_number)], ensure_ascii=False)
    name = hashlib.sha256(scope.encode('utf-8')).hexdigest() + '.json'
    return os.path.join(settings.datapath, 'rating_operations', name)


def read(report_number):
    try:
        with open(_path(report_number), encoding='utf-8') as source:
            result = json.load(source)
        if not isinstance(result, dict) or result.get('report_number') != str(report_number):
            raise RuntimeError('별점 제출 기록을 확인할 수 없습니다.')
        return result
    except FileNotFoundError:
        return {}
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError('별점 제출 기록을 확인할 수 없습니다.') from exc


def write(report_number, state, *, score=None):
    payload = {'report_number': str(report_number), 'state': state, 'score': score,
               'updated_at': datetime.now(timezone.utc).isoformat()}
    write_bytes(_path(report_number), json.dumps(payload, ensure_ascii=False).encode('utf-8'))
