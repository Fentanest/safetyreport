"""설정 단계 보고 `POST {SUPABASE_URL}/functions/v1/community-account/setup-progress` (규칙 문서 4-2).

PC 는 카카오 로그인 뒤 공식 로그인·연결 등록 전 단계를 보낸다: `kakao_ok` → `consent_ok` → `official_login_started` →
`official_login_failed`(+reason) / `official_login_ok` → `connection_registered`.
`source_app='safetyreport'`, `mode='server'`, `client=null`. 공식 사이트 ID·비밀번호·서버 주소·API 키·기기 식별자는 본문에 넣을 칸이 없다.

- 마지막으로 보낸 내용(stage+reason)과 같으면 보내지 않는다. 200 이면 "보낸 내용"으로 기록한다.
- 400·404 등 4xx(429 제외 — 옛 서버가 그 경로·action 을 모름, 또는 고쳐지지 않을 거절)도 "보냄"으로 기록하고 24시간 동안 같은 내용을
  다시 보내지 않는다(검수자 결정 D6, 예전에는 60초 게이트 갱신마다 다시 보냈다). 429·5xx·네트워크만 다음 변화 때 다시 보낸다.
- `connection_registered` 를 보낸 뒤에는 보내지 않는다(진행 보고가 이어받는다). 새 카카오 로그인(`kakao_ok`)이 오면 그 표시를 지우고 새로 시작한다.
- 같은 사용자 60초 1번(서버 429): 마지막 "시도" 뒤 60초 안이면 보내지 않고, 보내지 않은 단계는 기록하지 않아 다음 변화 때 다시 간다.
- 실패는 조용히 넘기고 재시도하지 않는다. 호출자에게 예외를 던지지 않는다.
- 기록: community.db meta `setup_progress`(JSON) = {sig, last_attempt_at, done}. 헤더 `X-Client-Version`·`X-Client-App: pc/<platform>`(6-3).
"""
from __future__ import annotations

import json
import logging
import re
import time

_log = logging.getLogger("safetyreport.community.setup_report")

PATH = "/functions/v1/community-account/setup-progress"
META_KEY = "setup_progress"
MIN_INTERVAL = 60
REJECTED_QUIET = 24 * 3600  # 4xx(429 제외)를 받은 같은 내용은 이 시간 동안 다시 보내지 않는다(D6)
TIMEOUT = 15.0
STAGES = ("kakao_ok", "consent_ok", "official_login_started", "official_login_failed", "official_login_ok",
          "connection_registered")
_REASON = re.compile(r"^[a-z0-9_]{1,40}$")

_now = time.time


def build_body(*, stage: str, reason: str | None, platform: str, client_version: str) -> dict | None:
    """정확한 본문. 모르는 단계나 형식이 틀린 reason 은 None(보내지 않는다). 성공 단계의 reason 은 항상 null."""
    if stage not in STAGES:
        return None
    if stage != "official_login_failed":
        reason = None
    elif reason is not None and not (isinstance(reason, str) and _REASON.fullmatch(reason)):
        reason = None  # 형식이 틀린 코드를 보내면 서버가 400 — 단계만 보낸다
    return {"protocol": 1, "client_version": client_version, "source_app": "safetyreport", "platform": platform,
            "mode": "server", "stage": stage, "reason": reason, "client": None}


def _store():
    from services.community_store import CommunityStore
    return CommunityStore.open()


def _load(store) -> dict:
    try:
        value = json.loads(store.meta(META_KEY) or "{}")
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def _save(store, record: dict) -> None:
    store.set_meta(META_KEY, json.dumps(record, separators=(",", ":")))


def _credentials():
    from services import community_auth_service as cas
    cfg = cas.load_config_from_settings()
    if not cfg.configured:
        raise cas.CommunityAuthError("community_unconfigured")
    return cfg.supabase_url, cfg.publishable_key, cas.get_access_token()


def _http_post(url: str, headers: dict, body: bytes):
    from services import community_ingest_client as ingest
    return ingest._http_post(url, headers, body, TIMEOUT)


def report(*, stage: str, reason: str | None = None) -> None:
    """설정 단계를 보고한다(조건에 맞을 때만 실제로 전송). 어떤 실패도 밖으로 던지지 않는다."""
    try:
        _report(stage, reason)
    except Exception as exc:
        _log.info("[community] 설정 단계 보고 건너뜀: %s", type(exc).__name__)


def _report(stage, reason) -> None:
    from services.community_gate import _platform
    from services.community_ingest_client import _client_version
    body = build_body(stage=stage, reason=reason, platform=_platform(), client_version=_client_version())
    if body is None:
        return
    store = _store()
    record = _load(store)
    now = _now()
    if stage == "kakao_ok":
        record = {}  # 새 설정 흐름: 이전의 "연결 등록 끝" 표시와 마지막 내용을 지운다
    elif record.get("done"):
        return
    sig = f"{body['stage']}|{body['reason']}"
    if record.get("sig") == sig:
        until = record.get("sig_quiet_until")
        if not isinstance(until, (int, float)) or isinstance(until, bool) or now < until:
            return  # 200 으로 보낸 내용은 계속, 4xx 로 끝난 내용은 24시간 동안 다시 보내지 않는다
    last = float(record.get("last_attempt_at") or 0)
    if last and 0 <= now - last < MIN_INTERVAL:
        return
    record["last_attempt_at"] = now
    _save(store, record)
    url, key, token = _credentials()
    headers = {"apikey": key, "Authorization": f"Bearer {token}", "Content-Type": "application/json",
               "X-Client-Version": body["client_version"], "X-Client-App": f"pc/{body['platform']}"}
    from urllib.parse import urlsplit
    if urlsplit(url).hostname != "127.0.0.1":
        from core.utils import runtime_mode
        runtime_mode.block_if_fixture("community setup report")
    status, _raw, _headers = _http_post(url.rstrip("/") + PATH, headers, json.dumps(body, separators=(",", ":")).encode())
    if status == 200:
        record["sig"] = sig
        record.pop("sig_quiet_until", None)
        if stage == "connection_registered":
            record["done"] = True
        _save(store, record)
    elif isinstance(status, int) and 400 <= status < 500 and status != 429:
        # 옛 서버(배포 전 auth 는 모르는 action 에 404)·고쳐지지 않을 거절: 보낸 것으로 보고 24시간 쉰다(D6).
        record["sig"] = sig
        record["sig_quiet_until"] = now + REJECTED_QUIET
        _save(store, record)
