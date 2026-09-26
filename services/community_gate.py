"""필수 진입 게이트 K && C (contracts/community-ingest/gate.md) — PC·Docker·소스 실행 공통.

K = 이 서버의 커뮤니티 세션이 유효하고 중앙 status 가 카카오 연결·세션을 확인함.
C = 중앙에 현재 정책 버전·동의문 해시와 같은 활성 동의 grant 가 있음.
로컬 파일의 "완료" 값으로는 통과하지 않는다. 중앙 ingest 는 이 캐시와 무관하게 저장 트랜잭션에서 다시 확인한다.

캐시: 화면 이동은 10분(CACHE_TTL), 새 작업(크롤·업로드·초기화·설정 저장·자정 실행)은 require_fresh(60).
온라인이면 60초 주기 refresh_now()(스케줄러 job community-gate-poll)로 원격 철회를 반영한다.
통과하면 writer 연결을 확보하고 community.db context 를 활성화한다. 미충족·철회·로그아웃이면 context 를 끈다.
"""
from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading
import time
from typing import Callable

from urllib.parse import urlsplit

import requests

from core.utils import runtime_mode
from services import community_auth_service as cas
from services.community_account_client import AccountApiError, CommunityAccountClient

_log = logging.getLogger("safetyreport.core.community_gate")

REQUIRED_POLICY_VERSION = "2026-09-26.1"
# contracts/community-ingest/consent/share-consent-2026-09-26.1.sha256 — tests/test_community_gate.py 가 같은지 확인
CONSENT_TEXT_SHA256 = "818703977dbf1596a82df8ff68408d0907280adbda7ce2140879a2ab5fd6a6fa"
CONSENT_TEXT_FILE = "contracts/community-ingest/consent/share-consent-2026-09-26.1.md"
CACHE_TTL = 600.0
FRESH_SECONDS = 60.0

STATES = ("ok", "config_invalid", "kakao_required", "kakao_reauth_required", "session_unreadable",
          "verification_required", "suspended", "consent_required")


def dataset_key(username: str | None) -> str | None:
    """공식(안전신문고) 계정 네임스페이스. 클라이언트 주장값 — writer 충돌 제어와 fact 구분용일 뿐 계정 증명이 아니다."""
    name = (username or "").strip().lower()
    if not name:
        return None
    return hashlib.sha256(f"safetyreport-dataset|v1|{name}".encode("utf-8")).hexdigest()


def account_fingerprint(user_id: str) -> str:
    return hashlib.sha256(f"sr-community-account|v1|{user_id}".encode("utf-8")).hexdigest()[:32]


def official_username() -> str | None:
    import settings.settings as app_settings

    return getattr(app_settings._instance, "username", None)


def _platform() -> str:
    if os.path.exists("/.dockerenv"):
        return "docker"
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def config_state(cfg: cas.CommunityConfig) -> str:
    if "config_conflict" in cfg.problems:
        return "conflict"
    if "publishable_key" in cfg.problems:
        return "secret_detected"
    if cfg.problems:
        return "placeholder"
    if not (cfg.supabase_url and cfg.publishable_key):
        return "missing"
    return "ok"


def decide(config: str, session: str, status: dict | None, age: float | None, invalidated: bool) -> tuple[str, list]:
    """gate.md 판정 순서(먼저 걸린 것이 결과). 순수 함수."""
    if config != "ok":
        return "config_invalid", [f"config_{config}"]
    if session == "none":
        return "kakao_required", ["session_none"]
    if session == "reauth_required":
        return "kakao_reauth_required", ["session_reauth_required"]
    if session == "unreadable":
        return "session_unreadable", ["session_unreadable"]
    if status is None or invalidated or age is None or age > CACHE_TTL:
        return "verification_required", ["status_stale"]
    gate = status.get("gate") or {}
    if not gate.get("kakao"):
        return "kakao_required", [r for r in (gate.get("reasons") or []) if isinstance(r, str)] or ["kakao_missing"]
    contributor = (status.get("contributor") or {}).get("status") or "none"
    if contributor not in ("active", "none"):
        return "suspended", [f"contributor_{contributor}"]
    consent = status.get("consent") or {}
    policy = status.get("policy") or {}
    if consent.get("state") != "active":
        return "consent_required", [f"consent_{consent.get('state') or 'none'}"]
    if consent.get("policy_version") != REQUIRED_POLICY_VERSION or policy.get("required_version") != REQUIRED_POLICY_VERSION \
            or policy.get("consent_text_sha256") != CONSENT_TEXT_SHA256:
        return "consent_required", ["consent_outdated"]
    return "ok", []


