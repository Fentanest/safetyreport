"""사용자 조치 안내 목록(규칙 문서 8절, PC 웹 상단 안내). 문구는 11절 원칙을 따르되 PC 에서는 "앱" 대신 "프로그램"·"설정"을 쓴다(2026-10-22 검수자 결정).

순수 함수: 상태 값만 받아 화면에 보여 줄 항목을 돌려준다(저장 없음). PC 는 시스템 알림이 없고 웹 화면 안내만 쓰므로,
"같은 사유는 하루 1번" 규칙은 적용하지 않는다 — 조건이 풀릴 때까지 화면에 계속 보이는 것이 맞다.
점검(`maintenance`)은 사용자가 할 일이 없으므로 여기에 넣지 않는다(점검 배너가 따로 맡는다).
`optimize_blocked_background`(모바일 백그라운드 최적화 대기)는 PC 에서 생기지 않으므로 두지 않는다(PC 의 최적화 대기는
진행 상태 `waiting_optimize` 이고 서버가 켜져 있는 동안 스스로 끝난다, R5-의심3).
"""
from __future__ import annotations

UPDATE_URL = "https://github.com/Fentanest/safetyreport/releases/latest"
LOGIN_URL = "/settings/"
UPLOAD_URL = "/settings/"

TEXT_OFFICIAL_LOGIN = "안전신문고 로그인이 필요합니다. 설정에서 다시 로그인해 주세요."
TEXT_STALE = "30일 넘게 올리지 못한 신고가 {n}건 있습니다. 다시 시도를 눌러 주세요."
TEXT_UPDATE = "프로그램을 업데이트해 주세요. 업데이트하면 신고 공유를 이어 갑니다."


def _item(code: str, text: str, action_label: str | None, action_url: str | None) -> dict:
    return {"code": code, "text": text, "action_label": action_label, "action_url": action_url}


def evaluate(*, backfill_reason: str | None, official_login_manual: bool, stale_holds: int,
             update_required: bool) -> list[dict]:
    """화면에 보일 안내(중요한 순). `action_url` 이 상대 경로면 이 웹 화면 안, https 면 새 탭으로 연다.
    `stale_holds` 항목의 [다시 시도] 는 `action_url` 이 없고 `action_label` 만 있다 — 화면이 `POST /community/upload/raw-backfill/retry` 를 부른다."""
    items: list[dict] = []
    if update_required:
        items.append(_item("update_required", TEXT_UPDATE, "업데이트", UPDATE_URL))
    if backfill_reason == "official_login_required" and official_login_manual:
        items.append(_item("official_login_required", TEXT_OFFICIAL_LOGIN, "로그인", LOGIN_URL))
    try:
        stale = int(stale_holds or 0)
    except (TypeError, ValueError):
        stale = 0
    if stale > 0:
        items.append(_item("stale_holds", TEXT_STALE.format(n=stale), "다시 시도", None))
    return items
