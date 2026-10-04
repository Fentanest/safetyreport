"""커뮤니티 계정 연결의 오류 타입·문구·HTTP 상태와 시각 도우미(EO R-09에서 서비스에서 분리)."""
from __future__ import annotations

from datetime import datetime, timezone

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

HTTP_STATUS_EXTRA = {"deletion_unconfirmed": 503}
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
        self.status = HTTP_STATUS.get(code) or HTTP_STATUS_EXTRA.get(code, 500)
        self.message = message or MESSAGES.get(code, MESSAGES["internal_error"])
        self.retry_after = retry_after


def error_dict(code: str) -> dict:
    return {"code": code, "message": MESSAGES.get(code, MESSAGES["internal_error"])}


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(value) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()
