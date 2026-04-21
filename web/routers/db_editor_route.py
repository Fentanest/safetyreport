from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from core.utils.templating import templates
from core.database.models import (
    merge_traffic_table, merge_parking_table, merge_other_table,
    detail_traffic_table, detail_parking_table, detail_other_table
)
from sqlalchemy import create_engine, select, update
import settings.settings as settings

router = APIRouter(prefix="/db-editor", tags=["db-editor"])
engine = create_engine(f"sqlite:///{settings.db_path}", connect_args={"check_same_thread": False})

_CATEGORY_TABLES = {
    "traffic": (merge_traffic_table, detail_traffic_table),
    "parking": (merge_parking_table, detail_parking_table),
    "other":   (merge_other_table,   detail_other_table),
}

_TITLE_FIELDS = ["상태", "신고번호", "신고명", "신고일", "만족도조사여부", "감시목록"]
_DETAIL_FIELDS = [
    "처리상태", "차량번호", "위반법규", "범칙금_과태료", "벌점",
    "처리기관", "담당자", "답변일", "발생일자", "발생시각", "위반장소",
    "종결여부", "신고내용", "처리내용", "지도", "첨부사진", "첨부파일",
]


@router.get("", response_class=HTMLResponse)
async def db_editor_list(request: Request, category: str = "traffic"):
    if category not in _CATEGORY_TABLES:
        category = "traffic"
    merge_tbl, _ = _CATEGORY_TABLES[category]
    with engine.connect() as conn:
        rows = conn.execute(
            select(merge_tbl).order_by(merge_tbl.c["신고일"].desc())
        ).fetchall()
    records = [dict(r._mapping) for r in rows]
    return templates.TemplateResponse(request, "db_editor.html", {
        "title": "데이터 수정",
        "records": records,
        "category": category,
    })


@router.get("/{category}/{record_id}", response_class=HTMLResponse)
async def db_editor_form(request: Request, category: str, record_id: str):
    if category not in _CATEGORY_TABLES:
        return RedirectResponse("/db-editor")
    merge_tbl, _ = _CATEGORY_TABLES[category]
    with engine.connect() as conn:
        row = conn.execute(
            select(merge_tbl).where(merge_tbl.c.ID == record_id)
        ).first()
    if not row:
        return RedirectResponse(f"/db-editor?category={category}")
    record = dict(row._mapping)
    return templates.TemplateResponse(request, "db_editor_form.html", {
        "title": f"데이터 수정 — {record.get('신고번호', record_id)}",
        "record": record,
        "category": category,
        "title_fields": _TITLE_FIELDS,
        "detail_fields": _DETAIL_FIELDS,
    })


@router.post("/{category}/{record_id}")
async def db_editor_save(request: Request, category: str, record_id: str):
    if category not in _CATEGORY_TABLES:
        return RedirectResponse("/db-editor", status_code=303)
    merge_tbl, detail_tbl = _CATEGORY_TABLES[category]

    form = await request.form()
    detail_values = {f: form.get(f, "") for f in _DETAIL_FIELDS}

    with engine.begin() as conn:
        # mysafetydetail 갱신
        conn.execute(update(detail_tbl).where(detail_tbl.c.ID == record_id).values(**detail_values))
        # mysafetymerge의 detail 필드만 갱신 (title 필드는 크롤링 upsert에 맡김)
        conn.execute(update(merge_tbl).where(merge_tbl.c.ID == record_id).values(**detail_values))

    return RedirectResponse(f"/db-editor?category={category}", status_code=303)
