"""커뮤니티 계정(카카오 via Supabase Auth) 기기 연결 서비스 — PC/Docker 쪽.

흐름(정본 protocol.md §7): 이 서버가 PKCE verifier·device_secret·delivery_key 를 만들어 암호화 저장 →
중계 `requests` → 관리자 화면에 비교코드·1회용 연결 링크 → 제한된 poll 스레드가 `code` 를 받으면 한 번만 교환 →
`/auth/v1/user` 로 계정 확인 → "계정 확인 필요" → 관리자가 확정하면 현재 세션으로 원자 교체 후 `complete`.

- 중앙 페이지는 이 서버에 접속하지 않는다. 중계 poll·코드 교환은 이 프로세스가 한다.
- 토큰·verifier·비밀값·연결 링크는 data.db·config.ini·로그·URL 에 넣지 않는다(services/community_auth_store.py).
- 업로드는 아직 없다. 향후 업로더는 is_upload_allowed() 와 get_access_token() 만 쓴다.
"""
from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import random
import re
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlsplit

from core.utils import runtime_mode
from services.community_auth_client import (
    AUTH_CODE_RE, DISPLAY_CODE_RE, UUID_RE, AuthError, CommunityAuthClient, CommunityHttpError, RelayError,
    jwt_claims_unverified, normalize_device_label, parse_bootstrap_url, pkce_challenge,
)
from services.community_auth_store import CommunitySessionStore, StoreUnreadable, random_b64url

_log = logging.getLogger("safetyreport.core.community_auth")

SECTION = "COMMUNITY"
DEFAULT_SITE_URL = "https://worklazy.net/safeauth/"
ENV_ENABLED = "SAFETYREPORT_COMMUNITY_ENABLED"
ENV_SUPABASE_URL = "SAFETYREPORT_COMMUNITY_SUPABASE_URL"
ENV_PUBLISHABLE_KEY = "SAFETYREPORT_COMMUNITY_PUBLISHABLE_KEY"
ENV_SITE_URL = "SAFETYREPORT_COMMUNITY_SITE_URL"

REFRESH_MARGIN_SECONDS = 60
DEFAULT_POLL_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 30.0
COMPLETE_ATTEMPTS = 3
FALLBACK_DISPLAY_NAME = "카카오 사용자"
LOCAL_PHASES = ("created", "claimed", "oauth_started", "exchanging", "confirm_required")
_TRUE = {"1", "true", "yes", "on"}

MESSAGES = {
    "community_disabled": "커뮤니티 계정 연결이 꺼져 있습니다. 설정에서 켜 주세요.",
    "community_unconfigured": "커뮤니티 계정 연결에 필요한 운영 설정(Supabase 주소·공개 키)이 없거나 올바르지 않습니다.",
    "fixture_blocked": "테스트(fixture) 모드에서는 외부 인증 서버에 연결하지 않습니다.",
    "no_pending": "진행 중인 연결 요청이 없습니다.",
    "request_mismatch": "다른 연결 요청입니다. 화면을 새로고침해 주세요.",
    "invalid_state": "지금 상태에서는 이 동작을 할 수 없습니다. 화면을 새로고침해 주세요.",
    "expired": "연결 요청이 만료되었습니다. 다시 시작해 주세요.",
    "rate_limited": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
    "relay_unavailable": "중앙 연결 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요.",
    "relay_rejected": "중앙 연결 서비스가 요청을 거부했습니다. 새로 연결을 시작해 주세요.",
    "invalid_label": "기기 이름은 1~40자이며 < > \" ' ` \\ 와 주소 형식은 쓸 수 없습니다.",
    "store_unreadable": "저장된 커뮤니티 로그인 정보를 읽을 수 없습니다(키 파일 분실 또는 손상). '연결 해제'로 초기화한 뒤 다시 연결해 주세요.",
    "not_connected": "커뮤니티 계정이 연결되어 있지 않습니다.",
    "reauth_required": "커뮤니티 로그인이 만료되었거나 해제되었습니다. 다시 연결해 주세요.",
    "auth_unavailable": "인증 서버에 일시적으로 연결할 수 없습니다. 연결 정보는 그대로 둡니다.",
    "exchange_failed": "로그인 결과를 이 서버의 세션으로 바꾸지 못했습니다. 새로 연결을 시작해 주세요.",
    "user_lookup_failed": "로그인한 계정 정보를 확인하지 못했습니다. 새로 연결을 시작해 주세요.",
    "complete_failed": "이 서버에는 연결됐지만 중앙 페이지에 완료 표시를 하지 못했습니다. 중앙 페이지는 닫아도 됩니다.",
    "code_expired": "로그인 결과의 유효 시간이 지났습니다. 새로 연결을 시작해 주세요.",
    "cancelled": "연결 요청이 취소되었습니다.",
    "failed": "중앙 페이지에서 로그인이 끝나지 않았습니다(거부 또는 오류). 새로 연결을 시작해 주세요.",
    "interrupted": "서버가 재시작되어 진행 중이던 연결을 마치지 못했습니다. 새로 연결을 시작해 주세요.",
    "already_completed": "이미 끝난 연결 요청입니다.",
    "invalid_response": "중앙 연결 서비스 응답이 올바르지 않습니다.",
    "permission_required": "서버 관리자 화면에서 이 기기의 커뮤니티 계정 관리 권한을 허용해야 합니다.",
    "invalid_settings": "설정 값이 올바르지 않습니다.",
    "internal_error": "알 수 없는 오류가 발생했습니다. 새로 연결을 시작해 주세요.",
}

