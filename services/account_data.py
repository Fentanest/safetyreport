"""신고 자료의 주인 = 로그인한 카카오 계정 (2026-09-27 사용자 결정, 모바일 lib/services/account_data.dart 와 같은 규칙).

- 카카오 로그인은 필수다. 게이트를 통과하면 개인 DB(sync_meta)에 카카오 회원번호를 적는다(처음 한 번). 이미 다른 번호가 적혀 있으면
  게이트는 `db_owner_mismatch` 로 막고, 사용자가 "자료를 지우고 이 계정으로 시작"하거나 로그아웃(원래 주인 자료는 남김)해야 한다.
- 카카오 로그아웃은 신고 자료를 지운다(관리자·API 키·감시목록·지오코딩 캐시는 남김 — 이전 DB 초기화와 같은 범위).
- DB 가져오기·복원은 카카오 회원번호가 지금 로그인한 계정과 같은 DB 만 받는다. 번호가 없는 DB(이 기능 전 DB)는 거절한다.
"""
from __future__ import annotations

import sqlite3

from core.database import database
from core.storage.exchange import RestoreRefused
from core.utils import logger

KAKAO_MEMBER_META_KEY = database.KAKAO_MEMBER_META_KEY


class ForeignDatabaseRefused(RestoreRefused):
    """다른 카카오 계정의 DB(또는 주인을 모르는 DB) — 가져오지 않는다."""


def _engine():
    from core.database.engine import get_engine

    return get_engine()


def db_owner(engine=None) -> str | None:
    """이 서버 DB 의 주인 카카오 회원번호(없으면 None)."""
    try:
        value = database.get_meta(engine or _engine(), KAKAO_MEMBER_META_KEY)
    except Exception:
        return None
    return value or None


def check_owner(kakao_id: str | None, engine=None) -> str:
    """게이트 통과 뒤 호출: 'ok'(같음·처음이라 적음) | 'mismatch'(다른 계정의 자료) | 'unknown'(로그인 계정 번호를 모름)."""
    if not kakao_id:
        return "unknown"
    engine = engine or _engine()
    owner = db_owner(engine)
    if owner is None:
        database.set_meta(engine, KAKAO_MEMBER_META_KEY, kakao_id)
        logger.LoggerFactory.logbot.info("[account] 이 서버 DB 의 주인 카카오 계정을 기록했습니다.")
        return "ok"
    return "ok" if owner == kakao_id else "mismatch"


def wipe_report_data(reason: str, *, then_owner: str | None = None) -> dict:
    """신고 자료를 비운다(크롤링·지도 변환 중이면 거절 — exchange.RestoreRefused). community.db 데이터셋을 먼저 선회전해
    지운 자료의 공유 대기 사본이 다음 계정으로 가지 않게 한다. then_owner 를 주면 비운 뒤 그 번호를 새 주인으로 적는다."""
    from core.database import write_barrier
    from core.storage import exchange
    from services.crawl_manager import RestoreBlocked, crawl_manager

    engine = _engine()
    exchange.ensure_restore_allowed(engine)

    def rotate():
        from services.community_store import CommunityStore
        CommunityStore.open().rotate_dataset(reason)

    try:
        with crawl_manager.hold_for_restore(), write_barrier.exclusive():
            info = database.empty_report_data(engine, before_empty=rotate)
            if then_owner:
                database.set_meta(engine, KAKAO_MEMBER_META_KEY, then_owner)
    except (RestoreBlocked, write_barrier.BarrierTimeout) as exc:
        raise exchange.RestoreRefused(str(exc)) from None
    return info


def file_owner(path: str, kind: str) -> str | None:
    """가져올 DB 파일의 주인 카카오 회원번호. 서버 DB 는 mysafety_sync_meta, 모바일 DB 는 sync_meta."""
    table = "mysafety_sync_meta" if kind == "server" else "sync_meta"
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute(f"SELECT value FROM {table} WHERE key=?", (KAKAO_MEMBER_META_KEY,)).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return row[0] if row and row[0] else None


def refuse_foreign_owner(path: str, kind: str, current_kakao_id: str | None) -> None:
    """가져오기·복원 전: 파일의 주인이 지금 로그인한 카카오 계정과 같아야 한다. 무엇이든 바꾸기 전에 부른다."""
    if not current_kakao_id:
        raise ForeignDatabaseRefused("카카오 로그인을 확인하지 못해 DB 를 가져올 수 없습니다. 다시 로그인한 뒤 시도하세요.")
    owner = file_owner(path, kind)
    if owner is None:
        raise ForeignDatabaseRefused(
            "누구의 자료인지 알 수 없는 DB(카카오 계정 정보가 없는 이전 DB)는 가져올 수 없습니다. "
            "초기화 크롤링으로 안전신문고에서 다시 받으세요.")
    if owner != current_kakao_id:
        raise ForeignDatabaseRefused("다른 카카오 계정의 DB 는 가져올 수 없습니다. 지금 로그인한 계정으로 만든 DB 만 가져올 수 있습니다.")
