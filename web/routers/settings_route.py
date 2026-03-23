from fastapi import APIRouter, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
import settings.settings as app_settings
import os
import re

from core.utils.templating import templates

router = APIRouter(prefix="/settings")

@router.get("/")
async def view_settings(request: Request):
    import sys
    app_settings._instance.load() # Reload to show latest values

    # Determine smart default for chrome_mode based on runtime environment
    raw_chrome_mode = app_settings.config.get('SELENIUM', 'chrome_mode', fallback=None)
    if raw_chrome_mode is None:
        if os.path.exists('/.dockerenv'):
            default_chrome_mode = 'hub'
        elif getattr(sys, 'frozen', False):
            default_chrome_mode = 'desktop'
        else:
            default_chrome_mode = 'hub'
    else:
        default_chrome_mode = raw_chrome_mode

    auth_path = os.path.join(app_settings._instance.datapath, 'auth', 'gspread.json')
    return templates.TemplateResponse(request, "settings.html", {
        "title": "설정",
        "admin_username": request.session.get("admin_username", ""),
        "admin_error": request.query_params.get("admin_error"),
        "admin_success": request.query_params.get("admin_success"),
        "username": app_settings.config.get('LOGIN', 'username', fallback=""),
        "password": app_settings.password or "",
        "telegram_token": app_settings.config.get('TELEGRAM', 'telegram_token', fallback=""),
        "chat_id": app_settings.config.get('TELEGRAM', 'chat_id', fallback=""),
        "sheet_key": app_settings.config.get('GOOGLESHEET', 'sheet_key', fallback=""),
        "normalize_police": app_settings.config.getboolean('SETTINGS', 'normalize_police', fallback=False),
        "exclude_withdraw": app_settings.config.getboolean('SETTINGS', 'exclude_withdraw', fallback=False),
        "retry_interval": int(app_settings.config.get('SETTINGS', 'retry_interval', fallback=60)),
        "max_retry_attemps": int(app_settings.config.get('SETTINGS', 'max_retry_attemps', fallback=10)),
        "log_level": app_settings.config.get('SETTINGS', 'log_level', fallback="INFO"),
        "chrome_mode": default_chrome_mode,
        "remote_debug_port": app_settings.config.get('SELENIUM', 'remote_debug_port', fallback="127.0.0.1:9222"),
        "headless": app_settings.config.getboolean('SELENIUM', 'headless', fallback=False),
        "scheduler_enabled": app_settings.config.getboolean('SCHEDULER', 'enabled', fallback=False),
        "scheduler_mode": app_settings.config.get('SCHEDULER', 'mode', fallback='interval'),
        "scheduler_interval_hours": int(app_settings.config.get('SCHEDULER', 'interval_hours', fallback=24)),
        "scheduler_cron_times": app_settings.config.get('SCHEDULER', 'cron_times', fallback='09:00'),
        "scheduler_interval_start": app_settings.config.get('SCHEDULER', 'interval_start', fallback='00:00'),
        "phone_number": app_settings.config.get('RATING', 'phone_number', fallback=''),
        "remotepath": app_settings.config.get('SELENIUM', 'remotepath', fallback="http://localhost:4444/wd/hub"),
        "google_json_exists": os.path.isfile(auth_path),
    })

@router.post("/save")
async def save_settings(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    telegram_token: str = Form(""),
    chat_id: str = Form(""),
    sheet_key: str = Form(""),
    normalize_police: bool = Form(False),
    exclude_withdraw: bool = Form(False),
    retry_interval: int = Form(60),
    max_retry_attemps: int = Form(10),
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
    remotepath: str = Form("http://localhost:4444/wd/hub")
):
    # Regex to extract Google Spreadsheet ID from full URL
    match = re.search(r'/d/([a-zA-Z0-9-_]+)', sheet_key)
    if match:
        sheet_key = match.group(1)

    app_settings._instance.update_config('LOGIN', 'username', username)
    app_settings._instance.update_config('LOGIN', 'password', password)

    app_settings._instance.update_config('TELEGRAM', 'telegram_token', telegram_token)
    app_settings._instance.update_config('TELEGRAM', 'chat_id', chat_id)

    app_settings._instance.update_config('GOOGLESHEET', 'sheet_key', sheet_key)
    
    app_settings._instance.update_config('SELENIUM', 'remotepath', remotepath)
    app_settings._instance.update_config('SELENIUM', 'chrome_mode', chrome_mode)
    app_settings._instance.update_config('SELENIUM', 'headless', headless)
    app_settings._instance.update_config('SELENIUM', 'remote_debug_port', remote_debug_port)

    app_settings._instance.update_config('SCHEDULER', 'enabled', scheduler_enabled)
    app_settings._instance.update_config('SCHEDULER', 'mode', scheduler_mode)
    app_settings._instance.update_config('SCHEDULER', 'interval_hours', scheduler_interval_hours)
    app_settings._instance.update_config('SCHEDULER', 'cron_times', scheduler_cron_times)
    app_settings._instance.update_config('SCHEDULER', 'interval_start', scheduler_interval_start)

    app_settings._instance.update_config('RATING', 'phone_number', re.sub(r'[^0-9]', '', phone_number))

    app_settings._instance.update_config('SETTINGS', 'normalize_police', normalize_police)
    app_settings._instance.update_config('SETTINGS', 'exclude_withdraw', exclude_withdraw)
    app_settings._instance.update_config('SETTINGS', 'retry_interval', retry_interval)
    app_settings._instance.update_config('SETTINGS', 'max_retry_attemps', max_retry_attemps)
    app_settings._instance.update_config('SETTINGS', 'log_level', log_level)

    app_settings._instance.save()

    try:
        from core.utils import scheduler
        scheduler.update_jobs()
    except Exception as e:
        from core.utils import logger
        logger.LoggerFactory.logbot.error(f"스케줄러 업데이트 실패: {e}")

    return RedirectResponse(url="/settings?saved=true", status_code=303)

@router.post("/upload_json")
async def upload_json(file: UploadFile = File(...)):
    if not file.filename.endswith('.json'):
        return RedirectResponse(url="/settings?error=invalid_file", status_code=303)

    # google_api_auth_file이 None일 수 있으므로 datapath 기준으로 직접 경로 계산
    auth_path = os.path.join(app_settings._instance.datapath, 'auth', 'gspread.json')
    os.makedirs(os.path.dirname(auth_path), exist_ok=True)

    contents = await file.read()
    with open(auth_path, "wb") as f:
        f.write(contents)

    # 설정 인스턴스 리로드하여 google_sheet_enabled 등 갱신
    app_settings._instance.load()

    return RedirectResponse(url="/settings?saved=true", status_code=303)
