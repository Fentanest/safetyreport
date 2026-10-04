"""앱 설정 명령과 저장 뒤 후속 작업(EO R-13). 라우터는 입력을 받아 명령을 만들고 결과를 응답으로 바꾸기만 한다.

- 웹 전체 저장(`web_command`): 설정 화면 폼의 모든 값. 시트 주소에서 ID 만, 전화번호는 숫자만, 신뢰 프록시는 앞뒤 공백 제거.
- API 부분 저장(`api_command`): 모바일이 바꿀 수 있는 불리언 4개만. 형이 틀리면 SettingsInvalid, 모르는 키는 무시(옛 앱이 보내는 폐기된 키).
- `apply`: 한 번에 기록·저장(설정 저장은 settings 의 원자 저장) → 안전신문고 계정이 바뀌었으면 이전 계정 토큰 무효화(기술일지 A2-01)
  → 요청했으면 스케줄러 job 갱신. job 갱신 실패는 저장을 되돌리지 않고 결과에 남긴다(예전과 같은 동작).
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field

import settings.settings as app_settings

API_BOOLEAN_FIELDS = ("exclude_withdraw", "use_representative_records", "auto_export_excel", "auto_export_sheet")
GOOGLE_CREDENTIAL_MAX_BYTES = 2 * 1024 * 1024

# 웹 폼 키 → (섹션, 설정 키)
_WEB_FIELDS = {
    "username": ("LOGIN", "username"), "password": ("LOGIN", "password"),
    "telegram_token": ("TELEGRAM", "telegram_token"), "chat_id": ("TELEGRAM", "chat_id"),
    "sheet_key": ("GOOGLESHEET", "sheet_key"),
    "remotepath": ("SELENIUM", "remotepath"), "chrome_mode": ("SELENIUM", "chrome_mode"),
    "headless": ("SELENIUM", "headless"), "remote_debug_port": ("SELENIUM", "remote_debug_port"),
    "scheduler_enabled": ("SCHEDULER", "enabled"), "scheduler_mode": ("SCHEDULER", "mode"),
    "scheduler_interval_hours": ("SCHEDULER", "interval_hours"), "scheduler_cron_times": ("SCHEDULER", "cron_times"),
    "scheduler_interval_start": ("SCHEDULER", "interval_start"),
    "phone_number": ("RATING", "phone_number"),
    "exclude_withdraw": ("SETTINGS", "exclude_withdraw"),
    "use_representative_records": ("SETTINGS", "use_representative_records"),
    "auto_export_excel": ("SETTINGS", "auto_export_excel"), "auto_export_sheet": ("SETTINGS", "auto_export_sheet"),
    "retry_interval": ("SETTINGS", "retry_interval"), "max_retry_attemps": ("SETTINGS", "max_retry_attemps"),
    "session_max_age": ("SETTINGS", "session_max_age"), "log_level": ("SETTINGS", "log_level"),
    "trusted_proxies": ("SETTINGS", "trusted_proxies"),
}


class SettingsInvalid(ValueError):
    def __init__(self, key: str):
        super().__init__(f"{key} must be a boolean")
        self.key = key


class CredentialInvalid(ValueError):
    pass


class CredentialTooLarge(ValueError):
    pass


@dataclass(frozen=True)
class SettingsCommand:
    values: tuple  # ((섹션, 키, 값), ...) — 기록 순서 그대로
    refresh_jobs: bool = False


@dataclass
class SettingsResult:
    login_changed: bool = False
    jobs_error: str | None = None
    written: list = field(default_factory=list)


def web_command(form: dict) -> SettingsCommand:
    """설정 화면 폼 → 명령(정규화 포함). form 은 화면이 보내는 모든 키를 가진다."""
    values = dict(form)
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', values.get("sheet_key") or "")  # 스프레드시트 주소 → ID
    if match:
        values["sheet_key"] = match.group(1)
    values["phone_number"] = re.sub(r'[^0-9]', '', values.get("phone_number") or "")
    values["trusted_proxies"] = (values.get("trusted_proxies") or "").strip()
    return SettingsCommand(tuple((*_WEB_FIELDS[key], values[key]) for key in _WEB_FIELDS if key in values),
                           refresh_jobs=True)


def api_command(body: dict) -> SettingsCommand:
    for key in API_BOOLEAN_FIELDS:
        if key in body and not isinstance(body[key], bool):
            raise SettingsInvalid(key)
    return SettingsCommand(tuple(("SETTINGS", key, body[key]) for key in API_BOOLEAN_FIELDS if key in body))


def apply(command: SettingsCommand) -> SettingsResult:
    instance = app_settings._instance
    previous_login = (instance.username, instance.password)
    result = SettingsResult()
    for section, key, value in command.values:
        instance.update_config(section, key, value)
        result.written.append((section, key))
    instance.save()
    if (instance.username, instance.password) != previous_login:
        # 안전신문고 계정이 바뀌면 이전 계정 토큰을 지운다 — 다음 크롤링은 새 계정으로 로그인한다(기술일지 A2-01)
        from core.crawler import direct_login
        direct_login.invalidate_token()
        result.login_changed = True
    if command.refresh_jobs:
        try:
            from core.utils import scheduler
            scheduler.update_jobs()
        except Exception as exc:
            from core.utils import logger
            logger.LoggerFactory.logbot.error(f"스케줄러 업데이트 실패: {exc}")
            result.jobs_error = type(exc).__name__
    return result


def _default_chrome_mode() -> str:
    raw = app_settings.config.get('SELENIUM', 'chrome_mode', fallback=None)
    if raw is not None:
        return raw
    if os.path.exists('/.dockerenv'):
        return 'hub'
    return 'desktop' if getattr(sys, 'frozen', False) else 'hub'


def google_credential_path() -> str:
    # google_api_auth_file 이 None 일 수 있으므로 datapath 기준으로 직접 경로 계산
    return os.path.join(app_settings._instance.datapath, 'auth', 'gspread.json')


def view_values() -> dict:
    """설정 화면 값(최신 파일을 다시 읽은 뒤). 기본값은 예전 화면과 같다."""
    app_settings._instance.load()
    config = app_settings.config
    return {
        "username": config.get('LOGIN', 'username', fallback=""),
        "password": app_settings.password or "",
        "telegram_token": config.get('TELEGRAM', 'telegram_token', fallback=""),
        "chat_id": config.get('TELEGRAM', 'chat_id', fallback=""),
        "sheet_key": config.get('GOOGLESHEET', 'sheet_key', fallback=""),
        "exclude_withdraw": config.getboolean('SETTINGS', 'exclude_withdraw', fallback=True),
        "use_representative_records": config.getboolean('SETTINGS', 'use_representative_records', fallback=True),
        "auto_export_excel": config.getboolean('SETTINGS', 'auto_export_excel', fallback=True),
        "auto_export_sheet": config.getboolean('SETTINGS', 'auto_export_sheet', fallback=False),
        "retry_interval": int(config.get('SETTINGS', 'retry_interval', fallback=10)),
        "max_retry_attemps": int(config.get('SETTINGS', 'max_retry_attemps', fallback=5)),
        "log_level": config.get('SETTINGS', 'log_level', fallback="INFO"),
        "chrome_mode": _default_chrome_mode(),
        "remote_debug_port": config.get('SELENIUM', 'remote_debug_port', fallback="127.0.0.1:9222"),
        "headless": config.getboolean('SELENIUM', 'headless', fallback=False),
        "scheduler_enabled": config.getboolean('SCHEDULER', 'enabled', fallback=False),
        "scheduler_mode": config.get('SCHEDULER', 'mode', fallback='interval'),
        "scheduler_interval_hours": int(config.get('SCHEDULER', 'interval_hours', fallback=24)),
        "scheduler_cron_times": config.get('SCHEDULER', 'cron_times', fallback='09:00'),
        "scheduler_interval_start": config.get('SCHEDULER', 'interval_start', fallback='00:00'),
        "phone_number": config.get('RATING', 'phone_number', fallback=''),
        "remotepath": config.get('SELENIUM', 'remotepath', fallback="http://localhost:4444/wd/hub"),
        "google_json_exists": os.path.isfile(google_credential_path()),
        "session_max_age": int(config.get('SETTINGS', 'session_max_age', fallback=10800)),
        "trusted_proxies": config.get('SETTINGS', 'trusted_proxies', fallback=''),
    }


def validate_google_credential(contents: bytes) -> None:
    """구글 서비스 계정 JSON 인지 확인한다(파일을 바꾸기 전에)."""
    if len(contents) > GOOGLE_CREDENTIAL_MAX_BYTES:
        raise CredentialTooLarge()
    try:
        credential = json.loads(contents)
        if not isinstance(credential, dict) or credential.get('type') != 'service_account' or any(
                not isinstance(credential.get(key), str) or not credential[key].strip()
                for key in ('client_email', 'private_key', 'token_uri')):
            raise ValueError('invalid service account')
    except (ValueError, UnicodeDecodeError):
        raise CredentialInvalid() from None


def save_google_credential(contents: bytes) -> None:
    """검증한 인증 파일을 원자적으로 바꾸고 설정을 다시 읽는다(google_sheet_enabled 등 갱신)."""
    from core.utils.atomic_file import write_bytes

    path = google_credential_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_bytes(path, contents)
    app_settings._instance.load()
