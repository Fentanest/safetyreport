"""앱 설정 화면. 설정 명령·저장·후속 작업은 services/settings_service.py(EO R-13)."""
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

import settings.settings as app_settings  # 시험이 이 이름으로 설정 인스턴스를 바꾼다

__all__ = ["router", "app_settings"]
from core.utils import csrf
from core.utils.templating import templates
from services import settings_service

router = APIRouter(prefix="/settings")


@router.get("/")
def view_settings(request: Request):
    return templates.TemplateResponse(request, "settings.html", {
        "title": "앱 설정",
        **settings_service.view_values(),
        "csrf_token": csrf.get_or_create_token(request),
    })


@router.post("/save")
def save_settings(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    telegram_token: str = Form(""),
    chat_id: str = Form(""),
    sheet_key: str = Form(""),
    exclude_withdraw: bool = Form(False),
    use_representative_records: bool = Form(False),
    auto_export_excel: bool = Form(False),
    auto_export_sheet: bool = Form(False),
    retry_interval: int = Form(10),
    max_retry_attemps: int = Form(5),
    log_level: str = Form("INFO"),
    chrome_mode: str = Form("hub"),
    remote_debug_port: str = Form("9222"),
    headless: bool = Form(False),
    scheduler_enabled: bool = Form(False),
    scheduler_mode: str = Form("interval"),
    scheduler_interval_hours: int = Form(24),
    scheduler_cron_times: str = Form("09:00"),
    scheduler_interval_start: str = Form("00:00"),
    phone_number: str = Form(""),
    remotepath: str = Form("http://localhost:4444/wd/hub"),
    session_max_age: int = Form(10800),
    trusted_proxies: str = Form("")
):
    form = {key: value for key, value in locals().items() if key != "request"}
    settings_service.apply(settings_service.web_command(form))
    return RedirectResponse(url="/settings?saved=true", status_code=303)


@router.post("/upload_json")
async def upload_json(file: UploadFile = File(...)):
    if not (file.filename or '').lower().endswith('.json'):
        return RedirectResponse(url="/settings?error=invalid_file", status_code=303)
    contents = await file.read(settings_service.GOOGLE_CREDENTIAL_MAX_BYTES + 1)
    try:
        settings_service.validate_google_credential(contents)
    except settings_service.CredentialTooLarge:
        raise HTTPException(413, 'JSON credential file is too large') from None
    except settings_service.CredentialInvalid:
        raise HTTPException(400, 'Invalid service account JSON') from None
    await run_in_threadpool(settings_service.save_google_credential, contents)
    return RedirectResponse(url="/settings?saved=true", status_code=303)
