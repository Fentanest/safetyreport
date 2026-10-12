"""커뮤니티 공개 설정 정규화(EO R-09에서 서비스에서 분리).

우선순위: 환경변수(SAFETYREPORT_COMMUNITY_* = COMMUNITY_* 별칭, 둘 다 있고 다르면 config_conflict)
> config.ini [COMMUNITY] > 빌드에 넣은 community_public.json.
폐기 호스트의 주소·키는 유효한 대체 번들이 있으면 함께 무시한다.
"""
from __future__ import annotations

import dataclasses
import configparser
import datetime
import logging
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
RETIRED_SUPABASE_HOSTS = frozenset({"nxdcxccixoswvqgjeprh.supabase.co"})

_TRUE = {"1", "true", "yes", "on"}


def is_retired_supabase_url(value) -> bool:
    try:
        return isinstance(value, str) and urlsplit(value.strip()).hostname in RETIRED_SUPABASE_HOSTS
    except ValueError:
        return False


def has_replacement_bundle(bundled: dict) -> bool:
    return bool(normalize_supabase_url(bundled.get("supabase_url"))
                and not is_retired_supabase_url(bundled.get("supabase_url"))
                and validate_publishable_key(bundled.get("publishable_key")))


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
    """환경변수 > ini > 번들 순서. 폐기 호스트의 주소·키 쌍은 유효한 번들로 복구한다."""
    env = os.environ if env is None else env
    bundled = bundled or {}
    locked = set()
    conflicts = set()

    # URL과 키는 같은 서버의 한 쌍이다. 폐기된 환경 설정은 ini보다도 번들을 우선한다.
    env_urls = [v.strip() for name in (ENV_SUPABASE_URL, *ENV_ALIASES[ENV_SUPABASE_URL])
                if (v := env.get(name)) and v.strip()]
    retired_env = bool(env_urls) and all(is_retired_supabase_url(v) for v in env_urls)
    retired_raw = is_retired_supabase_url(raw.get("supabase_url"))
    replacement = has_replacement_bundle(bundled)
    if replacement:
        if retired_raw or retired_env:
            raw = {k: v for k, v in raw.items() if k not in ("supabase_url", "publishable_key")}
        if retired_env:
            ignored = {ENV_SUPABASE_URL, *ENV_ALIASES[ENV_SUPABASE_URL],
                       ENV_PUBLISHABLE_KEY, *ENV_ALIASES[ENV_PUBLISHABLE_KEY]}
            env = {k: v for k, v in env.items() if k not in ignored}

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
    if is_retired_supabase_url(url_raw) and not replacement:
        problems.append("retired_supabase_url")
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


def cleanup_retired_config(config_path: str, bundled: dict) -> bool:
    """시작 시 폐기된 ini의 주소·키만 백업 후 제거한다. 실패해도 build_config가 우회한다.

    AppSettings.save는 ConfigParser로 전체를 직렬화해 주석을 지우므로 여기서는
    같은 파일 잠금/원자적 writer를 쓰되 나머지 바이트를 그대로 보존한다.
    대체 번들이 없으면 원래 설정과 파일을 유지한다.
    """
    if not has_replacement_bundle(bundled) or not os.path.isfile(config_path):
        return False
    from core.utils.atomic_file import write_bytes
    from core.utils.file_lock import exclusive

    try:
        with exclusive(config_path + ".lock"):
            with open(config_path, "rb") as source:
                original = source.read()
            parser = configparser.ConfigParser(interpolation=None)
            text = original.decode("utf-8")
            parser.read_string(text)
            if not is_retired_supabase_url(parser.get(SECTION, "supabase_url", fallback="")):
                return False
            lines = []
            section = None
            option_indent = None
            removing = False
            for line in text.splitlines(keepends=True):
                stripped = line.strip()
                if not stripped or stripped.startswith(("#", ";")):
                    lines.append(line)
                    continue
                indent = len(line) - len(line.lstrip())
                if option_indent is not None and indent > option_indent:
                    if not removing:
                        lines.append(line)
                    continue
                header = parser.SECTCRE.match(stripped)
                if header:
                    section = header.group("header")
                    option_indent, removing = None, False
                else:
                    option = re.match(r"([^:=]+?)\s*[:=]", stripped)
                    option_indent = indent
                    removing = bool(section == SECTION and option
                                    and option.group(1).strip().lower() in ("supabase_url", "publishable_key"))
                if not removing:
                    lines.append(line)
            updated = "".join(lines).encode("utf-8")
            if updated == original:
                return False
            # 제거 결과도 파싱되어야 한다. 오류 때 원본을 건드리지 않는다.
            cleaned = configparser.ConfigParser(interpolation=None)
            cleaned.read_string(updated.decode("utf-8"))
            if is_retired_supabase_url(cleaned.get(SECTION, "supabase_url", fallback="")):
                return False  # DEFAULT에서 상속한 주소는 이번 정리 대상이 아니다.
            for key in ("supabase_url", "publishable_key"):
                parser.remove_option(SECTION, key)
            if dict(cleaned) != dict(parser):
                return False
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            backup = f"{config_path}.bak-{stamp}"
            write_bytes(backup, original)
            write_bytes(config_path, updated)
        logging.getLogger("safetyreport.core.community_auth").warning(
            "이전 커뮤니티 서버 주소 설정을 정리했습니다(백업: %s)", backup)
        return True
    except (OSError, UnicodeError, configparser.Error):
        # 예외에는 설정 원문이 포함될 수 있다. 비밀을 로그에 남기지 않는다.
        return False


def load_config_from_settings() -> CommunityConfig:
    import settings.settings as app_settings

    cfg = app_settings._instance.config
    raw = dict(cfg.items(SECTION)) if cfg.has_section(SECTION) else {}
    return build_config(raw, bundled=load_bundled_public())
