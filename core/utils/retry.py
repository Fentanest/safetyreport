import settings.settings as settings


def get_configured_attempts(default: int = 3) -> int:
    try:
        return max(1, int(settings.max_retry_attemps))
    except Exception:
        return default


def get_retry_interval(default: int = 1) -> int:
    try:
        return max(1, int(settings.retry_interval))
    except Exception:
        return default
