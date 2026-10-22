"""필수 버전 잠금 (2-10, 규칙 문서 backfill-resume-20261022/CLIENT-RULES.md 6절).

status(community-account) 최상위 `client_requirement` 와 이 PC 의 `VERSION` 을 비교한다. 먹통 방지가 먼저다:
- 정상 응답(200, 형식 맞음)에서 요구 버전이 내 버전보다 높다고 **명시**됐을 때만 잠근다.
- `client_requirement: null`·내 플랫폼 요구 없음·같거나 높음 → 해제. 잠금 해제도 정상 응답에서만.
- 키 없음(서버가 판정 못 함)·버전 해석 불가·객체 모양이 틀림 → 마지막 판정 유지(새로 잠그지 않음).
- 시간 초과·네트워크·5xx·429·JSON 아님은 이 모듈까지 오지 않는다(게이트가 정상 응답에서만 부른다).
판정은 community.db meta `client_lock`({locked, min_version, url, message, decided_at})에 저장해 재시작해도 유지한다. 로컬 자료는 지우지 않는다.

잠금 중: 관리자 웹 전체 잠금 화면, 업로드·크롤·1회 전송·진행 보고 정지, Client API(`/api/v1/**`, WS)는 기존
`SERVER_UPGRADE_REQUIRED` 409 형식. status 확인(60초 poll)은 그대로 계속한다(해제를 보려고).
"""
from __future__ import annotations

import json
import re
import threading
import time

META_KEY = "client_lock"  # E(진행·설정 보고)도 이 이름을 읽는다
DEFAULT_URL = "https://github.com/Fentanest/safetyreport/releases/latest"
PLATFORM = "pc"

# contracts/community-ingest-v2/version-vectors.json: [v]N[.N[.N[.N]]][+build], N 은 1~6자리, 빠진 자리는 0.
_VERSION = re.compile(r"^v?([0-9]{1,6})((?:\.[0-9]{1,6}){0,3})(?:\+[0-9A-Za-z.-]{1,32})?$")

_cache_lock = threading.Lock()
_cache: dict[str, dict | None] = {}  # store.path → 판정(재시작 전까지 매 요청 SQLite 를 읽지 않게)


def version_parts(text) -> tuple[int, int, int, int] | None:
    if not isinstance(text, str):
        return None
    value = text.strip(" ")
    if not value or len(value) > 40:
        return None
    match = _VERSION.fullmatch(value)
    if not match:
        return None
    parts = [int(match.group(1))] + [int(p) for p in match.group(2).split(".")[1:]]
    return tuple(parts + [0] * (4 - len(parts)))


def compare(a, b) -> int | None:
    """-1 | 0 | 1, 어느 쪽이든 해석할 수 없으면 None(잠그지 않음)."""
    left, right = version_parts(a), version_parts(b)
    if left is None or right is None:
        return None
    return (left > right) - (left < right)


def app_version() -> str:
    from services.community_notice import app_version as _version
    return _version()


def _load(store) -> dict | None:
    with _cache_lock:
        if store.path in _cache:
            return _cache[store.path]
    try:
        value = json.loads(store.meta(META_KEY) or "null")
    except (TypeError, ValueError):
        value = None
    value = value if isinstance(value, dict) else None
    with _cache_lock:
        _cache[store.path] = value
    return value


def _save(store, value: dict) -> None:
    store.set_meta(META_KEY, json.dumps(value, ensure_ascii=False))
    with _cache_lock:
        _cache[store.path] = value


def forget_cache() -> None:  # 테스트 전용
    with _cache_lock:
        _cache.clear()


def decide(status: dict, version: str | None = None) -> dict | None:
    """정상 status 응답 하나 → 새 판정 dict 또는 None(마지막 판정 유지). 순수 함수."""
    if not isinstance(status, dict) or "client_requirement" not in status:
        return None  # 서버가 설정을 읽지 못했다
    req = status["client_requirement"]
    if req is None:
        return {"locked": False}
    if not isinstance(req, dict) or not isinstance(req.get("min_version"), dict):
        return None  # 모양이 틀린 객체 — 잠그지도 풀지도 않는다
    minimum = req["min_version"].get(PLATFORM)
    if minimum is None:
        return {"locked": False}
    if not isinstance(minimum, str):
        return None
    cmp = compare(app_version() if version is None else version, minimum)
    if cmp is None:
        return None
    if cmp >= 0:
        return {"locked": False}
    urls = req.get("url") if isinstance(req.get("url"), dict) else {}
    url = urls.get(PLATFORM)
    message = req.get("message")
    return {"locked": True, "min_version": minimum.strip(" "),
            "url": url if isinstance(url, str) and url.startswith("https://") else None,
            "message": message if isinstance(message, str) and message.strip() else None}


def apply_status(store, status: dict, *, version: str | None = None, now: float | None = None) -> dict | None:
    """정상 응답에서만 부른다. 판정이 바뀌면 저장하고 새 값을 돌려준다(유지면 None)."""
    verdict = decide(status, version)
    if verdict is None:
        return None
    previous = _load(store) or {"locked": False}
    verdict["decided_at"] = int(time.time() if now is None else now)
    same = {k: v for k, v in previous.items() if k != "decided_at"} == {k: v for k, v in verdict.items() if k != "decided_at"}
    if same and _load(store) is not None:
        return None
    _save(store, verdict)
    if previous.get("locked") and not verdict["locked"]:
        _on_unlock()
    return verdict


def _on_unlock() -> None:
    """해제: 멈췄던 업로드·1회 전송을 다시 깨운다(60초 poll 도 이어 받는다)."""
    try:
        from services import community_uploader
        community_uploader.wake()
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback("update_lock.unlock_wake", exc)


def read(store) -> dict | None:
    """지금 지키는 잠금 판정 또는 None. 읽을 때마다 저장된 min_version 과 지금 버전을 다시 비교한다(검수자 결정 D3):
    업데이트해서 `cmp(지금, min) >= 0` 이면 status 를 다시 받기 전이라도(카카오 세션 만료·네트워크 장애) 잠금이 아니고 저장값도 지운다.
    해석할 수 없으면(cmp None) 저장된 판정을 그대로 둔다(새로 잠그지도 풀지도 않음)."""
    value = _load(store)
    if not value or not value.get("locked"):
        return None
    if compare(app_version(), value.get("min_version")) in (0, 1):
        _save(store, {"locked": False, "decided_at": int(time.time()), "cleared_by": "version"})
        return None
    return value


def locked(store=None) -> bool:
    try:
        if store is None:
            from services.community_store import CommunityStore
            store = CommunityStore.open()
        return read(store) is not None
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback("update_lock.read", exc)
        return False  # 읽지 못하면 잠그지 않는다(먹통 방지)


def view(store=None) -> dict:
    """화면용 요약(비밀값 없음)."""
    try:
        if store is None:
            from services.community_store import CommunityStore
            store = CommunityStore.open()
        value = read(store)
    except Exception:
        value = None
    if not value:
        return {"locked": False}
    return {"locked": True, "min_version": value.get("min_version"), "url": value.get("url") or DEFAULT_URL,
            "message": value.get("message"), "current_version": app_version()}


SERVER_UPGRADE_MESSAGE = "서버 프로그램 업데이트가 필요합니다. 서버 관리자에게 업데이트를 요청해 주세요."