HTTP_STATUS = {
    "community_disabled": 503, "community_unconfigured": 503, "fixture_blocked": 503,
    "no_pending": 409, "request_mismatch": 409, "invalid_state": 409, "store_unreadable": 409,
    "not_connected": 409, "reauth_required": 409,
    "expired": 410, "rate_limited": 429,
    "relay_unavailable": 502, "relay_rejected": 502, "auth_unavailable": 502,
    "invalid_label": 400, "invalid_settings": 400, "permission_required": 403,
}


class CommunityAuthError(RuntimeError):
    """로컬 API 로 그대로 내보낼 수 있는 오류(코드·HTTP 상태·한국어 문구)."""

    def __init__(self, code: str, message: str | None = None, retry_after: float | None = None):
        super().__init__(code)
        self.code = code
        self.status = HTTP_STATUS.get(code, 500)
        self.message = message or MESSAGES.get(code, MESSAGES["internal_error"])
        self.retry_after = retry_after


def _error(code: str) -> dict:
    return {"code": code, "message": MESSAGES.get(code, MESSAGES["internal_error"])}


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_ts(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


# ── 설정 ──────────────────────────────────────────────────────────────────────

def normalize_supabase_url(value) -> str | None:
    """https origin 만. http 는 127.0.0.1(로컬 검증 스택)만. 경로·쿼리·계정정보 금지."""
    value = (value or "").strip() if isinstance(value, str) else ""
    if not value:
        return None
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - 잘못된 포트면 ValueError
    except ValueError:
        return None
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        return None
    if not parts.hostname:
        return None
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname == "127.0.0.1"):
        return f"{parts.scheme}://{parts.netloc}"
    return None


def normalize_site_url(value) -> str | None:
    value = (value or "").strip() if isinstance(value, str) else ""
    if not value:
        return None
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018
    except ValueError:
        return None
    if parts.username or parts.password or parts.query or parts.fragment or not parts.path.endswith("/"):
        return None
    if not parts.hostname:
        return None
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname == "127.0.0.1"):
        return f"{parts.scheme}://{parts.netloc}{parts.path}"
    return None


def validate_publishable_key(value) -> str | None:
    """공개(publishable/anon) 키만. sb_secret_·service_role 등 비밀 키는 거부."""
    value = (value or "").strip() if isinstance(value, str) else ""
    if not value or len(value) > 2048 or value.startswith("sb_secret_"):
        return None
    if re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{10,200}", value):
        return value
    if re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", value):
        return value if jwt_claims_unverified(value).get("role") == "anon" else None
    return None


@dataclasses.dataclass(frozen=True)
class CommunityConfig:
    enabled: bool = False
    supabase_url: str = ""
    publishable_key: str = ""
    site_url: str = DEFAULT_SITE_URL
    device_label: str = ""
    api_key_managers: frozenset = frozenset()
    upload_enabled: bool = False
    problems: tuple = ()
    env_locked: frozenset = frozenset()

    @property
    def configured(self) -> bool:
        return bool(self.supabase_url and self.publishable_key and self.site_url) and not self.problems


def build_config(raw: dict, env: dict | None = None) -> CommunityConfig:
    """config.ini [COMMUNITY] 값(raw) + 환경변수 우선값으로 검증된 설정을 만든다."""
    env = os.environ if env is None else env
    locked = set()

    def pick(key: str, env_name: str, default: str = "") -> str:
        env_value = env.get(env_name)
        if env_value is not None and env_value.strip() != "":
            locked.add(key)
            return env_value.strip()
        value = raw.get(key)
        return default if value is None else str(value).strip()

    problems = []
    enabled = pick("enabled", ENV_ENABLED, "false").lower() in _TRUE
    url_raw = pick("supabase_url", ENV_SUPABASE_URL)
    key_raw = pick("publishable_key", ENV_PUBLISHABLE_KEY)
    site_raw = pick("site_url", ENV_SITE_URL, DEFAULT_SITE_URL) or DEFAULT_SITE_URL
    url = normalize_supabase_url(url_raw) or ""
    if url_raw and not url:
        problems.append("supabase_url")
    key = validate_publishable_key(key_raw) or ""
    if key_raw and not key:
        problems.append("publishable_key")
    site = normalize_site_url(site_raw) or ""
    if not site:
        problems.append("site_url")
    label = normalize_device_label(str(raw.get("device_label") or "")) or ""
    managers = frozenset(
        h.strip().lower() for h in str(raw.get("api_key_managers") or "").split(",")
        if re.fullmatch(r"[0-9a-fA-F]{64}", h.strip())
    )
    upload = str(raw.get("upload_enabled") or "false").strip().lower() in _TRUE
    return CommunityConfig(enabled=enabled, supabase_url=url, publishable_key=key, site_url=site,
                           device_label=label, api_key_managers=managers, upload_enabled=upload,
                           problems=tuple(problems), env_locked=frozenset(locked))


def load_config_from_settings() -> CommunityConfig:
    import settings.settings as app_settings

    cfg = app_settings._instance.config
    raw = dict(cfg.items(SECTION)) if cfg.has_section(SECTION) else {}
    return build_config(raw)


