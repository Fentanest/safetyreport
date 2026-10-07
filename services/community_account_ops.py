"""커뮤니티 계정 업무 순서(EO R-08). HTTP 처리기는 입력·권한 확인과 응답 변환만 하고, 순서는 여기서 정한다.

- 로그아웃: 자료 주인 확인 → 게이트 무효화(업로드·새 작업 중단) → (필요하면) 신고 자료 삭제 → 카카오 연결 해제.
  주인을 확인하지 못하면 아무것도 하지 않는다(남의 자료를 지우지 않게). 삭제가 거절되면(크롤링 중 등) 로그인도 그대로 둔다.
- 자료 채택: 게이트가 db_owner_mismatch 일 때만, 지금 카카오 계정으로 자료를 지우고 다시 확인한다.
- 동의·철회·업로드 연결 전환·공유 자료 삭제 요청: 중앙 community-account 호출과 오류 변환, 게이트 재확인.
"""
from __future__ import annotations

import logging

from core.utils.fallback import note_fallback
from services import community_auth_service as cas
from services import community_gate
from services import community_cloud as cloud
from services.community_account_client import AccountApiError, CommunityAccountClient
from services.community_auth_service import CommunityAuthError

logger = logging.getLogger("services.community_account_ops")

LOGOUT_CONFIRM = "DELETE_MY_REPORTS"
ADOPT_CONFIRM = "DELETE_OTHER_ACCOUNT_REPORTS"


class OperationRefused(RuntimeError):
    """지금은 할 수 없어 아무것도 바꾸지 않았다(크롤링·지도 변환 중, 자료 주인 확인 실패). HTTP 409 busy."""


def logout_wipes(service) -> bool:
    """로그아웃하면 이 서버의 신고 자료를 지우는가. 지금 로그인한 카카오 계정이 자료 주인과 **다르다고 확인된** 경우만 남긴다
    (다른 계정의 자료이므로). 주인이 없거나 같거나 확인하지 못하면 지운다(로그아웃 = 자료 삭제, 사용자 결정)."""
    from services import account_data

    owner = account_data.db_owner()
    kakao = service.session_kakao_id()
    return not (owner and kakao and owner != kakao)


def logout() -> dict:
    """카카오 로그아웃: 신고 자료를 지운 뒤(관리자·API 키·감시목록·지오코딩 캐시는 남김) 이 서버의 카카오 로그인을 끝낸다."""
    from core.storage.exchange import RestoreRefused
    from services import account_data

    service = cas.get_service()
    try:
        wipe = logout_wipes(service)
    except Exception as exc:  # 주인 표시를 읽지 못함 — 남의 자료를 지우지 않게 로그아웃하지 않는다
        logger.warning("[community] 로그아웃 전 자료 주인 확인 실패: %s", type(exc).__name__)
        raise OperationRefused("저장된 신고 내역을 확인하지 못해 로그아웃하지 않았습니다. 잠시 뒤 다시 시도하세요.") from None
    cloud.deny("logout", service=service)
    community_gate.invalidate("logout")  # 업로드·새 작업을 먼저 멈춘다
    wiped = None
    if wipe:
        try:
            wiped = account_data.wipe_report_data("kakao_logout")
        except RestoreRefused as exc:  # 크롤링·지도 변환 중 — 아무것도 지우지 않고 로그인도 그대로
            community_gate.invalidate("logout_refused")
            raise OperationRefused(str(exc)) from None
    result = service.disconnect()
    return {"data": service.status(can_manage=True), "result": dict(result, reports_wiped=bool(wiped)),
            "gate": community_gate.status_view()}


def reset_session() -> dict:
    """세션 파일을 읽을 수 없을 때만: 옆으로 옮기고 다시 로그인하게 한다(자료는 그대로 — 다음 로그인 계정이 주인과 다르면 게이트가 막는다)."""
    service = cas.get_service()
    if service.status(can_manage=True)["state"] != "store_unreadable":
        raise CommunityAuthError("invalid_state")
    community_gate.invalidate("session_reset")
    result = service.disconnect()
    return {"data": service.status(can_manage=True), "result": result, "gate": community_gate.status_view()}