class _Gate:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._status: dict | None = None
        self._verified_at: float | None = None
        self._invalidated = True  # cold start: 한 번은 중앙 status 를 받아야 한다
        self._last_state: str | None = None
        self._listeners: list[Callable[[dict], None]] = []
        self._writer_note: dict | None = None
        self._last_error: str | None = None
        self._takeover_requested = False
        self._last_attempt: float | None = None

    # -- 입력 --------------------------------------------------------------------------------------------
    @staticmethod
    def _session_state(service: cas.CommunityAuthService) -> tuple[str, dict | None]:
        try:
            st = service.store.load()
        except cas.StoreUnreadable:
            return "unreadable", None
        if st.get("current"):
            return "valid", st["current"]
        return ("reauth_required" if st.get("reauth") else "none"), None

    def evaluate(self) -> dict:
        service = cas.get_service()
        cfg = service.config()
        session, _ = self._session_state(service)
        with self._lock:
            status, verified_at, invalidated = self._status, self._verified_at, self._invalidated
        age = None if verified_at is None else max(0.0, self._clock() - verified_at)
        state, reasons = decide(config_state(cfg), session, status, age, invalidated)
        if state == "verification_required" and self._last_error:
            reasons = reasons + [self._last_error]
        result = {"state": state, "can_enter": state == "ok", "reasons": reasons, "verified_age": age}
        self._notify_if_changed(result)
        return result

    # -- 재검증 ------------------------------------------------------------------------------------------
    def refresh_now(self) -> dict:
        """중앙 status 를 다시 받는다(한 번에 하나). 네트워크 장애면 유효 기간 안의 성공 캐시는 유지한다."""
        with self._refresh_lock:
            try:
                self._refresh_locked()
            except Exception as exc:  # 게이트 갱신 실패가 서버를 멈추지 않게 한다(판정은 fail-closed)
                _log.warning("[community] 게이트 확인 중 오류: %s", type(exc).__name__)
                self._last_error = "gate_error"
            result = self.evaluate()
            if not result["can_enter"] and result["state"] != "verification_required":
                self._deactivate(result["state"])
            return result

    def _refresh_locked(self) -> None:
        service = cas.get_service()
        cfg = service.config()
        session, current = self._session_state(service)
        if config_state(cfg) != "ok" or session != "valid":
            with self._lock:
                self._status = None
            return
        try:
            token = service.get_access_token()
        except cas.CommunityAuthError as exc:
            self._last_error = exc.code
            if exc.code != "auth_unavailable":
                self._mark_invalid()
            return
        client = CommunityAccountClient(cfg.supabase_url, cfg.publishable_key)
        writer = self._load_writer(service)
        own = writer if writer and writer.get("user_id") == current.get("user_id") else None
        try:
            status = client.status(token, own.get("connection_id") if own else None)
        except AccountApiError as exc:
            self._last_error = exc.code
            if not exc.transient:
                self._mark_invalid()
            return
        with self._lock:
            self._status, self._verified_at, self._invalidated = status, self._clock(), False
        self._last_error = None
        state, _ = decide("ok", "valid", status, 0.0, False)
        if state == "ok":
            self._ensure_writer(service, client, token, status, own, current)

    def check_for_request(self, retry_interval: float = 15.0) -> dict:
        """HTTP 요청용: 캐시 판정. 확인이 필요하면(cold start·무효화·만료) 중앙 status 를 받되,
        네트워크 장애 때 요청마다 10초씩 막히지 않게 retry_interval 초에 한 번만 시도한다."""
        result = self.evaluate()
        if result["state"] != "verification_required":
            return result
        now = self._clock()
        with self._lock:
            due = self._last_attempt is None or now - self._last_attempt >= retry_interval
            if due:
                self._last_attempt = now
        return self.refresh_now() if due else result

    def require_fresh(self, max_age: float = FRESH_SECONDS) -> dict:
        with self._lock:
            age = None if self._verified_at is None else self._clock() - self._verified_at
            stale = self._invalidated or age is None or age > max_age
        return self.refresh_now() if stale else self.evaluate()

    def _mark_invalid(self) -> None:
        with self._lock:
            self._invalidated = True

    def invalidate(self, reason: str) -> None:
        """로그인·로그아웃·계정 변경·동의 저장/철회·업로드 401/403 뒤. 다음 판정은 중앙 status 를 다시 받아야 통과한다."""
        self._mark_invalid()
        self._deactivate(reason)
        self.evaluate()

    def on_change(self, callback: Callable[[dict], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def _notify_if_changed(self, result: dict) -> None:
        with self._lock:
            changed = result["state"] != self._last_state
            self._last_state = result["state"]
            listeners = list(self._listeners)
        if changed:
            for cb in listeners:
                try:
                    cb(result)
                except Exception:
                    _log.warning("[community] 게이트 변경 알림 처리 실패", exc_info=True)

    # -- writer 연결 -------------------------------------------------------------------------------------
    @staticmethod
    def _load_writer(service: cas.CommunityAuthService) -> dict | None:
        try:
            writer = service.store.load_writer()
        except cas.StoreUnreadable:
            return None
        return writer if writer and writer.get("connection_id") and writer.get("connection_secret") else None

    def _ensure_writer(self, service, client: CommunityAccountClient, token: str, status: dict,
                       writer: dict | None, current: dict) -> None:
        """현재 사용자·공식 계정으로 writer 연결을 확보하고 community.db context 를 활성화한다.
        게이트 통과(진입)와 별개다 — 여기서 막히면 화면은 쓰되 업로드만 멈춘다(writer 메모로 표시)."""
        dkey = dataset_key(official_username())
        user_id = current.get("user_id")
        if not dkey or not user_id:
            self._set_writer_note("official_account_required")
            return
        conn = status.get("connection") if writer and writer.get("dataset_key") == dkey else None
        takeover, self._takeover_requested = self._takeover_requested, False
        try:
            if conn and conn.get("status") == "active" and not takeover:
                epoch, last = conn.get("writer_epoch"), conn.get("last_accepted_revision")
                if not conn.get("bound_to_current_session"):
                    res = client.rebind_connection(token, writer["connection_id"], writer["connection_secret"])
                    epoch, last = res.get("writer_epoch", epoch), res.get("last_accepted_revision", last)
                writer = {**writer, "writer_epoch": epoch}
            elif conn and conn.get("status") in ("superseded", "suspended") and not takeover:
                self._set_writer_note(f"connection_{conn['status']}")
                return
            else:  # 연결 없음·다른 사용자·다른 공식 계정·폐기됨·사용자가 전환 요청
                secret = cas.random_b64url(32)
                res = client.register_connection(token, platform=_platform(), device_label=service.default_device_label(),
                                                 dataset_key=dkey, connection_secret=secret, takeover=takeover)
                writer = {"connection_id": res["connection_id"], "connection_secret": secret,
                          "writer_epoch": res["writer_epoch"], "dataset_key": dkey, "user_id": user_id}
                last = 0
            service.store.save_writer(writer)
        except AccountApiError as exc:
            note = {"code": exc.code}
            if isinstance(exc.extra.get("active_writer"), dict):
                aw = exc.extra["active_writer"]
                note["active_writer"] = {k: aw.get(k) for k in ("device_label", "platform", "source_app", "created_at")}
            self._set_writer_note(exc.code, note)
            return
        from services.community_store import CommunityStore

        consent = status.get("consent") or {}
        store = CommunityStore.open()
        store.set_context(contributor_fingerprint=account_fingerprint(user_id), connection_id=writer["connection_id"],
                          writer_epoch=int(writer["writer_epoch"]), dataset_key=dkey,
                          consent_grant_id=consent.get("grant_id"), policy_version=consent.get("policy_version"),
                          consent_text_sha256=CONSENT_TEXT_SHA256, source_app="safetyreport", source_mode="server")
        if isinstance(last, int) and last > 0:
            store.raise_revision_floor(last)
        self._writer_note = None
        if store.meta("manifest_scope") != f"{dkey}:{int(writer['writer_epoch'])}":
            try:
                from services import community_uploader

                community_uploader.refresh_server_completed()
            except Exception as exc:  # 실패해도 크롤 시작 전에 다시 확인한다(fail-closed 는 수집 쪽)
                _log.info("[community] 완료 목록(manifest) 갱신을 미룹니다: %s", type(exc).__name__)

    def _set_writer_note(self, code: str, note: dict | None = None) -> None:
        self._writer_note = note or {"code": code}
        self._deactivate(code)

    def writer_note(self) -> dict | None:
        return self._writer_note

    def request_takeover(self) -> dict:
        """다른 기기가 업로드 연결을 쓰고 있을 때 관리자가 '이 서버로 업로드 전환'을 누르면."""
        self._takeover_requested = True
        self._mark_invalid()
        return self.refresh_now()

    @staticmethod
    def _deactivate(reason: str) -> None:
        try:
            from services.community_store import CommunityStore

            store = CommunityStore.open()
            ctx = store.context()
            if not ctx or ctx.get("state") == "active" or ctx.get("inactive_reason") != reason:
                store.deactivate_context(reason)
        except Exception:
            _log.warning("[community] community.db context 를 끄지 못했습니다", exc_info=False)

    # -- 모바일 Client 위임 ------------------------------------------------------------------------------
    def verify_client_user_token(self, token: str | None) -> bool:
        """모바일 Client 가 보낸 자기 access token 이 이 서버에 연결된 커뮤니티 사용자와 같은지 GoTrue /user 로 확인."""
        if not token or len(token) > 8192:
            return False
        service = cas.get_service()
        cfg = service.config()
        _, current = self._session_state(service)
        if config_state(cfg) != "ok" or not current or not current.get("user_id"):
            return False
        if urlsplit(cfg.supabase_url).hostname != "127.0.0.1" and runtime_mode.is_fixture_mode():
            return False
        try:
            resp = requests.get(f"{cfg.supabase_url}/auth/v1/user", timeout=10, allow_redirects=False,
                                headers={"apikey": cfg.publishable_key, "Authorization": f"Bearer {token}"})
            if resp.status_code != 200:
                return False
            user = resp.json()
        except (requests.RequestException, ValueError):
            return False
        return isinstance(user, dict) and user.get("id") == current.get("user_id") and not user.get("is_anonymous")

    # -- 화면·API 요약 -----------------------------------------------------------------------------------
    def status_view(self) -> dict:
        """토큰·사용자 UUID·연결 비밀 없는 요약."""
        result = self.evaluate()
        with self._lock:
            status = self._status or {}
        consent = status.get("consent") or {}
        return {"state": result["state"], "can_enter": result["can_enter"], "reasons": result["reasons"],
                "verified_age": result["verified_age"], "policy_version": REQUIRED_POLICY_VERSION,
                "consent": {"state": consent.get("state"), "granted_at": consent.get("granted_at"),
                            "policy_version": consent.get("policy_version")},
                "kakao": bool((status.get("gate") or {}).get("kakao")),
                "contributor": (status.get("contributor") or {}).get("status"),
                "account": status.get("account"), "projection": status.get("projection"),
                "writer": self._writer_note or ({"code": "ok"} if result["can_enter"] else None),
                "deletion": _deletion_state()}

    def current_grant_id(self) -> str | None:
        with self._lock:
            return ((self._status or {}).get("consent") or {}).get("grant_id")

    def _reset_for_tests(self) -> None:
        with self._lock:
            self._status, self._verified_at, self._invalidated = None, None, True
            self._last_state, self._writer_note, self._last_error = None, None, None
            self._takeover_requested = False
            self._last_attempt = None
            self._listeners.clear()


_gate = _Gate()


def evaluate() -> dict:
    return _gate.evaluate()


def refresh_now() -> dict:
    return _gate.refresh_now()


def require_fresh(max_age: float = FRESH_SECONDS) -> dict:
    return _gate.require_fresh(max_age)


def check_for_request() -> dict:
    return _gate.check_for_request()


def invalidate(reason: str) -> None:
    _gate.invalidate(reason)


def on_change(callback: Callable[[dict], None]) -> None:
    _gate.on_change(callback)


def verify_client_user_token(token: str | None) -> bool:
    return _gate.verify_client_user_token(token)


def request_takeover() -> dict:
    return _gate.request_takeover()


def status_view() -> dict:
    return _gate.status_view()


def _deletion_state() -> str | None:
    try:
        from services import community_capture

        return community_capture.deletion_state()
    except Exception:
        return None


def current_grant_id() -> str | None:
    return _gate.current_grant_id()


ONBOARDING_REQUIRED = "COMMUNITY_ONBOARDING_REQUIRED"
REBUILD_REQUIRED = "COMMUNITY_REBUILD_REQUIRED"
BLOCK_MESSAGES = {
    ONBOARDING_REQUIRED: "커뮤니티 필수 설정(카카오 인증·공유 동의)을 먼저 마쳐 주세요.",
    REBUILD_REQUIRED: "초기화 크롤링이 필요하거나 진행 중입니다. 설정의 초기화 크롤링 화면에서 확인해 주세요.",
}


def crawl_block() -> tuple[int, str] | None:
    """크롤 시작(수동·예약·큐) 전: 게이트 60초 이내 재검증 → 초기화 필요·진행 중이면 막는다. 통과면 None."""
    if not require_fresh(FRESH_SECONDS)["can_enter"]:
        return 403, ONBOARDING_REQUIRED
    from services import community_rebuild

    if community_rebuild.required() or community_rebuild.blocking_state():
        return 409, REBUILD_REQUIRED
    return None


def block_code(exc: BaseException) -> str | None:
    """crawl_control 이 RuntimeError(code) 로 막은 경우 그 코드."""
    code = str(exc)
    return code if code in BLOCK_MESSAGES else None


def consent_text() -> str:
    from core.utils.path_utils import resource_path

    with open(resource_path(CONSENT_TEXT_FILE), "r", encoding="utf-8") as fh:
        return fh.read()