# ── 서비스 ────────────────────────────────────────────────────────────────────

class CommunityAuthService:
    def __init__(self, datapath: str, config_provider=load_config_from_settings, client_factory=None, *,
                 dockerenv_path: str = "/.dockerenv", clock=time.time,
                 poll_interval_override: float | None = None, retry_base_seconds: float = 1.0):
        self.store = CommunitySessionStore(datapath)
        self._config_provider = config_provider
        self._client_factory = client_factory or (lambda cfg: CommunityAuthClient(cfg.supabase_url, cfg.publishable_key))
        self._dockerenv_path = dockerenv_path
        self._clock = clock
        self.poll_interval_override = poll_interval_override
        self.retry_base_seconds = retry_base_seconds
        self._workers: dict[str, tuple[threading.Thread, threading.Event]] = {}
        self._workers_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._shutdown = threading.Event()

    # 기본 도우미 -----------------------------------------------------------
    def config(self) -> CommunityConfig:
        return self._config_provider()

    def _now(self) -> float:
        return float(self._clock())

    def default_client_kind(self) -> str:
        return "docker" if os.path.exists(self._dockerenv_path) else "pc"

    def default_device_label(self, cfg: CommunityConfig | None = None) -> str:
        cfg = cfg or self.config()
        return cfg.device_label or ("Docker 서버" if self.default_client_kind() == "docker" else "이 PC")

    @staticmethod
    def _require_ready(cfg: CommunityConfig) -> None:
        if not cfg.enabled:
            raise CommunityAuthError("community_disabled")
        if not cfg.configured:
            raise CommunityAuthError("community_unconfigured")

    def _client(self, cfg: CommunityConfig):
        if urlsplit(cfg.supabase_url).hostname != "127.0.0.1":
            try:
                runtime_mode.block_if_fixture("community auth network")
            except runtime_mode.ExternalSideEffectBlocked:
                raise CommunityAuthError("fixture_blocked") from None
        return self._client_factory(cfg)

    def _load(self) -> dict:
        try:
            return self.store.load()
        except StoreUnreadable:
            raise CommunityAuthError("store_unreadable") from None

    @staticmethod
    def _relay_failure(exc: CommunityHttpError) -> CommunityAuthError:
        if exc.code in ("rate_limited", "too_many_pending") or exc.status == 429:
            return CommunityAuthError("rate_limited", retry_after=exc.retry_after)
        if exc.code == "expired":
            return CommunityAuthError("expired")
        if exc.transient or exc.code in ("service_disabled", "config_missing", "capacity"):
            return CommunityAuthError("relay_unavailable")
        return CommunityAuthError("relay_rejected")

    # 상태 ------------------------------------------------------------------
    def status(self, *, can_manage: bool) -> dict:
        cfg = self.config()
        self._expire_pending_if_needed()
        dto = {"state": "disconnected", "can_manage": bool(can_manage), "pending": None, "candidate": None,
               "account": None, "last_error": None, "upload_enabled": False}
        try:
            st = self.store.load()
        except StoreUnreadable:
            dto["state"] = "store_unreadable"
            dto["last_error"] = _error("store_unreadable")
            return dto
        pending = st.get("pending") if (st.get("pending") or {}).get("request_id") else None
        current, reauth = st.get("current"), st.get("reauth")
        if not cfg.enabled:
            dto["state"] = "disabled"
        elif not cfg.configured:
            dto["state"] = "unconfigured"
        elif pending:
            dto["state"] = "confirm_required" if pending.get("phase") == "confirm_required" else "pending"
        elif current:
            dto["state"] = "connected"
        elif reauth:
            dto["state"] = "reauth_required"
        if pending:
            dto["pending"] = {
                "request_id": pending["request_id"],
                "display_code": pending.get("display_code"),
                "expires_at": pending.get("expires_at"),
                "phase": pending.get("phase") if pending.get("phase") in LOCAL_PHASES else "created",
            }
            if can_manage:
                dto["pending"]["bootstrap_url"] = pending.get("bootstrap_url")
            cand = pending.get("candidate")
            if cand and pending.get("phase") == "confirm_required":
                existing = current or reauth
                dto["candidate"] = {
                    "request_id": pending["request_id"],
                    "display_name": cand.get("display_name") or FALLBACK_DISPLAY_NAME,
                    "has_email": bool(cand.get("has_email")),
                    "is_different_account": bool(existing and existing.get("user_id") != cand.get("user_id")),
                }
        if current:
            dto["account"] = {"display_name": current.get("display_name") or FALLBACK_DISPLAY_NAME,
                              "connected_at": current.get("connected_at"), "session_state": "valid"}
        elif reauth:
            dto["account"] = {"display_name": reauth.get("display_name") or FALLBACK_DISPLAY_NAME,
                              "connected_at": reauth.get("connected_at"), "session_state": "reauth_required"}
        if st.get("last_error"):
            code = st["last_error"].get("code")
            dto["last_error"] = _error(code if code in MESSAGES else "internal_error")
        dto["upload_enabled"] = bool(cfg.upload_enabled and current)
        return dto

    def is_connected(self) -> bool:
        try:
            return bool(self.store.load().get("current"))
        except StoreUnreadable:
            return False

    def is_upload_allowed(self) -> bool:
        """향후 업로더는 업로드마다 이것을 확인한다: 연결됨 + [COMMUNITY] upload_enabled."""
        cfg = self.config()
        return bool(cfg.enabled and cfg.configured and cfg.upload_enabled and self.is_connected())

    # 시작 ------------------------------------------------------------------
    def start(self, *, client_kind: str | None = None, device_label: str | None = None) -> dict:
        cfg = self.config()
        self._require_ready(cfg)
        if device_label is None or device_label == "":
            label = self.default_device_label(cfg)
        else:
            label = normalize_device_label(device_label)
            if not label:
                raise CommunityAuthError("invalid_label")
        kind = client_kind or self.default_client_kind()
        client = self._client(cfg)
        with self._start_lock:
            with self.store.locked():
                st = self._load()
                old = st.get("pending")
                verifier = random_b64url()
                new = {
                    "request_id": None, "code_verifier": verifier, "device_secret": random_b64url(),
                    "delivery_key": random_b64url(), "installation_id": self.store.installation_id(),
                    "client_kind": kind, "device_label": label, "display_code": None, "bootstrap_url": None,
                    "expires_at": None, "expires_ts": None, "phase": "created", "candidate": None,
                    "code_consumed": False, "created_ts": self._now(), "poll_interval": DEFAULT_POLL_SECONDS,
                }
                st["pending"] = new
                st["last_error"] = None
                self.store.save(st)  # verifier 를 중계 호출 전에 저장(프로세스가 죽어도 남도록)
            if old:
                self._stop_worker(old.get("request_id"))
                self._discard_remote(cfg, old, cancel_relay=bool(old.get("request_id")))
            try:
                resp = self._create_request(client, new)
                request_id, bootstrap_url = self._validate_created(cfg, resp)
            except (CommunityHttpError, CommunityAuthError) as exc:
                self._drop_new_pending(new["device_secret"])
                if isinstance(exc, CommunityAuthError):
                    raise
                _log.warning("[community] 연결 요청 생성 실패: %s", exc.code)
                raise self._relay_failure(exc) from None
            with self.store.locked():
                st = self._load()
                p = st.get("pending")
                if not p or p.get("device_secret") != new["device_secret"]:
                    lost = True
                else:
                    lost = False
                    p.update({
                        "request_id": request_id, "bootstrap_url": bootstrap_url,
                        "display_code": resp["display_code"], "expires_at": resp["expires_at"],
                        "expires_ts": _parse_ts(resp["expires_at"]),
                        "poll_interval": float(resp.get("poll_interval_seconds") or DEFAULT_POLL_SECONDS),
                    })
                    self.store.save(st)
            if lost:  # 그 사이 취소됨
                self._safe_relay_cancel(cfg, request_id, new["device_secret"])
                raise CommunityAuthError("invalid_state")
            _log.info("[community] 연결 요청을 만들었습니다(%s).", kind)
            self._start_worker(request_id)
        return self.status(can_manage=True)

    def _create_request(self, client, pending: dict) -> dict:
        body = dict(client_kind=pending["client_kind"], device_label=pending["device_label"],
                    code_challenge=pkce_challenge(pending["code_verifier"]), device_secret=pending["device_secret"],
                    installation_id=pending["installation_id"])
        try:
            return client.create_request(**body)
        except RelayError as exc:
            if exc.code != "network_error":
                raise
        # 같은 device_secret 재시도는 같은 요청(멱등, 새 ticket) — 응답 유실 대비 1회만
        time.sleep(min(1.0, self.retry_base_seconds))
        return client.create_request(**body)

    def _validate_created(self, cfg: CommunityConfig, resp: dict) -> tuple[str, str]:
        request_id = resp.get("request_id")
        if not isinstance(request_id, str) or not UUID_RE.match(request_id):
            raise CommunityAuthError("relay_rejected", MESSAGES["invalid_response"])
        parsed = parse_bootstrap_url(resp.get("bootstrap_url"), cfg.site_url)
        valid = (parsed is not None and parsed[0] == request_id
                 and isinstance(resp.get("display_code"), str) and DISPLAY_CODE_RE.match(resp["display_code"])
                 and _parse_ts(resp.get("expires_at")) is not None)
        if not valid:
            raise CommunityAuthError("relay_rejected", MESSAGES["invalid_response"])
        return request_id, resp["bootstrap_url"]

    def _drop_new_pending(self, device_secret: str) -> None:
        try:
            with self.store.locked():
                st = self.store.load()
                p = st.get("pending")
                if p and p.get("device_secret") == device_secret:
                    st["pending"] = None
                    self.store.save(st)
        except StoreUnreadable:
            pass

    # poll 스레드 -----------------------------------------------------------
    def _start_worker(self, request_id: str) -> None:
        if self._shutdown.is_set():
            return
        with self._workers_lock:
            existing = self._workers.get(request_id)
            if existing and existing[0].is_alive():
                return
            stop = threading.Event()
            thread = threading.Thread(target=self._poll_loop, args=(request_id, stop),
                                      name="community-auth-poll", daemon=True)
            self._workers[request_id] = (thread, stop)
            thread.start()

    def _stop_worker(self, request_id: str | None, join: bool = False) -> None:
        if not request_id:
            return
        with self._workers_lock:
            entry = self._workers.pop(request_id, None)
        if entry:
            entry[1].set()
            if join and entry[0] is not threading.current_thread():
                entry[0].join(timeout=5)

    def active_workers(self) -> int:
        with self._workers_lock:
            return sum(1 for t, _ in self._workers.values() if t.is_alive())

    def _interval(self, base: float) -> float:
        if self.poll_interval_override is not None:
            base = self.poll_interval_override
        return max(0.01, base * random.uniform(0.8, 1.2))

    def _poll_snapshot(self, request_id: str) -> dict | None:
        try:
            p = self.store.load().get("pending")
        except StoreUnreadable:
            return None
        if not p or p.get("request_id") != request_id or p.get("code_consumed"):
            return None
        if p.get("phase") not in ("created", "claimed", "oauth_started"):
            return None
        return p

    def _poll_loop(self, request_id: str, stop: threading.Event) -> None:
        backoff = 0.0
        try:
            while not stop.is_set() and not self._shutdown.is_set():
                p = self._poll_snapshot(request_id)
                if p is None:
                    return
                if p.get("expires_ts") and self._now() >= p["expires_ts"]:
                    self._fail_pending(request_id, "expired")
                    return
                cfg = self.config()
                if not cfg.enabled or not cfg.configured:
                    return
                interval = p.get("poll_interval") or DEFAULT_POLL_SECONDS
                try:
                    client = self._client(cfg)
                    resp = client.poll(request_id=request_id, device_secret=p["device_secret"],
                                       delivery_key=p["delivery_key"])
                except CommunityAuthError as exc:
                    self._fail_pending(request_id, exc.code)
                    return
                except RelayError as exc:
                    if exc.status == 429 or exc.code == "rate_limited":
                        floor = self._interval(interval)
                        stop.wait(max(floor, exc.retry_after) if exc.retry_after is not None else floor)
                    elif exc.transient:
                        backoff = min(MAX_BACKOFF_SECONDS, backoff * 2 if backoff else self._interval(interval))
                        stop.wait(backoff)
                    else:
                        code = exc.code if exc.code in ("expired", "cancelled", "failed") else "relay_rejected"
                        self._fail_pending(request_id, code)
                        return
                    continue
                backoff = 0.0
                status = resp.get("status")
                if status == "pending":
                    phase = resp.get("phase")
                    if phase in ("created", "claimed", "oauth_started") and phase != p.get("phase"):
                        self._set_phase(request_id, phase)
                    wait = resp.get("poll_after_seconds")
                    base = float(wait) if isinstance(wait, (int, float)) and 1 <= wait <= 60 else interval
                    stop.wait(self._interval(base))
                elif status == "code":
                    code = resp.get("auth_code")
                    if not isinstance(code, str) or not AUTH_CODE_RE.match(code):
                        self._fail_pending(request_id, "invalid_response", cancel_relay=True)
                    else:
                        self._handle_code(request_id, code)
                    return
                elif status in ("expired", "cancelled", "failed", "code_expired"):
                    self._fail_pending(request_id, status)
                    return
                elif status == "completed":
                    self._fail_pending(request_id, "already_completed")
                    return
                else:
                    self._fail_pending(request_id, "invalid_response", cancel_relay=True)
                    return
        except Exception as exc:  # 스레드가 조용히 죽지 않게. 비밀값이 섞일 수 있는 메시지는 남기지 않는다.
            _log.warning("[community] poll 스레드 오류: %s", type(exc).__name__)
            self._fail_pending(request_id, "internal_error", cancel_relay=True)
        finally:
            with self._workers_lock:
                entry = self._workers.get(request_id)
                if entry and entry[0] is threading.current_thread():
                    self._workers.pop(request_id, None)

    def _set_phase(self, request_id: str, phase: str) -> None:
        with self.store.locked():
            st = self.store.load()
            p = st.get("pending")
            if p and p.get("request_id") == request_id and p.get("phase") in ("created", "claimed", "oauth_started"):
                p["phase"] = phase
                self.store.save(st)

    def _handle_code(self, request_id: str, auth_code: str) -> None:
        with self.store.locked():
            st = self.store.load()
            p = st.get("pending")
            if not p or p.get("request_id") != request_id or p.get("code_consumed"):
                return
            p["code_consumed"] = True  # 이 코드는 다시 교환하지 않는다(재시작 뒤에도)
            p["phase"] = "exchanging"
            self.store.save(st)
            verifier = p["code_verifier"]
        cfg = self.config()
        try:
            client = self._client(cfg)
            session = client.exchange_code(auth_code=auth_code, code_verifier=verifier)
        except (CommunityHttpError, CommunityAuthError) as exc:
            _log.warning("[community] 코드 교환 실패: %s", getattr(exc, "code", "error"))
            self._fail_pending(request_id, "exchange_failed", cancel_relay=True)
            return
        try:
            user = client.get_user(access_token=session["access_token"])
            record = self._session_record(session, user)
        except (CommunityHttpError, ValueError) as exc:
            _log.warning("[community] 계정 확인 실패: %s", getattr(exc, "code", "error"))
            self._logout_quietly(cfg, {"access_token": session.get("access_token"),
                                       "refresh_token": session.get("refresh_token"),
                                       "expires_at": self._now() + 300})
            self._fail_pending(request_id, "user_lookup_failed", cancel_relay=True)
            return
        with self.store.locked():
            st = self.store.load()
            p = st.get("pending")
            orphan = not p or p.get("request_id") != request_id
            if not orphan:
                p["candidate"] = record
                p["phase"] = "confirm_required"
                self.store.save(st)
        if orphan:  # 그 사이 취소·새 요청 → 방금 만든 세션은 버린다
            self._logout_quietly(cfg, record)
            return
        _log.info("[community] 로그인 결과를 받았습니다. 관리자 확인을 기다립니다.")

    def _session_record(self, session: dict, user: dict) -> dict:
        user_id = user.get("id")
        token_user = (session.get("user") or {}).get("id") if isinstance(session.get("user"), dict) else None
        if not isinstance(user_id, str) or (token_user and token_user != user_id):
            raise ValueError("user mismatch")
        now = self._now()
        expires_at = session.get("expires_at")
        if not isinstance(expires_at, (int, float)):
            expires_in = session.get("expires_in")
            expires_at = now + (float(expires_in) if isinstance(expires_in, (int, float)) else 3600.0)
        claims = jwt_claims_unverified(session["access_token"])
        return {
            "access_token": session["access_token"], "refresh_token": session["refresh_token"],
            "expires_at": float(expires_at), "user_id": user_id,
            "display_name": _display_name(user), "has_email": bool(user.get("email")),
            "session_id": claims.get("session_id") if isinstance(claims.get("session_id"), str) else None,
            "connected_at": None,
        }

    def _fail_pending(self, request_id: str, code: str, cancel_relay: bool = False) -> None:
        try:
            with self.store.locked():
                st = self.store.load()
                p = st.get("pending")
                if not p or p.get("request_id") != request_id:
                    return
                st["pending"] = None
                st["last_error"] = {"code": code if code in MESSAGES else "internal_error", "at": _iso(self._now())}
                self.store.save(st)
        except StoreUnreadable:
            return
        self._stop_worker(request_id)
        _log.info("[community] 연결 요청 종료: %s", code)
        try:
            cfg = self.config()
        except Exception:
            return
        self._discard_remote(cfg, p, cancel_relay=cancel_relay)

    def _expire_pending_if_needed(self) -> None:
        try:
            p = self.store.load().get("pending")
        except StoreUnreadable:
            return
        if p and p.get("request_id") and p.get("expires_ts") and self._now() >= p["expires_ts"]:
            self._fail_pending(p["request_id"], "expired")

    # 확정 ------------------------------------------------------------------
    def confirm(self, request_id: str) -> dict:
        cfg = self.config()
        self._require_ready(cfg)
        if not isinstance(request_id, str) or not UUID_RE.match(request_id):
            raise CommunityAuthError("request_mismatch")
        expired_pending = None
        with self.store.locked():
            st = self._load()
            p = st.get("pending")
            if not p or not p.get("request_id"):
                raise CommunityAuthError("no_pending")
            if p["request_id"] != request_id:
                raise CommunityAuthError("request_mismatch")
            if p.get("phase") != "confirm_required" or not p.get("candidate"):
                raise CommunityAuthError("invalid_state")
            if p.get("expires_ts") and self._now() >= p["expires_ts"]:
                expired_pending = p
            else:
                old = st.get("current")
                candidate = dict(p["candidate"])
                candidate["connected_at"] = _iso(self._now())
                st["current"] = candidate
                st["pending"] = None
                st["reauth"] = None
                st["last_error"] = None
                self.store.save(st)  # 저장이 끝난 뒤에만 complete 를 부른다
                device_secret = p["device_secret"]
        if expired_pending is not None:
            self._fail_pending(request_id, "expired")
            raise CommunityAuthError("expired")
        self._stop_worker(request_id)
        _log.info("[community] 커뮤니티 계정을 이 서버에 연결했습니다.")
        completed = self._complete_with_retry(cfg, request_id, device_secret, candidate["access_token"])
        if not completed:
            with self.store.locked():
                st = self._load()
                st["last_error"] = {"code": "complete_failed", "at": _iso(self._now())}
                self.store.save(st)
        if old and old.get("session_id") != candidate.get("session_id"):
            self._logout_quietly(cfg, old)  # 교체된 이전 세션은 이 세션만 끝낸다(best-effort)
        return self.status(can_manage=True)

    def _complete_with_retry(self, cfg, request_id: str, device_secret: str, access_token: str) -> bool:
        delay = self.retry_base_seconds
        for attempt in range(COMPLETE_ATTEMPTS):
            try:
                self._client(cfg).complete(request_id=request_id, device_secret=device_secret,
                                           access_token=access_token)
                return True
            except CommunityAuthError:
                return False
            except RelayError as exc:
                if exc.code == "already_completed":
                    return True
                if not exc.transient or attempt == COMPLETE_ATTEMPTS - 1:
                    _log.warning("[community] complete 실패: %s", exc.code)
                    return False
                time.sleep(min(MAX_BACKOFF_SECONDS, delay))
                delay *= 2
        return False

    # 취소 ------------------------------------------------------------------
    def cancel(self, request_id: str | None = None) -> dict:
        cfg = self.config()
        with self.store.locked():
            st = self._load()
            p = st.get("pending")
            if not p:
                raise CommunityAuthError("no_pending")
            if request_id and p.get("request_id") != request_id:
                raise CommunityAuthError("request_mismatch")
            st["pending"] = None
            self.store.save(st)  # 기존 연결(current)은 건드리지 않는다
        self._stop_worker(p.get("request_id"))
        self._discard_remote(cfg, p, cancel_relay=bool(p.get("request_id")))
        _log.info("[community] 연결 요청을 취소했습니다.")
        return self.status(can_manage=True)

    def _discard_remote(self, cfg: CommunityConfig, pending: dict, cancel_relay: bool) -> None:
        """대기 요청의 원격 정리(best-effort): 중계 cancel, 받아 둔 후보 세션 logout(scope=local)."""
        if not cfg.configured:
            return
        if cancel_relay and pending.get("request_id"):
            self._safe_relay_cancel(cfg, pending["request_id"], pending.get("device_secret"))
        if pending.get("candidate"):
            self._logout_quietly(cfg, pending["candidate"])

    def _safe_relay_cancel(self, cfg, request_id: str, device_secret: str | None) -> None:
        if not device_secret:
            return
        try:
            self._client(cfg).cancel(request_id=request_id, device_secret=device_secret)
        except (CommunityHttpError, CommunityAuthError) as exc:
            _log.info("[community] 중계 취소 실패(무시): %s", exc.code)

    # 연결 해제 ---------------------------------------------------------------
    def disconnect(self) -> dict:
        """이 서버의 커뮤니티 로그인을 지운다. 신고 데이터 등 다른 것은 건드리지 않는다."""
        cfg = self.config()
        with self.store.locked():
            try:
                st = self.store.load()
            except StoreUnreadable:
                self.store.reset_unreadable()
                return {"server_logout": False, "reset_unreadable": True}
            current, pending = st.get("current"), st.get("pending")
            self.store.save({"current": None, "pending": None, "reauth": None, "last_error": None})
        if pending:
            self._stop_worker(pending.get("request_id"))
            self._discard_remote(cfg, pending, cancel_relay=bool(pending.get("request_id")))
        server_logout = False
        if current and cfg.configured:
            server_logout = self._logout_quietly(cfg, current)
        _log.info("[community] 커뮤니티 계정 연결을 해제했습니다(서버 로그아웃 확인: %s).", server_logout)
        return {"server_logout": server_logout, "reset_unreadable": False}

    def _logout_quietly(self, cfg: CommunityConfig, record: dict) -> bool:
        """scope=local 로 그 세션만 끝낸다. access 가 만료됐으면 먼저 refresh, refresh 도 실패하면 건너뛴다."""
        access = record.get("access_token")
        try:
            client = self._client(cfg)
            if not access or float(record.get("expires_at") or 0) - self._now() <= 5:
                if not record.get("refresh_token"):
                    return False
                access = client.refresh(refresh_token=record["refresh_token"])["access_token"]
            return bool(client.logout_local(access_token=access))
        except (CommunityHttpError, CommunityAuthError, KeyError) as exc:
            _log.info("[community] 세션 로그아웃 실패(무시): %s", getattr(exc, "code", type(exc).__name__))
            return False

    # 세션 공급 ---------------------------------------------------------------
    def get_access_token(self) -> str:
        """업로더용: 유효한 access token. 60초 안에 만료되면 락 안에서 한 번만 refresh 한다."""
        cfg = self.config()
        self._require_ready(cfg)
        with self.store.locked():
            st = self._load()  # 락을 잡은 뒤 다시 읽는다 → 다른 호출자가 이미 갱신했으면 그 값을 쓴다
            cur = st.get("current")
            if not cur:
                raise CommunityAuthError("reauth_required" if st.get("reauth") else "not_connected")
            if float(cur.get("expires_at") or 0) - self._now() > REFRESH_MARGIN_SECONDS:
                return cur["access_token"]
            try:
                session = self._client(cfg).refresh(refresh_token=cur["refresh_token"])
            except AuthError as exc:
                if exc.reauth_required:
                    st["reauth"] = {k: cur.get(k) for k in ("display_name", "connected_at", "has_email", "user_id")}
                    st["current"] = None
                    st["last_error"] = {"code": "reauth_required", "at": _iso(self._now())}
                    self.store.save(st)
                    _log.warning("[community] refresh 토큰이 거부되어 다시 로그인이 필요합니다: %s", exc.code)
                    raise CommunityAuthError("reauth_required") from None
                raise CommunityAuthError("auth_unavailable") from None
            except CommunityHttpError:
                raise CommunityAuthError("auth_unavailable") from None
            now = self._now()
            expires_at = session.get("expires_at")
            if not isinstance(expires_at, (int, float)):
                expires_in = session.get("expires_in")
                expires_at = now + (float(expires_in) if isinstance(expires_in, (int, float)) else 3600.0)
            claims = jwt_claims_unverified(session["access_token"])
            cur.update({"access_token": session["access_token"], "refresh_token": session["refresh_token"],
                        "expires_at": float(expires_at), "refreshed_at": _iso(now)})
            if isinstance(claims.get("session_id"), str):
                cur["session_id"] = claims["session_id"]
            self.store.save(st)  # access + refresh 를 함께 원자 저장
            return cur["access_token"]

    # 수명주기 ---------------------------------------------------------------
    def resume(self) -> None:
        """서버 시작 시: 만료 전 대기 요청이 있으면 poll 을 다시 시작한다. 그 밖에는 아무것도 하지 않는다."""
        cfg = self.config()
        if not cfg.enabled or not cfg.configured:
            return
        try:
            st = self.store.load()
        except StoreUnreadable:
            _log.warning("[community] 커뮤니티 세션 파일을 읽을 수 없습니다. 관리자 화면에서 초기화가 필요합니다.")
            return
        p = st.get("pending")
        if not p:
            return
        if not p.get("request_id"):  # 중계 생성 결과를 모름 → 버린다(중계 쪽은 만료로 정리)
            with self.store.locked():
                st = self.store.load()
                if st.get("pending") and not st["pending"].get("request_id"):
                    st["pending"] = None
                    self.store.save(st)
            return
        if p.get("expires_ts") and self._now() >= p["expires_ts"]:
            self._fail_pending(p["request_id"], "expired")
            return
        if p.get("phase") == "exchanging" or (p.get("code_consumed") and p.get("phase") != "confirm_required"):
            self._fail_pending(p["request_id"], "interrupted", cancel_relay=True)
            return
        if p.get("phase") == "confirm_required":
            return
        if urlsplit(cfg.supabase_url).hostname != "127.0.0.1" and runtime_mode.skip_in_fixture("community auth poll resume"):
            return
        self._start_worker(p["request_id"])

    def shutdown(self, timeout: float = 5.0) -> None:
        self._shutdown.set()
        with self._workers_lock:
            entries = list(self._workers.values())
            self._workers.clear()
        for _, stop in entries:
            stop.set()
        for thread, _ in entries:
            if thread is not threading.current_thread():
                thread.join(timeout=timeout)