def adopt_db_owner() -> dict:
    """이 서버의 신고 자료가 다른 카카오 계정 것일 때(게이트 db_owner_mismatch): 그 자료를 지우고 지금 계정으로 시작한다."""
    from core.storage.exchange import RestoreRefused
    from services import account_data

    if community_gate.evaluate()["state"] != "db_owner_mismatch":
        raise CommunityAuthError("invalid_state")
    kakao = cas.get_service().current_kakao_id()
    if not kakao:
        raise CommunityAuthError("not_connected")
    try:
        account_data.wipe_report_data("db_owner_adopt", then_owner=kakao)
    except RestoreRefused as exc:
        raise OperationRefused(str(exc)) from None
    return {"data": regate("db_owner_adopt")}


# ── 필수 게이트: 동의·철회·업로드 연결·삭제 요청 ──────────────────────────────────

_ACCOUNT_ERRORS = {
    "kakao_required": (403, "카카오 계정 연결이 먼저 필요합니다."),
    "policy_mismatch": (409, "동의 문서가 바뀌었습니다. 새로고침한 뒤 새 문서를 확인해 주세요."),
    "contributor_suspended": (403, "이 계정의 공유가 중지되어 있습니다."),
    "stale_grant": (409, "동의 상태가 바뀌었습니다. 새로고침한 뒤 다시 시도해 주세요."),
    "not_found": (404, "대상을 찾을 수 없습니다."),
    "writer_conflict": (409, "다른 기기가 이 공식 계정의 업로드를 맡고 있습니다."),
    "rate_limited": (429, "요청이 너무 잦습니다. 잠시 뒤 다시 시도해 주세요."),
    "auth_required": (401, "커뮤니티 로그인이 만료되었습니다. 다시 로그인해 주세요."),
}


# 사용자가 이 서버에서 직접 한 행동(카카오 로그인 확정·공유 동의). 업로드 연결이 다른 기기에 있으면 이 서버로 가져온다(2026-09-28).
_CLAIM_REASONS = {"login", "consent_saved"}


def regate(reason: str) -> dict:
    if reason in _CLAIM_REASONS:
        community_gate.claim_for_this_device()
    community_gate.invalidate(reason)
    community_gate.refresh_now()
    return community_gate.status_view()


def account_call(fn):
    """커뮤니티 세션 토큰으로 community-account 를 부른다. 실패는 CommunityAuthError 로 바꾼다."""
    service = cas.get_service()
    cfg = service.config()
    token = service.get_access_token()  # CommunityAuthError(not_connected/reauth_required/...)
    try:
        return fn(CommunityAccountClient(cfg.supabase_url, cfg.publishable_key), token)
    except AccountApiError as exc:
        status, message = _ACCOUNT_ERRORS.get(exc.code, (503 if exc.transient else 502, None))
        err = CommunityAuthError("account_" + exc.code if exc.code not in _ACCOUNT_ERRORS else exc.code,
                                 message or "커뮤니티 서버에 연결하지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
                                 retry_after=exc.retry_after)
        err.status = status
        err.extra = exc.extra
        raise err from None


def policy_view() -> dict:
    """중앙의 지금 동의문(본문 해시를 확인한 것). 카카오 로그인 전이면 CommunityAuthError(not_connected 등)."""
    p = account_call(lambda c, t: c.policy(t))
    return {"policy_version": p["version"], "consent_text_sha256": p["consent_text_sha256"], "text": p["consent_text"]}


def consent(policy_version: str, consent_text_sha256: str) -> dict:
    # 화면이 보여 준 동의문의 (버전, 해시) 그대로 보낸다. 그 사이 중앙 정책이 바뀌었으면 중앙이 policy_mismatch 로 거절한다.
    res = account_call(lambda c, t: c.consent(t, policy_version, consent_text_sha256))
    cloud.consent_accepted()
    cloud.observe(cas.get_service(), kind="active", source="consent_accepted")
    return {"result": {k: res.get(k) for k in ("policy_version", "granted_at", "created")}, "gate": regate("consent_saved")}


