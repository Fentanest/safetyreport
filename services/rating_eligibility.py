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