def _display_name(user: dict) -> str:
    meta = user.get("user_metadata") if isinstance(user.get("user_metadata"), dict) else {}
    for key in ("name", "nickname", "preferred_username", "full_name"):
        value = meta.get(key)
        if isinstance(value, str):
            cleaned = re.sub(r"[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁦-⁩]", "", value)
            cleaned = re.sub(r"\s+", " ", cleaned).strip()
            if cleaned:
                return cleaned[:40]
    return FALLBACK_DISPLAY_NAME


# ── 모듈 기본 인스턴스 (앱 전체에서 하나) ───────────────────────────────────────

_default: CommunityAuthService | None = None
_default_lock = threading.Lock()


def get_service() -> CommunityAuthService:
    global _default
    with _default_lock:
        if _default is None:
            import settings.settings as app_settings

            _default = CommunityAuthService(app_settings.datapath)
        return _default


def resume_on_startup() -> None:
    get_service().resume()


def shutdown() -> None:
    if _default is not None:
        _default.shutdown()


def get_access_token() -> str:
    return get_service().get_access_token()


def is_upload_allowed() -> bool:
    return get_service().is_upload_allowed()


def update_settings(body: dict, known_key_hashes: set[str]) -> CommunityConfig:
    """관리자 화면 저장. 환경변수로 고정된 값은 바꾸지 않는다. 잘못된 값이면 CommunityAuthError(invalid_settings)."""
    import settings.settings as app_settings

    if not isinstance(body, dict):
        raise CommunityAuthError("invalid_settings")
    allowed = {"enabled", "supabase_url", "publishable_key", "device_label", "api_key_managers"}
    if set(body) - allowed:
        raise CommunityAuthError("invalid_settings")
    before = load_config_from_settings()
    updates: dict[str, str] = {}
    if "enabled" in body and "enabled" not in before.env_locked:
        if not isinstance(body["enabled"], bool):
            raise CommunityAuthError("invalid_settings")
        updates["enabled"] = "true" if body["enabled"] else "false"
    if "supabase_url" in body and "supabase_url" not in before.env_locked:
        raw = body["supabase_url"]
        if not isinstance(raw, str) or (raw.strip() and not normalize_supabase_url(raw)):
            raise CommunityAuthError("invalid_settings", "Supabase 주소는 https 주소여야 합니다(경로 없이).")
        updates["supabase_url"] = normalize_supabase_url(raw) or ""
    if "publishable_key" in body and "publishable_key" not in before.env_locked:
        raw = body["publishable_key"]
        if not isinstance(raw, str) or (raw.strip() and not validate_publishable_key(raw)):
            raise CommunityAuthError(
                "invalid_settings", "공개(publishable/anon) 키만 넣을 수 있습니다. sb_secret_·service_role 키는 넣지 마세요.")
        updates["publishable_key"] = raw.strip()
    if "device_label" in body:
        raw = body["device_label"]
        if not isinstance(raw, str) or (raw.strip() and not normalize_device_label(raw)):
            raise CommunityAuthError("invalid_label")
        updates["device_label"] = normalize_device_label(raw) or ""
    if "api_key_managers" in body:
        raw = body["api_key_managers"]
        if not isinstance(raw, list) or not all(isinstance(h, str) for h in raw):
            raise CommunityAuthError("invalid_settings")
        hashes = sorted({h.lower() for h in raw})
        if any(h not in known_key_hashes for h in hashes):
            raise CommunityAuthError("invalid_settings", "없는 API 키가 있습니다. 화면을 새로고침해 주세요.")
        updates["api_key_managers"] = ",".join(hashes)
    for key, value in updates.items():
        app_settings._instance.update_config(SECTION, key, value)
    if updates:
        app_settings._instance.save()
    after = load_config_from_settings()
    if before.enabled and not after.enabled:
        try:  # 끄면 진행 중인 연결 요청은 정리한다(기존 연결은 그대로)
            get_service().cancel()
        except CommunityAuthError:
            pass
    return after