def consent_revoke() -> dict:
    cloud.deny("consent_revoked", sticky=True)
    cloud.observe(cas.get_service(), kind="revoked", source="local_revoke_pending")
    community_gate.invalidate("consent_revoked")
    grant_id = community_gate.current_grant_id()
    if not grant_id:
        community_gate.refresh_now()
        grant_id = community_gate.current_grant_id()
    if not grant_id:
        raise CommunityAuthError("invalid_state", "철회할 동의가 없습니다.")
    res = account_call(lambda c, t: c.consent_revoke(t, grant_id))
    return {"result": {"revoked": bool(res.get("revoked")), "already_revoked": bool(res.get("already_revoked"))},
            "gate": regate("consent_revoked")}


def takeover() -> dict:
    community_gate.request_takeover()
    return {"gate": community_gate.status_view()}


def contributions_delete() -> dict:
    # 미구현 기능(공유한 자료 전체 삭제)의 내부 처리. 현재 이를 부르는 화면·HTTP 경로가 없다(아래 주석 처리한 라우트 참고).
    from services import community_capture

    # 1) 로컬 삭제 대기 표시를 먼저(community.db, 트랜잭션). 못 쓰면 중앙 삭제를 요청하지 않는다(Sol 2차 H-03a).
    try:
        local_id = community_capture.begin_deletion()
    except Exception:
        raise CommunityAuthError("invalid_state", "이 서버의 공유 저장소에 기록할 수 없어 삭제를 요청하지 않았습니다. 잠시 뒤 다시 시도해 주세요.") from None
    community_gate.invalidate("deletion_requested")
    try:
        res = account_call(lambda c, t: c.delete_contributions(t))
    except CommunityAuthError as exc:
        if 400 <= (exc.status or 500) < 500:
            # 중앙이 확실히 거절(4xx·토큰 없음 등 — 삭제가 일어나지 않음): 이 prepared 표시만 지운다
            try:
                community_capture.cancel_deletion(local_id)
            except Exception as cancel_exc:  # 지우지 못하면 업로드가 막힌 채 남는다(fail-closed)
                note_fallback("community_account_ops.cancel_deletion", cancel_exc)
            raise
        # 응답 불명(네트워크·타임아웃·5xx): 중앙이 이미 지웠을 수 있다 → 표시를 유지하고(업로드·reshare 차단) 다시 요청하게 한다.
        # 삭제는 여러 번 요청해도 안전하다(Sol 3차 H-03d).
        raise CommunityAuthError("deletion_unconfirmed", "삭제 요청 결과를 확인하지 못했습니다. 확인될 때까지 업로드를 멈췄습니다. "
                                 "네트워크를 확인한 뒤 '공유한 자료 삭제 요청'을 다시 눌러 주세요.") from None
    local_ok = True
    try:  # 2) 중앙 성공 뒤에만 확정·적용(그 시점까지의 journal 전부 차단, 앞선 prepared 표시 포함). 실패해도 confirmed 표시가 막는다.
        community_capture.confirm_deletion()
    except Exception:
        local_ok = False
    # 3) 로컬 확정 뒤에 writer 파일을 지운다(Sol 4차 3 — 파일 오류가 확정을 가로막지 않게).
    #    중앙이 연결을 모두 폐기했다 → 다음 확인 때 새로 등록. 못 지우면 폐기된 연결이라 중앙이 거절하므로 업로드는 나가지 않는다.
    writer_ok = True
    try:
        cas.get_service().store.save_writer(None)
    except Exception:
        writer_ok = False
        logger.warning("공유 자료 삭제 뒤 writer 연결 파일을 지우지 못함 — 다음 게이트 확인 때 다시 등록 필요")
    return {"result": {k: res.get(k) for k in ("deletion_id", "deleted_facts", "revoked_connections", "deleted_at")},
            "local_cleanup_pending": not local_ok, "writer_reset_pending": not writer_ok,
            "gate": regate("deletion_completed")}
