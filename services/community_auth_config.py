"""커뮤니티 공개 설정 정규화(EO R-09에서 서비스에서 분리).

우선순위: 환경변수(SAFETYREPORT_COMMUNITY_* = COMMUNITY_* 별칭, 둘 다 있고 다르면 config_conflict)
> config.ini [COMMUNITY] > 빌드에 넣은 community_public.json.
"""
from __future__ import annotations

import dataclasses
import os
import re
from urllib.parse import urlsplit

from services.community_auth_client import jwt_claims_unverified, normalize_device_label

SECTION = "COMMUNITY"
DEFAULT_SITE_URL = "https://safeauth.worklazy.net/"
ENV_ENABLED = "SAFETYREPORT_COMMUNITY_ENABLED"
ENV_SUPABASE_URL = "SAFETYREPORT_COMMUNITY_SUPABASE_URL"
ENV_PUBLISHABLE_KEY = "SAFETYREPORT_COMMUNITY_PUBLISHABLE_KEY"
ENV_SITE_URL = "SAFETYREPORT_COMMUNITY_SITE_URL"
# 별칭: 빌드·Docker 가 쓰는 짧은 이름. 공개 키는 Android·지도와 같은 정본 COMMUNITY_SUPABASE_PUBLISHABLE_KEY 와 옛 이름
# COMMUNITY_PUBLISHABLE_KEY 를 모두 받는다. 여러 이름이 서로 다른 값이면 config_conflict(조용히 하나를 고르지 않는다).
ENV_ALIASES = {ENV_ENABLED: ("COMMUNITY_ENABLED",), ENV_SUPABASE_URL: ("COMMUNITY_SUPABASE_URL",),
               ENV_PUBLISHABLE_KEY: ("COMMUNITY_SUPABASE_PUBLISHABLE_KEY", "COMMUNITY_PUBLISHABLE_KEY"),
               ENV_SITE_URL: ("COMMUNITY_SITE_URL",)}
BUNDLED_PUBLIC_FILE = "community_public.json"

_TRUE = {"1", "true", "yes", "on"}


def normalize_supabase_url(value) -> str | None:
    """https origin 만. http 는 127.0.0.1(로컬 검증 스택)만. 경로·쿼리·계정정보 금지."""
    value = (value or "").strip() if isinstance(value, str) else ""
    if not value:
        return None
    if any(token in value.lower() for token in ('<', 'your_', 'project_ref', 'example')):
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
    enabled: bool = True  # 필수 기능 — 항상 True. 설정의 false 는 disabled_ignored 로만 남는다.
    supabase_url: str = ""
    publishable_key: str = ""
    site_url: str = DEFAULT_SITE_URL
    device_label: str = ""
    api_key_managers: frozenset = frozenset()
    problems: tuple = ()
    env_locked: frozenset = frozenset()
    disabled_ignored: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.supabase_url and self.publishable_key and self.site_url) and not self.problems


def build_config(raw: dict, env: dict | None = None, bundled: dict | None = None) -> CommunityConfig:
    """환경변수(정식 이름·별칭) > config.ini [COMMUNITY](raw) > 번들 공개값(bundled) 순서로 검증된 설정을 만든다."""
    env = os.environ if env is None else env
    bundled = bundled or {}
    locked = set()
    conflicts = set()

    def pick(key: str, env_name: str, default: str = "") -> str:
        values = [v.strip() for v in (env.get(env_name), *(env.get(a) for a in ENV_ALIASES[env_name])) if v is not None and v.strip() != ""]
        if values:
            locked.add(key)
            if len(set(values)) > 1:
                conflicts.add(key)
            return values[0]
        value = raw.get(key)
        if isinstance(value, str) and any(token in value.lower() for token in ('<', 'your_', 'project_ref', 'example')) and bundled.get(key):
            value = None  # Old sample config is not an explicit advanced override.
        if value is not None and str(value).strip() != "":
            return str(value).strip()
        value = bundled.get(key)
        return default if not isinstance(value, str) or not value.strip() else value.strip()

    problems = []
    disabled_ignored = pick("enabled", ENV_ENABLED, "true").lower() not in _TRUE
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
    if conflicts:
        problems.append("config_conflict")
    return CommunityConfig(enabled=True, supabase_url=url, publishable_key=key, site_url=site,
                           device_label=label, api_key_managers=managers,
                           problems=tuple(problems), env_locked=frozenset(locked), disabled_ignored=disabled_ignored)


_bundled_cache: dict | None = None


def load_bundled_public() -> dict:
    """빌드가 넣은 공개 설정(community_public.json: supabase_url·publishable_key·site_url). 없거나 틀리면 빈 dict."""
    global _bundled_cache
    if _bundled_cache is None:
        import json

        from core.utils.path_utils import resource_path

        data = {}
        try:
            with open(resource_path(BUNDLED_PUBLIC_FILE), "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data = {k: loaded[k] for k in ("supabase_url", "publishable_key", "site_url") if isinstance(loaded.get(k), str)}
        except (OSError, ValueError):
            data = {}
        _bundled_cache = data
    return _bundled_cache


def load_config_from_settings() -> CommunityConfig:
    import settings.settings as app_settings

    cfg = app_settings._instance.config
    raw = dict(cfg.items(SECTION)) if cfg.has_section(SECTION) else {}
    return build_config(raw, bundled=load_bundled_public())
