from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from core.utils.templating import templates
from core.database.engine import get_engine
from services import db_editor_service

router = APIRouter(prefix="/db-editor", tags=["db-editor"])
engine = get_engine()


@router.get("", response_class=HTMLResponse)
async def db_editor_list(request: Request, category: str = "traffic"):
    if not db_editor_service.get_category_tables(category):
        category = "traffic"
    records = db_editor_service.list_records(engine, category)
    return templates.TemplateResponse(request, "db_editor.html", {
        "title": "데이터 수정",
        "records": records,
        "category": category,
    })


@router.get("/{category}/{record_id}", response_class=HTMLResponse)
async def db_editor_form(request: Request, category: str, record_id: str):
    if not db_editor_service.get_category_tables(category):
        return RedirectResponse("/db-editor")
    record = db_editor_service.get_record(engine, category, record_id)
    if not record:
        return RedirectResponse(f"/db-editor?category={category}")
    schema = db_editor_service.get_editor_schema()
    return templates.TemplateResponse(request, "db_editor_form.html", {
        "title": f"데이터 수정 — {record.get('신고번호', record_id)}",
        "record": record,
        "category": category,
        "title_fields": schema["title_fields"],
        "detail_fields": schema["detail_fields"],
        "fine_info_example": schema["fine_info_example"],
    })


@router.post("/{category}/{record_id}")
async def db_editor_save(request: Request, category: str, record_id: str):
    if not db_editor_service.get_category_tables(category):
        return RedirectResponse("/db-editor", status_code=303)
    form = await request.form()
    db_editor_service.update_record(engine, category, record_id, dict(form))
    return RedirectResponse(f"/db-editor?category={category}", status_code=303)
