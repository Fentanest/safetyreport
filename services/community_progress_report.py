"""1회 전송 진행 보고 `POST {SUPABASE_URL}/functions/v1/community-ingest/progress` (규칙 문서 4-1).

개수와 상태 코드만 보낸다(`contracts/community-ingest-v2/progress.schema.json`). 실패는 조용히 넘기고 재시도하지 않는다.
호출자(C)는 `maybe_send(...)` 한 곳만 부르면 되고, 이 모듈이 보낼지 말지를 정한다.

trigger
- `start`        앱(서버) 기동·게이트 통과 직후 1회. 항상 보낸다(60초 규칙만 적용).
- `state_change` `(state, phase, reason, official_login, optimize.*)` 중 하나가 마지막 전송과 다를 때만(개수만 바뀐 것은 보내지 않는다).
- `periodic`     진행 중 상태(`listing`·`filling`·`uploading`·`paused`·`waiting_optimize`·`holds`)에서 마지막 전송 뒤 15분이 지났을 때.
- `daily`        `done` 상태에서 마지막 전송 날짜(KST)가 오늘이 아닐 때.
같은 연결에 60초 안에 두 번 보내지 않는다(마지막 "시도" 시각 기준, 실패한 시도도 센다).

기록(community.db meta `progress_report`, JSON)
  {connection_id, last_attempt_at(epoch), last_sig, last_date(KST YYYY-MM-DD), last_result:{http_status, code, at}, maintenance:{...}|없음}
- 503 `maintenance` 를 받으면 `maintenance` = {resume_at(epoch 초|null), retry_after(초|null), message(평문|null), seen_at(epoch)} 를 남긴다.
  C 는 `last_maintenance(store)` 로 읽어 5절 `upload_pause`(source='503')로 옮긴다. 성공 응답이 오면 지운다.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone

_log = logging.getLogger("safetyreport.community.progress")

PATH = "/functions/v1/community-ingest/progress"
META_KEY = "progress_report"
MIN_INTERVAL = 60
PERIODIC_INTERVAL = 15 * 60
MAX_BODY_BYTES = 4096
TIMEOUT = (5.0, 15.0)
ACTIVE_STATES = ("listing", "filling", "uploading", "paused", "waiting_optimize", "holds")
STATES = ACTIVE_STATES + ("done", "none")
TRIGGERS = ("start", "state_change", "periodic", "daily")

_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_CODE = re.compile(r"^[a-z0-9_]{1,40}$")
_INSTANT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$")
_VERSION = re.compile(r"^[0-9][0-9A-Za-z.+_-]{0,39}$")
_PARSER = re.compile(r"^[0-9A-Za-z.+_-]{1,40}$")
_COUNTS = ("total", "stored", "queued", "pending", "holds", "stale_holds", "raw_unavailable", "not_eligible")
_PLATFORMS = ("windows", "linux", "macos", "docker", "android", "ios", "other")

_now = time.time  # 시험에서 바꾼다


# ── 본문 ────────────────────────────────────────────────────────────────────────
def _instant(value) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if value <= 0:
            return None
        return datetime.fromtimestamp(float(value), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, str) and _INSTANT.match(value):
        return value
    return None


def _count(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return min(value, 10_000_000)


def _code(value) -> str | None:
    return value if isinstance(value, str) and _CODE.match(value) else None


def build_body(*, connection_id: str, backfill: dict, optimize: dict, official_login: str,
               platform: str, client_version: str, parser_version: str) -> dict:
    """스키마 모양의 본문. 모르는 값은 null/기본값으로 정리한다(키를 더 넣지 않는다). PC 는 `pace` null."""
    backfill = backfill if isinstance(backfill, dict) else {}
    optimize = optimize if isinstance(optimize, dict) else {}
    state = backfill.get("state") if backfill.get("state") in STATES else "none"
    out = {"state": state, "phase": None, "reason": None, "next_attempt_at": None,
           **{k: None for k in _COUNTS}, "run_started_at": None, "updated_at": None}
    if state != "none":
        out.update(phase=_code(backfill.get("phase")), reason=_code(backfill.get("reason")),
                   next_attempt_at=_instant(backfill.get("next_attempt_at")),
                   run_started_at=_instant(backfill.get("run_started_at")), updated_at=_instant(backfill.get("updated_at")))
        for key in _COUNTS:
            out[key] = _count(backfill.get(key))
    login = official_login if official_login in ("ok", "retry", "manual_required") else "ok"
    return {"protocol": 1, "contract": "community-progress-v1", "connection_id": connection_id,
            "source_app": "safetyreport", "platform": platform, "client_version": client_version,
            "parser_version": parser_version, "backfill": out,
            "optimize": {k: bool(optimize.get(k)) for k in ("required", "running", "holds_only")},
            "official_login": login, "pace": None}


def validate_body(body) -> bool:
    """`progress.schema.json` 과 같은 규칙(추가 키 금지·형식·4KiB). jsonschema 에 기대지 않는다."""
    def is_count(v):
        return v is None or (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10_000_000)

    def is_code(v):
        return v is None or (isinstance(v, str) and bool(_CODE.match(v)))

    def is_instant(v):
        return v is None or (isinstance(v, str) and bool(_INSTANT.match(v)))

    try:
        if not isinstance(body, dict) or set(body) != {"protocol", "contract", "connection_id", "source_app", "platform",
                                                          "client_version", "parser_version", "backfill", "optimize",
                                                          "official_login", "pace"}:
            return False
        if body["protocol"] != 1 or body["contract"] != "community-progress-v1":
            return False
        if not (isinstance(body["connection_id"], str) and _UUID.match(body["connection_id"])):
            return False
        if body["source_app"] not in ("safetyreport", "safetyreport-mobile") or body["platform"] not in _PLATFORMS:
            return False
        if not (isinstance(body["client_version"], str) and _VERSION.match(body["client_version"])):
            return False
        if not (isinstance(body["parser_version"], str) and _PARSER.match(body["parser_version"])):
            return False
        b = body["backfill"]
        if not isinstance(b, dict) or set(b) != {"state", "phase", "reason", "next_attempt_at", *_COUNTS, "run_started_at", "updated_at"}:
            return False
        if b["state"] not in STATES or not is_code(b["phase"]) or not is_code(b["reason"]):
            return False
        if not all(is_instant(b[k]) for k in ("next_attempt_at", "run_started_at", "updated_at")):
            return False
        if not all(is_count(b[k]) for k in _COUNTS):
            return False
        o = body["optimize"]
        if not isinstance(o, dict) or set(o) != {"required", "running", "holds_only"} or not all(isinstance(v, bool) for v in o.values()):
            return False
        if body["official_login"] not in ("ok", "retry", "manual_required"):
            return False
        pace = body["pace"]
        if pace is not None and body["source_app"] == "safetyreport":
            return False  # PC 는 pace 를 보내지 않는다(서버도 거절)
        if pace is not None:
            if not isinstance(pace, dict) or set(pace) != {"foreground_slices_today", "background_runs_today"}:
                return False
            for v in pace.values():
                if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 100000):
                    return False
        return len(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")) <= MAX_BODY_BYTES
    except (KeyError, TypeError):
        return False


def signature(body: dict) -> str:
    """상태 변화 판정 값: (state, phase, reason, official_login, optimize.*). 개수·시각은 넣지 않는다."""
    b, o = body["backfill"], body["optimize"]
    return json.dumps([b["state"], b["phase"], b["reason"], body["official_login"], o["required"], o["running"], o["holds_only"]])


# ── 기록 ────────────────────────────────────────────────────────────────────────
def _load(store) -> dict:
    try:
        value = json.loads(store.meta(META_KEY) or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _save(store, record: dict) -> None:
    store.set_meta(META_KEY, json.dumps(record, ensure_ascii=False, separators=(",", ":")))


def last_maintenance(store) -> dict | None:
    """마지막 진행 보고가 503 maintenance 였다면 {resume_at, retry_after, message, seen_at}, 아니면 None."""
    value = _load(store).get("maintenance")
    return value if isinstance(value, dict) else None


def _kst_date(epoch: float) -> str:
    return (datetime.fromtimestamp(epoch, timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d")


def should_send(record: dict, *, connection_id: str, trigger: str, sig: str, state: str, now: float) -> bool:
    """보낼지 판정(순수). `record` 는 meta 에 저장된 값."""
    if trigger not in TRIGGERS:
        return False
    same = record.get("connection_id") == connection_id
    last_attempt = float(record.get("last_attempt_at") or 0) if same else 0.0
    if last_attempt and 0 <= now - last_attempt < MIN_INTERVAL:
        return False
    if trigger == "start":
        return True
    first = not same or not last_attempt  # 이 연결로 보낸 적이 없다
    if trigger == "state_change":
        return first or record.get("last_sig") != sig
    if trigger == "periodic":
        return state in ACTIVE_STATES and (first or now - last_attempt >= PERIODIC_INTERVAL)
    return state == "done" and (first or record.get("last_date") != _kst_date(now))  # daily


# ── 전송 ────────────────────────────────────────────────────────────────────────
def _blocked(store) -> bool:
    """중지(5절)·필수 버전 잠금(6절) 중에는 보내지 않는다. 저장 형식은 C 의 `upload_pause`·`client_lock`(JSON, 아래)."""
    for key, flag in (("upload_pause", "paused"), ("client_lock", "locked")):
        try:
            value = json.loads(store.meta(key) or "null")
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict) and value.get(flag):
            if key == "upload_pause":
                resume = value.get("resume_at")
                if isinstance(resume, (int, float)) and not isinstance(resume, bool) and resume <= _now():
                    continue  # 재개 시각이 지났으면 C 가 status 로 확인하기 전이라도 막지 않는다(서버가 최종 판정)
            return True
    return False


def _http_post(url: str, headers: dict, body: bytes):
    from services import community_ingest_client as ingest
    return ingest._http_post(url, headers, body, sum(TIMEOUT))


def _credentials():
    """(supabase_url, publishable_key, access_token). 못 받으면 예외 — 호출자가 조용히 넘긴다."""
    from services import community_auth_service as cas
    cfg = cas.load_config_from_settings()
    if not cfg.configured:
        raise cas.CommunityAuthError("community_unconfigured")
    return cfg.supabase_url, cfg.publishable_key, cas.get_access_token()


def _parse_maintenance(raw: bytes, headers: dict, now: float) -> dict:
    retry_after = resume_at = message = None
    try:
        err = json.loads(raw).get("error") or {}
        if err.get("code") != "maintenance":
            return {}
        if isinstance(err.get("retry_after_seconds"), (int, float)) and not isinstance(err["retry_after_seconds"], bool):
            retry_after = int(err["retry_after_seconds"])
        if isinstance(err.get("resume_at"), str):
            resume_at = datetime.fromisoformat(err["resume_at"].replace("Z", "+00:00")).timestamp()
        if isinstance(err.get("pause_message"), str):
            message = err["pause_message"][:200]
    except (ValueError, TypeError, AttributeError):
        return {}
    return {"resume_at": resume_at, "retry_after": retry_after, "message": message, "seen_at": now}


def maybe_send(*, store, trigger: str, backfill: dict, optimize: dict, official_login: str) -> None:
    """진행 보고를 보낼 조건이면 한 번 보낸다. 어떤 실패도 밖으로 던지지 않는다."""
    try:
        _maybe_send(store, trigger, backfill, optimize, official_login)
    except Exception as exc:  # 조용히 넘긴다(다음 주기에 다시)
        _log.info("[community] 진행 보고 건너뜀: %s", type(exc).__name__)


def _maybe_send(store, trigger, backfill, optimize, official_login) -> None:
    ctx = store.active_context() if hasattr(store, "active_context") else None
    connection_id = (ctx or {}).get("connection_id")
    if not isinstance(connection_id, str) or not _UUID.match(connection_id):
        return  # 연결 전 단계는 설정 단계 보고(community_setup_report)가 맡는다
    if _blocked(store):
        return
    from services.community_capture import PARSER_VERSION
    from services.community_gate import _platform
    from services.community_ingest_client import _client_version
    body = build_body(connection_id=connection_id, backfill=backfill, optimize=optimize, official_login=official_login,
                      platform=_platform(), client_version=_client_version(), parser_version=PARSER_VERSION)
    if not validate_body(body):
        _log.info("[community] 진행 보고 본문이 계약과 다릅니다 — 보내지 않습니다")
        return
    now = _now()
    record = _load(store)
    sig = signature(body)
    if not should_send(record, connection_id=connection_id, trigger=trigger, sig=sig, state=body["backfill"]["state"], now=now):
        return
    # 시도 시각·내용을 먼저 남긴다 → 실패해도 같은 변화를 다시 보내지 않는다(재시도 없음)
    record = {"connection_id": connection_id, "last_attempt_at": now, "last_sig": sig, "last_date": _kst_date(now),
              "last_result": {"http_status": None, "code": "not_sent", "at": now},
              **({"maintenance": record["maintenance"]} if isinstance(record.get("maintenance"), dict) else {})}
    _save(store, record)
    try:
        url, key, token = _credentials()
    except Exception as exc:
        _log.info("[community] 진행 보고 토큰을 받지 못했습니다: %s", type(exc).__name__)
        return
    payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")  # gzip 금지
    headers = {"apikey": key, "Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        from urllib.parse import urlsplit
        if urlsplit(url).hostname != "127.0.0.1":
            from core.utils import runtime_mode
            runtime_mode.block_if_fixture("community progress report")
        status, raw, resp_headers = _http_post(url.rstrip("/") + PATH, headers, payload)
    except Exception as exc:
        record["last_result"] = {"http_status": None, "code": type(exc).__name__[:40].lower(), "at": now}
        _save(store, record)
        return
    code = None
    if status == 200:
        record.pop("maintenance", None)
    else:
        try:
            code = _code((json.loads(raw).get("error") or {}).get("code"))
        except (ValueError, TypeError, AttributeError):
            code = None
        if status == 503:
            maint = _parse_maintenance(raw, resp_headers, now)
            if maint:
                record["maintenance"] = maint
    record["last_result"] = {"http_status": status, "code": code, "at": now}
    _save(store, record)
