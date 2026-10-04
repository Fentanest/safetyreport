"""커뮤니티 세션 토큰 공급(EO R-09에서 서비스에서 분리). 같은 세션 저장소 락 안에서 한 번만 refresh 한다."""
from __future__ import annotations

import logging

from services.community_auth_client import AuthError, CommunityHttpError, jwt_claims_unverified
from services.community_auth_errors import CommunityAuthError, iso as _iso

_log = logging.getLogger("safetyreport.core.community_auth")

REFRESH_MARGIN_SECONDS = 60


class CommunityTokenProvider:
    """service 의 저장소·설정·클라이언트·시계를 그대로 쓴다(락·저장 형식이 하나)."""

    def __init__(self, service):
        self._service = service

    def get_access_token(self, *, rejected: str | None = None) -> str:
        """업로더용: 유효한 access token. 60초 안에 만료되면 락 안에서 한 번만 refresh 한다.

        rejected: 서버가 401 로 거절한 토큰. 저장된 토큰이 그것과 같으면 만료 전이어도 **실제로** refresh 한다(같은 토큰을
        다시 돌려주는 것은 갱신이 아니다). 다른 호출자가 이미 바꿨으면 새 토큰을 그대로 돌려준다(회전된 refresh token 을 덮지 않음).
        refresh 네트워크 실패는 auth_unavailable(일시), refresh token 거부는 reauth_required(확정)."""
        service = self._service
        cfg = service.config()
        service._require_ready(cfg)
        with service.store.locked():
            st = service._load()  # 락을 잡은 뒤 다시 읽는다 → 다른 호출자가 이미 갱신했으면 그 값을 쓴다
            cur = st.get("current")
            if not cur:
                raise CommunityAuthError("reauth_required" if st.get("reauth") else "not_connected")
            forced = rejected is not None and cur.get("access_token") == rejected
            if not forced and float(cur.get("expires_at") or 0) - service._now() > REFRESH_MARGIN_SECONDS:
                return cur["access_token"]
            try:
                session = service._client(cfg).refresh(refresh_token=cur["refresh_token"])
            except AuthError as exc:
                if exc.reauth_required:
                    st["reauth"] = {k: cur.get(k) for k in ("display_name", "connected_at", "has_email", "user_id", "kakao_id")}
                    st["current"] = None
                    st["last_error"] = {"code": "reauth_required", "at": _iso(service._now())}
                    service.store.save(st)
                    _log.warning("[community] refresh 토큰이 거부되어 다시 로그인이 필요합니다: %s", exc.code)
                    raise CommunityAuthError("reauth_required") from None
                raise CommunityAuthError("auth_unavailable") from None
            except CommunityHttpError:
                raise CommunityAuthError("auth_unavailable") from None
            now = service._now()
            expires_at = session.get("expires_at")
            if not isinstance(expires_at, (int, float)):
                expires_in = session.get("expires_in")
                expires_at = now + (float(expires_in) if isinstance(expires_in, (int, float)) else 3600.0)
            claims = jwt_claims_unverified(session["access_token"])
            cur.update({"access_token": session["access_token"], "refresh_token": session["refresh_token"],
                        "expires_at": float(expires_at), "refreshed_at": _iso(now)})
            if isinstance(claims.get("session_id"), str):
                cur["session_id"] = claims["session_id"]
            service.store.save(st)  # access + refresh 를 함께 원자 저장
            return cur["access_token"]
