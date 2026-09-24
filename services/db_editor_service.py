from __future__ import annotations

from sqlalchemy import desc, select

from core.database import models


_CATEGORY_TABLES = {
    "traffic": (models.merge_traffic_table, models.detail_traffic_table),
    "parking": (models.merge_parking_table, models.detail_parking_table),
    "other": (models.merge_other_table, models.detail_other_table),
}

_TITLE_FIELDS = ["상태", "신고번호", "신고명", "신고일", "만족도조사여부", "감시목록"]
_DETAIL_FIELDS = [
    "처리상태",
    "차량번호",
    "위반법규",
    "범칙금_과태료",
    "벌점",
    "처리기관",
    "담당자",
    "답변일",
    "발생일자",
    "발생시각",
    "위반장소",
    "종결여부",
    "신고내용",
    "처리내용",
    "지도",
    "첨부사진",
    "첨부파일",
]
_FINE_INFO_EXAMPLE = "예: 과태료: 40,000원 / 범칙금: 30,000원 / 경고 / 미확인"


def get_category_tables(category: str):
    return _CATEGORY_TABLES.get((category or "").strip().lower())


def get_editor_schema() -> dict:
    return {
        "title_fields": list(_TITLE_FIELDS),
        "detail_fields": list(_DETAIL_FIELDS),
        "fine_info_example": _FINE_INFO_EXAMPLE,
    }


def list_records(engine, category: str) -> list[dict]:
    tables = get_category_tables(category)
    if not tables:
        return []
    merge_tbl, _ = tables
    with engine.connect() as conn:
        rows = conn.execute(
            select(merge_tbl).order_by(desc(merge_tbl.c["신고번호"]))
        ).fetchall()
    return [dict(row._mapping) for row in rows]


def get_record(engine, category: str, record_id: str) -> dict | None:
    tables = get_category_tables(category)
    if not tables:
        return None
    merge_tbl, _ = tables
    with engine.connect() as conn:
        row = conn.execute(
            select(merge_tbl).where(merge_tbl.c.ID == record_id)
        ).first()
    if not row:
        return None
    return dict(row._mapping)


def update_record(engine, category: str, record_id: str, values: dict) -> bool:
    """편집값은 사용자 수정값 표(mysafety_report_override)에 저장한다(결정 D-1, 저장 계층 재설계 R2).
    사이트 원본(detail)은 건드리지 않아 재크롤링이 편집을 되돌리지 않는다(S-1). 보낸 필드만 반영한다(S-2).
    화면용 표는 같은 트랜잭션에서 다시 만든다."""
    from core.storage import reports_repo

    tables = get_category_tables(category)
    if not tables:
        return False
    _merge_tbl, detail_tbl = tables
    provided = {field: values[field] for field in _DETAIL_FIELDS if field in values}

    with engine.begin() as conn:
        site = conn.execute(select(detail_tbl).where(detail_tbl.c.ID == record_id)).mappings().first()
        if site is None:
            return False
        reports_repo.set_overrides(conn, record_id, provided, dict(site))
        reports_repo.refresh_merge_rows(conn, [record_id])
    return True
