from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from core.utils.templating import templates
from core.database.engine import get_engine
from services import db_editor_service

router = APIRouter(prefix="/db-editor", tags=["db-editor"])
engine = get_engine()


@router.get("", response_class=HTMLResponse)
def db_editor_list(request: Request, category: str = "traffic"):
    if not db_editor_service.categories_for(category):
        category = "traffic"
    records = db_editor_service.list_records(engine, category, summary=True)
    return templates.TemplateResponse(request, "db_editor.html", {
        "title": "데이터 수정",
        "records": records,
        "category": category,
    })


@router.get("/{category}/{record_id}", response_class=HTMLResponse)
def db_editor_form(request: Request, category: str, record_id: str):
    if not db_editor_service.categories_for(category):
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
        **db_editor_service.get_edit_state(engine, category, record_id),
    })


@router.post("/{category}/{record_id}")
async def db_editor_save(request: Request, category: str, record_id: str):
    if not db_editor_service.categories_for(category):
        return RedirectResponse("/db-editor", status_code=303)
    form = await request.form()
    values = dict(form)
    # HTML textarea submits CRLF. An unrelated edit must not create a law
    # override merely because the stored field used LF (or legacy CRLF).
    if isinstance(values.get("위반법규"), str):
        current = await run_in_threadpool(db_editor_service.get_record, engine, category, record_id)
        submitted = values["위반법규"].replace("\r\n", "\n").replace("\r", "\n")
        existing = (current or {}).get("위반법규")
        if isinstance(existing, str) and submitted == existing.replace("\r\n", "\n").replace("\r", "\n"):
            del values["위반법규"]
        else:
            values["위반법규"] = submitted
    await run_in_threadpool(db_editor_service.update_record, engine, category, record_id, values)
    return RedirectResponse(f"/db-editor?category={category}", status_code=303)
