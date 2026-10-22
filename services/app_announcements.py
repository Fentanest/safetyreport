"""앱 공지(규칙 문서 9절). PC 서버가 Edge `app-announcements` 를 받아 관리자 웹 화면에 넘긴다(브라우저는 Supabase 를 부르지 않는다).

GET {SUPABASE_URL}/functions/v1/app-announcements?app=pc&mode=server&platform=<windows|linux|macos|docker>&version=<VERSION>
헤더는 `apikey`(와 앱 이름이 든 User-Agent) 뿐 — 카카오 로그인 불필요. 응답은 `contracts/app-announcements/response.schema.json` 으로 다시 검사하고
틀린 항목은 버린다. 실패(네트워크·4xx·5xx·형식 이상)는 빈 목록 — 다른 기능에 영향이 없고 재시도 반복도 없다.

- 캐시: 서버 전체에 하나, 6시간(성공·실패 모두). 관리자 로그인 직후 `on_login()` 이 한 번 강제로 새로 받는다(60초 안 중복 방지).
- 보지 않기: `<데이터 폴더>/announcements/snooze.json` = {"<관리자 키>":{"<id>":"<만료 ISO>"}}. 지난 값은 읽을 때 지운다.
- 닫기: 메모리. 관리자 키별 id 집합이며 `on_login()` 이 비운다(= 이번 로그인 동안만). 서버 재시작도 비운다.
- PC 는 `app_action` 버튼을 숨기고(`primary_action` null), 링크는 허용 목록(`links.json`)과 같은 규칙만 연다.
- 본문은 평문 — 템플릿이 `textContent` 로 넣고 `\\n` 만 줄바꿈으로 본다.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlsplit

_log = logging.getLogger("safetyreport.app_announcements")

PATH = "/functions/v1/app-announcements"
CACHE_SECONDS = 6 * 3600
FORCE_MIN_SECONDS = 60
MAX_ITEMS = 3
MAX_BYTES = 64 * 1024
TIMEOUT = (5.0, 10.0)
SNOOZE_DIR = "announcements"
SNOOZE_FILE = "snooze.json"
MAX_ADMIN_KEYS = 50
MAX_IDS_PER_ADMIN = 100

_ID = re.compile(r"[a-z0-9][a-z0-9-]{2,63}")  # fullmatch 로만 쓴다(`$` 는 끝 줄바꿈을 허용한다, R2-S5)
_TITLE_BAD = re.compile(r"[\x00-\x1f\x7f]")
_BODY_BAD = re.compile(r"[\x00-\x09\x0b-\x1f\x7f]")
_URL_BAD = re.compile(r"[\s\x00-\x1f\x7f<>\"\\]")
_ITEM_KEYS = {"id", "title", "body", "primary_action", "snooze_days", "starts_at", "ends_at", "priority"}

_now = time.time  # 시험에서 바꾼다

_lock = threading.RLock()
_cache = {"fetched_at": 0.0, "items": []}
_dismissed: dict[str, set] = {}


# ── 링크 허용 목록 (vectors/links.json 과 같은 결과) ─────────────────────────────
# 서버 DB 정규식(private.app_announcement_url_allowed)·Edge(announcementUrlAllowed)와 같은 세 가지 형태(R5-5, 2026-10-22).
# URL 을 해석해 비교하던 예전 방식은 경계 경우(쿼리·조각·특수문자·한글 경로·하이픈 호스트)에서 서버와 달랐다.
_LINKS = (
    re.compile(r"https://play\.google\.com/store/apps/details\?id=com\.fentanest\.mysafetyreport(&[A-Za-z0-9._~=%-]+)*"),
    re.compile(r"https://github\.com/Fentanest/[A-Za-z0-9._~/%?=&#+-]*"),
    re.compile(r"https://([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*worklazy\.net(/[A-Za-z0-9._~/%?=&#+-]*)?"),
)


def link_allowed(url) -> bool:
    if not isinstance(url, str) or not url or len(url) > 500 or _URL_BAD.search(url):
        return False
    return any(pattern.fullmatch(url) for pattern in _LINKS)


# ── 응답 검사 ───────────────────────────────────────────────────────────────────
def _instant(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _int(value, low=None, high=None) -> bool:
    if isinstance(value, bool) or not isinstance(value, int):
        return False
    return (low is None or value >= low) and (high is None or value <= high)


def snooze_label(days: int) -> str | None:
    if days <= 0:
        return None
    return "일주일 동안 보지 않기" if days == 7 else f"{days}일 동안 보지 않기"


def validate_item(item) -> dict | None:
    """항목 하나를 스키마로 검사하고 화면용으로 정리한다. 틀리면 None(그 항목만 버린다)."""
    if not isinstance(item, dict) or set(item) != _ITEM_KEYS:
        return None
    if not (isinstance(item["id"], str) and _ID.fullmatch(item["id"])):
        return None
    title, body = item["title"], item["body"]
    if not (isinstance(title, str) and 1 <= len(title) <= 40 and not _TITLE_BAD.search(title)):
        return None
    if not (isinstance(body, str) and 1 <= len(body) <= 600 and not _BODY_BAD.search(body)):
        return None
    if not _int(item["snooze_days"], 0, 365) or not _int(item["priority"]):
        return None
    starts, ends = _instant(item["starts_at"]), (None if item["ends_at"] is None else _instant(item["ends_at"]))
    if starts is None or (item["ends_at"] is not None and ends is None):
        return None
    action = item["primary_action"]
    primary = None
    if action is not None:
        if not isinstance(action, dict):
            return None
        label = action.get("label")
        if not (isinstance(label, str) and 1 <= len(label) <= 20):
            return None
        if set(action) == {"label", "url"}:
            url = action["url"]
            if not isinstance(url, str) or len(url) > 500:
                return None
            primary = {"label": label, "url": url} if link_allowed(url) else None  # 밖의 링크: 버튼만 숨긴다
        elif set(action) == {"label", "app_action"}:
            if action["app_action"] != "open_mode_switch":
                return None
            primary = None  # PC 는 앱 동작 버튼을 보이지 않는다
        else:
            return None
    return {"id": item["id"], "title": title, "body": body, "primary_action": primary, "snooze_days": item["snooze_days"],
            "snooze_label": snooze_label(item["snooze_days"]), "starts_at": starts, "ends_at": ends}


def parse_response(payload) -> list[dict]:
    """응답 전체 검사 → 유효한 항목 목록(서버 순서, 최대 3). 형태가 틀리면 빈 목록."""
    if not isinstance(payload, dict) or payload.get("protocol") != 1 or not isinstance(payload.get("announcements"), list):
        return []
    if _instant(payload.get("server_time")) is None:
        return []
    items, seen = [], set()
    for raw in payload["announcements"][:MAX_ITEMS]:
        item = validate_item(raw)
        if item and item["id"] not in seen:
            seen.add(item["id"])
            items.append(item)
    return items


def _active(item: dict, now: float) -> bool:
    moment = datetime.fromtimestamp(now, timezone.utc)
    return item["starts_at"] <= moment and (item["ends_at"] is None or moment < item["ends_at"])


# ── 가져오기·캐시 ───────────────────────────────────────────────────────────────
def _http_get(url: str, params: list, headers: dict):
    import requests
    from services.community_ingest_client import _user_agent
    resp = requests.get(url, params=params, headers={"User-Agent": _user_agent(), **headers}, timeout=TIMEOUT,
                        allow_redirects=False, stream=True)
    try:
        body = resp.raw.read(MAX_BYTES + 1, decode_content=True)
    finally:
        resp.close()
    return resp.status_code, body


def _config():
    from services import community_auth_service as cas
    cfg = cas.load_config_from_settings()
    return (cfg.supabase_url, cfg.publishable_key) if cfg.configured else None


def _fetch() -> list[dict]:
    """네트워크 한 번. 어떤 실패든 빈 목록."""
    try:
        cfg = _config()
        if not cfg:
            return []
        url, key = cfg
        if urlsplit(url).hostname != "127.0.0.1":
            from core.utils import runtime_mode
            runtime_mode.block_if_fixture("app announcements")
        from services.community_gate import _platform
        from services.community_ingest_client import _client_version
        params = [("app", "pc"), ("mode", "server"), ("platform", _platform()), ("version", _client_version())]
        status, raw = _http_get(url.rstrip("/") + PATH, params, {"apikey": key})
        if status != 200 or len(raw) > MAX_BYTES:
            return []
        return parse_response(json.loads(raw))
    except Exception as exc:
        _log.info("[announcements] 공지를 받지 못했습니다: %s", type(exc).__name__)
        return []


def refresh(force: bool = False) -> list[dict]:
    """캐시가 6시간 안이면 그대로, 아니면(또는 force 이고 마지막 시도가 60초 전이면) 새로 받는다. 시도 시각은 실패해도 갱신한다."""
    with _lock:
        now = _now()
        age = now - _cache["fetched_at"]
        stale = age < 0 or age >= CACHE_SECONDS
        if stale or (force and age >= FORCE_MIN_SECONDS):
            _cache["fetched_at"] = now
            _cache["items"] = _fetch()
        return list(_cache["items"])


def on_login(admin_key: str) -> None:
    """관리자 로그인 직후: 이번 로그인의 "닫기"를 비우고 공지를 한 번 새로 받는다(로그인 응답을 막지 않도록 백그라운드)."""
    with _lock:
        _dismissed.pop(str(admin_key), None)
    threading.Thread(target=lambda: refresh(force=True), name="app-announcements-login", daemon=True).start()


# ── 보지 않기 파일 ──────────────────────────────────────────────────────────────
def _snooze_path() -> str:
    import settings.settings as app_settings
    return os.path.join(app_settings.datapath, SNOOZE_DIR, SNOOZE_FILE)


def _read_snooze(now: float) -> dict:
    """파일을 읽고 지난 만료·형식 틀린 값을 정리한 dict. 파일이 없거나 깨졌으면 빈 dict."""
    try:
        with open(_snooze_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    moment = datetime.fromtimestamp(now, timezone.utc)
    clean = {}
    for admin, entries in data.items():
        if not isinstance(admin, str) or not isinstance(entries, dict):
            continue
        kept = {i: e for i, e in entries.items()
                if isinstance(i, str) and _ID.fullmatch(i) and (_instant(e) or datetime.min.replace(tzinfo=timezone.utc)) > moment}
        if kept:
            clean[admin] = kept
    return clean


def _write_snooze(data: dict) -> None:
    path = _snooze_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── 공개 함수 ───────────────────────────────────────────────────────────────────
def pending_for_admin(admin_key: str) -> list[dict]:
    """이 관리자에게 지금 보여 줄 공지(서버 순서). 숨김(보지 않기·닫기)과 기간 밖 공지는 뺀다.
    캐시가 오래됐으면 여기서 네트워크를 한 번 부르므로, 웹 라우터는 스레드풀에서 호출한다. 어떤 실패도 빈 목록."""
    try:
        key = str(admin_key)
        items = refresh()
        now = _now()
        with _lock:
            snoozed = _read_snooze(now).get(key, {})
            closed = set(_dismissed.get(key, ()))
        out = []
        for item in items:
            if item["id"] in snoozed or item["id"] in closed or not _active(item, now):
                continue
            out.append({"id": item["id"], "title": item["title"], "body": item["body"], "primary_action": item["primary_action"],
                        "snooze_days": item["snooze_days"], "snooze_label": item["snooze_label"]})
        return out
    except Exception as exc:
        _log.info("[announcements] 목록을 만들지 못했습니다: %s", type(exc).__name__)
        return []


def snooze(admin_key: str, announcement_id: str) -> None:
    """지금 + snooze_days×24시간 까지 이 관리자에게 숨긴다. 일수는 캐시된 공지에서 읽는다(알 수 없는 id 는 7일, 0일 공지는 무시)."""
    try:
        if not isinstance(announcement_id, str) or not _ID.fullmatch(announcement_id):
            return
        days = 7
        with _lock:
            for item in _cache["items"]:
                if item["id"] == announcement_id:
                    days = item["snooze_days"]
                    break
            if days <= 0:
                return
            now = _now()
            data = _read_snooze(now)
            admin = data.setdefault(str(admin_key), {})
            admin[announcement_id] = _iso(now + days * 86400)
            while len(admin) > MAX_IDS_PER_ADMIN:
                admin.pop(min(admin, key=admin.get))
            while len(data) > MAX_ADMIN_KEYS:
                data.pop(next(iter(data)))
            _write_snooze(data)
    except Exception as exc:
        _log.info("[announcements] 보지 않기를 저장하지 못했습니다: %s", type(exc).__name__)


def dismiss(admin_key: str, announcement_id: str) -> None:
    """이번 로그인 동안만 숨긴다(메모리)."""
    if isinstance(announcement_id, str) and _ID.fullmatch(announcement_id):
        with _lock:
            _dismissed.setdefault(str(admin_key), set()).add(announcement_id)


def _reset_for_tests() -> None:
    with _lock:
        _cache.update(fetched_at=0.0, items=[])
        _dismissed.clear()
