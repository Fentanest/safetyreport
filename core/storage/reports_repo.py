"""크롤링 결과 저장과 화면용 표(merge) 만들기 (저장 계층 재설계 R2, docs/plans/storage-refactor-plan.md §3-3).

규칙
- 열 주인(계약 owner): 크롤러는 사이트 열(site)만 쓴다. 사용자 수정값은 mysafety_report_override 에 있고 재크롤링이 지우지 않는다.
- 신고 1건 = 트랜잭션 1개: 상세·본문·entry_value·목록(title)·화면용 표(merge, 그 신고만)를 함께 커밋한다(S-12).
  네트워크(카카오 지오코딩, 사진 촬영 시각)는 트랜잭션을 열기 전에 끝낸다(S-7·S-8).
- 변경 판정은 모바일 `_syncedAtTrackedKeys` 와 같은 열(상세 사이트 열 + category + entry_value + 본문)만 보고,
  NULL 과 '' 는 같게 본다(둘 다 '값 없음' — S-5·S-6). 지오코딩·사진 열은 변경으로 치지 않는다.
- 목록의 식별 정보(상태·신고번호·신고명·신고일)는 빈 값으로 덮지 않는다(S-23). 만족도·별점은 결정 D-2·D-3.
- 화면용 표 = 목록 + 상세(사이트 원본) + 수정값 + 감시목록(계산) + 6개월 지난 첨부 가림. 전체 재생성과 1건 갱신이 같은 규칙이다(S-31).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from dateutil.relativedelta import relativedelta
from sqlalchemy import case, delete, select, update
from sqlalchemy.dialects.sqlite import insert

from core.database import models
from core.utils import logger

DETAIL_TABLES = {
    "traffic": models.detail_traffic_table,
    "parking": models.detail_parking_table,
    "other": models.detail_other_table,
}
MERGE_TABLES = {
    "traffic": models.merge_traffic_table,
    "parking": models.merge_parking_table,
    "other": models.merge_other_table,
}
GEO_COLUMNS = ("주소정규화", "행정구역", "위도", "경도", "지오코딩상태")
PHOTO_COLUMNS = ("사진_첫촬영", "사진_끝촬영", "사진_촬영수")
DERIVED_DETAIL_COLUMNS = frozenset(GEO_COLUMNS) | frozenset(PHOTO_COLUMNS) | {"synced_at"}
DETAIL_SITE_COLUMNS = tuple(c.name for c in models.detail_traffic_table.columns if c.name != "ID" and c.name not in DERIVED_DETAIL_COLUMNS)
# 모바일 LocalDbService._syncedAtTrackedKeys 와 같은 목록(category·entry_value·본문은 따로 비교). 계약의 change_tracked 와 테스트로 묶는다.
CHANGE_TRACKED_COLUMNS = DETAIL_SITE_COLUMNS
TITLE_IDENTITY_COLUMNS = ("상태", "신고번호", "신고명", "신고일")
OVERRIDABLE_COLUMNS = frozenset(DETAIL_SITE_COLUMNS)
ATTACHMENT_COLUMNS = ("지도", "첨부사진", "첨부파일")
EXPIRED_ATTACHMENT = "6개월 초과"


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def comparable(value) -> str:
    """변경 판정용. NULL 과 '' 는 같게, 3.0 과 3 은 같게."""
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if value.is_integer():
            return str(int(value))
    return str(value)


def _clean(value):
    """pandas 가 빈 칸을 NaN 으로 줄 때가 있어 None 으로 돌린다. 그 밖의 값은 그대로."""
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


@dataclass
class CrawledDetail:
    id: str
    category: str
    detail: dict
    entry_value: str | None = None
    progress_status: str | None = None
    title_fields: dict | None = None
    raw_content: str | None = None
    raw_type: str = ""

    @classmethod
    def from_legacy_tuple(cls, item) -> "CrawledDetail":
        """detail_to_sql 의 2~7 튜플 (df, category, entry_value, progress_status, title_fields, raw_content, raw_type)."""
        fields = list(item) + [None] * (7 - len(item))
        df, category, entry_value, progress_status, title_fields, raw_content, raw_type = fields[:7]
        rows = df.to_dict("records")
        if not rows:
            raise ValueError("빈 상세 데이터")
        row = {k: _clean(v) for k, v in rows[0].items()}
        return cls(
            id=str(row["ID"]), category=category if category in DETAIL_TABLES else "other", detail=row,
            entry_value=entry_value, progress_status=progress_status, title_fields=title_fields,
            raw_content=raw_content, raw_type=raw_type or "",
        )


@dataclass
class SaveResult:
    changed: list[dict] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    saved: int = 0


# ── 저장 ─────────────────────────────────────────────────────────────────────

def _find_existing(conn, record_id: str):
    for category, table in DETAIL_TABLES.items():
        row = conn.execute(select(table).where(table.c.ID == record_id)).mappings().first()
        if row is not None:
            return category, dict(row)
    return None, None


def _prefetch_derived(engine, rec: CrawledDetail, existing: dict | None) -> dict:
    """트랜잭션 밖에서 계산값(지오코딩·사진 촬영 시각)을 준비한다."""
    from services import geocode_service, photo_capture_time

    address = rec.detail.get("위반장소") or ""
    try:
        geo = geocode_service.prepare_geo_payload(engine, address, existing_record=existing)
    except Exception as exc:
        logger.LoggerFactory.logbot.warning(f"[geocode] ID {rec.id} 지오코딩 준비 실패: {exc}")
        existing_address = (existing or {}).get("주소정규화") or (existing or {}).get("위반장소") or ""
        if existing and geocode_service.normalize_address(existing_address) == geocode_service.normalize_address(address):
            geo = geocode_service.extract_geo_payload(existing, fallback_address=address)
        else:
            geo = geocode_service.build_pending_geo_payload(address, status="error")

    photo = {name: (existing or {}).get(name) for name in PHOTO_COLUMNS}
    if photo["사진_촬영수"] is None and photo_capture_time.is_parking_report(rec.category, rec.entry_value):
        try:
            collected = photo_capture_time.collect(rec.detail.get("첨부사진"))
        except Exception as exc:  # fixture 차단 포함 — 크롤링은 멈추지 않는다
            logger.LoggerFactory.logbot.warning(f"[photo] ID {rec.id} 촬영 시각 수집 실패: {exc}")
            collected = None
        if collected is not None:
            photo = collected
    return {**{c: geo.get(c) for c in GEO_COLUMNS}, **photo}


def _save_entry_value(conn, rec: CrawledDetail) -> bool:
    if rec.entry_value is None:
        return False
    table = models.entry_value_table
    previous = conn.execute(select(table.c.entry_value).where(table.c.ID == rec.id)).scalar()
    stmt = insert(table).values(ID=rec.id, entry_value=rec.entry_value)
    conn.execute(stmt.on_conflict_do_update(index_elements=["ID"], set_={"entry_value": rec.entry_value}))
    return previous is not None and previous != rec.entry_value


def _save_raw(conn, rec: CrawledDetail, now_ms: int) -> bool:
    if rec.raw_content is None or not str(rec.raw_content).strip():
        return False
    table = models.raw_content_table
    previous = conn.execute(select(table).where(table.c.ID == rec.id)).mappings().first()
    content, raw_type = str(rec.raw_content), str(rec.raw_type or "")
    changed = previous is not None and (previous["raw_content"] != content or (previous["raw_type"] or "") != raw_type)
    saved_at = now_ms if previous is None or changed or previous["saved_at"] is None else previous["saved_at"]
    stmt = insert(table).values(ID=rec.id, raw_content=content, raw_type=raw_type, saved_at=saved_at)
    conn.execute(stmt.on_conflict_do_update(index_elements=["ID"], set_={"raw_content": content, "raw_type": raw_type, "saved_at": saved_at}))
    return changed


def _update_title(conn, rec: CrawledDetail) -> None:
    title = models.title_table
    fields = rec.title_fields
    if not fields:
        from core.database.database import _TITLE_STATUS_FROM_PROGRESS  # 레거시 경로(title_fields 없음)의 상태 대응표

        status = _TITLE_STATUS_FROM_PROGRESS.get(rec.progress_status)
        if status:
            conn.execute(update(title).where(title.c.ID == rec.id).where(title.c.상태 == "진행").values(상태=status))
        return
    values = {c: fields[c] for c in TITLE_IDENTITY_COLUMNS if fields.get(c) not in (None, "")}
    poll = fields.get("만족도조사여부") or ""
    # 별점: 상세 응답의 점수(파서)나 만족도 조회 결과(detail_pipeline)가 있을 때만 붙는다. 사유는 조회가 성공했을 때만 붙는다.
    has_rating = "별점" in fields
    if has_rating and poll == "참여 가능":
        values["만족도조사여부"] = poll  # 확정 미참여 재분류(조회가 미참여를 확정)
    elif poll:
        values["만족도조사여부"] = case((title.c.만족도조사여부 == "참여 완료", title.c.만족도조사여부), else_=poll)
    if has_rating:  # 결정 D-2: 사이트 값이 있을 때만 바꾼다
        values["별점"] = fields.get("별점")
    if "별점사유" in fields:  # 조회 실패면 사유 키가 없다 → 기존 사유 유지
        values["별점사유"] = fields.get("별점사유")
    if values:
        conn.execute(update(title).where(title.c.ID == rec.id).values(**values))


def _save_one(engine, rec: CrawledDetail) -> dict | None:
    table = DETAIL_TABLES[rec.category]
    with engine.connect() as read:
        _, existing_before = _find_existing(read, rec.id)
    derived = _prefetch_derived(engine, rec, existing_before)

    with engine.begin() as conn:
        now_ms = _now_ms()
        existing_category, existing = _find_existing(conn, rec.id)
        site = {c: _clean(rec.detail.get(c)) for c in DETAIL_SITE_COLUMNS}
        raw_changed = _save_raw(conn, rec, now_ms)
        entry_changed = _save_entry_value(conn, rec)

        if existing is None:
            change = {"id": rec.id, "change_type": "신규"}
        elif (existing_category != rec.category or raw_changed or entry_changed
              or any(comparable(existing.get(c)) != comparable(site[c]) for c in CHANGE_TRACKED_COLUMNS)):
            change = {"id": rec.id, "change_type": "변경"}
        else:
            change = None

        row = {"ID": rec.id, **site, **derived, "synced_at": now_ms if change else existing.get("synced_at")}
        if existing_category and existing_category != rec.category:
            conn.execute(delete(DETAIL_TABLES[existing_category]).where(DETAIL_TABLES[existing_category].c.ID == rec.id))
        stmt = insert(table).values(**row)
        conn.execute(stmt.on_conflict_do_update(index_elements=["ID"], set_={k: v for k, v in row.items() if k != "ID"}))
        _update_title(conn, rec)
        refresh_merge_rows(conn, [rec.id])
    return change


def save_crawled(engine, records, *, refresh_duplicates: bool = True) -> SaveResult:
    result = SaveResult()
    for rec in records:
        try:
            change = _save_one(engine, rec)
        except Exception as exc:
            result.failed.append((rec.id, repr(exc)))
            logger.LoggerFactory.logbot.error(f"ID {rec.id} 저장 실패: {exc}")
            continue
        result.saved += 1
        if change:
            result.changed.append(change)
    if result.saved and refresh_duplicates:
        from core.database import database
        database._refresh_duplicate_groups(engine)
    if result.failed:
        logger.LoggerFactory.logbot.error(
            f"상세 저장 실패 {len(result.failed)}건: " + ", ".join(rid for rid, _ in result.failed[:20])
        )
    return result


# ── 화면용 표(merge) ─────────────────────────────────────────────────────────

def _merge_select(detail):
    title = models.title_table
    merge_columns = [c.name for c in models.merge_traffic_table.columns]
    cols = [title.c[name] if name in title.c else detail.c[name] for name in merge_columns]
    return merge_columns, select(*cols).select_from(title.join(detail, title.c.ID == detail.c.ID))


def _attachment_cutoff() -> str:
    return (datetime.now() - relativedelta(months=6)).strftime("%Y-%m-%d")


def refresh_merge_rows(conn, ids=None) -> None:
    """ids=None 이면 전체, 아니면 그 신고만 화면용 표를 다시 만든다(같은 규칙)."""
    ids = list(ids) if ids is not None else None
    for category, merge in MERGE_TABLES.items():
        detail = DETAIL_TABLES[category]
        conn.execute(delete(merge) if ids is None else delete(merge).where(merge.c.ID.in_(ids)))
        columns, stmt = _merge_select(detail)
        if ids is not None:
            stmt = stmt.where(detail.c.ID.in_(ids))
        conn.execute(merge.insert().from_select(columns, stmt))
    _apply_overrides(conn, ids)
    _apply_attachment_expiry(conn, ids)
    _apply_watch_flags(conn, ids)


def set_overrides(conn, record_id: str, provided: dict, site: dict) -> None:
    """사용자 편집값을 수정값 표에 쓴다(결정 D-1). 보낸 열만 다루고(S-2), 사이트 원본과 같아지면 수정값을 지운다(되돌리기).
    화면용 표의 "6개월 초과" 가림 글자는 수정값으로 받지 않는다(S-3)."""
    table = models.report_override_table
    now_ms = _now_ms()
    for column, value in provided.items():
        if column not in OVERRIDABLE_COLUMNS:
            continue
        if column in ATTACHMENT_COLUMNS and value == EXPIRED_ATTACHMENT:
            continue
        conn.execute(delete(table).where(table.c.ID == record_id).where(table.c.column_name == column))
        if comparable(value) != comparable(site.get(column)):
            conn.execute(table.insert().values(ID=record_id, column_name=column, value=value, updated_at=now_ms))


def _apply_overrides(conn, ids) -> None:
    table = models.report_override_table
    query = select(table.c.ID, table.c.column_name, table.c.value)
    if ids is not None:
        query = query.where(table.c.ID.in_(ids))
    for record_id, column, value in conn.execute(query):
        if column not in OVERRIDABLE_COLUMNS:
            logger.LoggerFactory.logbot.warning(f"[override] 허용되지 않은 열 무시: {record_id}.{column}")
            continue
        values = {column: value}
        if column == "위반장소":
            values.update(_geo_for_overridden_address(conn, value))
        for merge in MERGE_TABLES.values():
            conn.execute(update(merge).where(merge.c.ID == record_id).values(values))


def _geo_for_overridden_address(conn, address) -> dict:
    """고친 주소의 좌표는 지오코딩 캐시에서만 찾는다(네트워크 없음). 없으면 대기 상태로 둔다."""
    from services import geocode_service

    normalized = geocode_service.normalize_address(address)
    cache = models.geocode_cache_table
    row = conn.execute(select(cache).where(cache.c["주소정규화"] == normalized)).mappings().first() if normalized else None
    if row is None:
        return {"주소정규화": normalized, "행정구역": None, "위도": None, "경도": None, "지오코딩상태": "pending" if normalized else ""}
    return {"주소정규화": normalized, "행정구역": row["행정구역"], "위도": row["위도"], "경도": row["경도"], "지오코딩상태": row["상태"]}


def _apply_attachment_expiry(conn, ids) -> None:
    cutoff = _attachment_cutoff()
    for merge in MERGE_TABLES.values():
        stmt = update(merge).where(merge.c.신고일 < cutoff)
        if ids is not None:
            stmt = stmt.where(merge.c.ID.in_(ids))
        conn.execute(stmt.values(**{c: EXPIRED_ATTACHMENT for c in ATTACHMENT_COLUMNS}))


def _apply_watch_flags(conn, ids) -> None:
    watched = select(models.watchlist_table.c["신고번호"])
    for table in (models.title_table, *MERGE_TABLES.values()):
        stmt = update(table).values(감시목록=case((table.c.신고번호.in_(watched), "Y"), else_="N"))
        if ids is not None:
            stmt = stmt.where(table.c.ID.in_(ids))
        conn.execute(stmt)
