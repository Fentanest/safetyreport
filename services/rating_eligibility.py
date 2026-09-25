"""별점(만족도 조사) 대상 판정 — 모바일 lib/services/rating_service.dart `ineligibleReason` 과 같은 규칙·문구(2026-09-25).

목록(`get_unrated_records`)과 제출(`star_rating_service.run_batch_rating`)이 이 함수 하나를 쓴다.
같은 입력·같은 결과는 두 레포 공용 `contracts/rating-eligibility-vectors.json` 으로 양쪽 테스트.
"""
from __future__ import annotations

_PROCESSING_STATUSES = frozenset({"진행", "진행중", "검토중", "처리중"})
BLOCKED_STATUSES = frozenset({"취하", "답변 대기", "처리중"})


def canonical_status(status) -> str:
    """진행/진행중/검토중/처리중 → 처리중, 나머지는 앞뒤 공백만 뗀 값."""
    trimmed = str(status or "").strip()
    return "처리중" if trimmed in _PROCESSING_STATUSES else trimmed


def ineligible_reason(poll_status, status) -> str | None:
    """별점을 줄 수 없는 이유. 줄 수 있으면 None."""
    poll = str(poll_status or "").strip()
    if poll == "참여 완료":
        return "이미 만족도 조사에 참여한 신고입니다."
    if poll == "참여 불가":
        return "만족도 조사가 불가능한 신고입니다."
    canonical = canonical_status(status)
    if canonical in BLOCKED_STATUSES:
        return f"{canonical} 상태에서는 만족도 조사를 진행할 수 없습니다."
    return None


# 별점 공통 사유(선택). 사이트 `STSFDG_CAUSE` 로 보낸다. 사이트 제한은 알려지지 않아 안전 상한만 둔다.
# 길이는 유니코드 코드포인트 수(Python len, Dart runes.length, 웹 JS [...s].length) — 세 곳이 같게 센다.
RATING_CAUSE_MAX = 1000
# 세 언어의 trim 이 공통으로 떼는 문자: 공백·탭·줄바꿈·NBSP·전각 공백·BOM 등
_TRIM_CHARS = " \t\n\x0b\x0c\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff"


def normalize_cause(text) -> str:
    """줄바꿈을 \\n 으로 맞추고 앞뒤 공백·BOM 을 뗀다(가운데 줄바꿈은 유지). Dart trim·JS trim 처럼 BOM(U+FEFF)도 뗀다."""
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip(_TRIM_CHARS)


def cause_error(text) -> str | None:
    """정리한 사유가 상한을 넘으면 안내 문구, 아니면 None."""
    length = len(normalize_cause(text))
    if length > RATING_CAUSE_MAX:
        return f"사유는 {RATING_CAUSE_MAX}자까지 입력할 수 있습니다. (현재 {length}자)"
    return None
