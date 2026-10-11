from __future__ import annotations

from sqlalchemy import desc, select

from core.database import models
from services import report_category

_EFFECTIVE = models.report_effective_table
_REPORT = models.report_table

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


def categories_for(category: str) -> tuple[str, ...] | None:
    """편집기 분류 값 → 실제 분류 묶음. 모르면 None. 'other' 는 5분류의 기타 하나다(구앱 API 는 호출자가 묶음을 넘긴다)."""
    value = (category or "").strip().lower()
    return (value,) if value in report_category.CATEGORIES else None


def get_editor_schema() -> dict:
    return {
        "title_fields": list(_TITLE_FIELDS),
        "detail_fields": list(_DETAIL_FIELDS),
        "fine_info_example": _FINE_INFO_EXAMPLE,
    }


def list_records(engine, category, *, summary: bool = False) -> list[dict]:
    categories = categories_for(category) if isinstance(category, str) else tuple(category or ())
    if not categories:
        return []
    fields = [_EFFECTIVE.c[name] for name in ('ID', '신고번호', '신고일', '신고명', '차량번호',
              '처리기관', '담당자', '처리상태', '범칙금_과태료', '답변일')] if summary else [_EFFECTIVE]
    with engine.connect() as conn:
        rows = conn.execute(
            select(*fields).where(_EFFECTIVE.c.category.in_(categories)).order_by(desc(_EFFECTIVE.c["신고번호"]))
        ).fetchall()
    return [dict(row._mapping) for row in rows]


def get_record(engine, category, record_id: str) -> dict | None:
    """화면용 행. category 는 확인용(그 분류의 신고가 아니면 None) — 구앱 'other' 묶음은 호출자가 tuple 로 넘긴다."""
    categories = categories_for(category) if isinstance(category, str) else tuple(category or ())
    if not categories:
        return None
    with engine.connect() as conn:
        row = conn.execute(
            select(_EFFECTIVE).where(_EFFECTIVE.c.ID == record_id, _EFFECTIVE.c.category.in_(categories))
        ).first()
    if not row:
        return None
    return dict(row._mapping)


def get_edit_state(engine, category: str, record_id: str) -> dict:
    """편집 화면용: 어떤 필드를 사용자가 고쳤는지와 사이트 원본 값(결정 D-1, 저장 계층 재설계 R4).
    반환: {"overrides": {열: {"value", "updated_at"}}, "site_values": {열: 원본}} — 고친 열만."""
    if not (categories_for(category) if isinstance(category, str) else category):
        return {"overrides": {}, "site_values": {}}
    detail_tbl = _REPORT
    override = models.report_override_table
    with engine.connect() as conn:
        rows = conn.execute(
            select(override.c.column_name, override.c.value, override.c.updated_at).where(override.c.ID == record_id)
        ).all()
        site = conn.execute(select(detail_tbl).where(detail_tbl.c.ID == record_id)).mappings().first() or {}
    overrides = {r.column_name: {"value": r.value, "updated_at": r.updated_at} for r in rows if r.column_name in _DETAIL_FIELDS}
    return {"overrides": overrides, "site_values": {c: site.get(c) for c in overrides}}


class InvalidEditorValue(ValueError):
    """편집 필드 값이 문자열·숫자·null 이 아니다 — API 는 400 으로 답한다(기술일지 A1-10, data-contracts '잘못된 필드 타입은 400')."""


def _check_editor_values(provided: dict) -> None:
    bad = [field for field, value in provided.items()
           if value is not None and (isinstance(value, bool) or not isinstance(value, (str, int, float)))]
    if bad:
        raise InvalidEditorValue("문자열·숫자·null 만 저장할 수 있습니다: " + ", ".join(sorted(bad)))


def update_record(engine, category: str, record_id: str, values: dict) -> bool:
    """편집값은 사용자 수정값 표(mysafety_report_override)에 저장한다(결정 D-1, 저장 계층 재설계 R2).
    사이트 원본(mysafety_report)은 건드리지 않아 재크롤링이 편집을 되돌리지 않는다(S-1). 보낸 필드만 반영한다(S-2).
    화면용 표는 같은 트랜잭션에서 다시 만든다."""
    from core.storage import reports_repo

    categories = categories_for(category) if isinstance(category, str) else tuple(category or ())
    if not categories:
        return False
    detail_tbl = _REPORT
    provided = {field: values[field] for field in _DETAIL_FIELDS if field in values}
    _check_editor_values(provided)

    with engine.begin() as conn:
        site = conn.execute(select(detail_tbl).where(
            detail_tbl.c.ID == record_id, detail_tbl.c.category.in_(categories))).mappings().first()
        if site is None:
            return False
        reports_repo.set_overrides(conn, record_id, provided, dict(site))
        reports_repo.refresh_effective_rows(conn, [record_id])
    return True
