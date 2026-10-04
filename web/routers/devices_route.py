from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from core.utils.templating import templates
from core.database import database
from core.database.engine import get_engine

router = APIRouter(prefix="/devices")


@router.get("/", response_class=HTMLResponse)
def view_devices(request: Request):
    from services.ws_manager import ws_manager
    engine = get_engine()
    api_keys = database.get_all_api_keys(engine)
    connected = ws_manager.get_connected_clients()
    return templates.TemplateResponse(request, "devices.html", {
        "title": "기기 연동",
        "api_keys": api_keys,
        "connected_clients": connected,
    })


@router.get("/connected-clients")
def get_connected_clients(request: Request):
    from services.ws_manager import ws_manager
    return JSONResponse(ws_manager.get_connected_clients())


@router.post("/api-keys/create")
def create_api_key(request: Request, key_name: str = Form(...)):
    engine = get_engine()
    new_key = database.create_api_key(engine, key_name.strip() or "unnamed")
    return JSONResponse({"key": new_key, "name": key_name})


@router.post("/api-keys/delete")
async def delete_api_key(request: Request, key: str = Form(...)):
    from services.ws_manager import ws_manager
    engine = get_engine()
    await run_in_threadpool(database.delete_api_key, engine, key)
    # 키를 지운 뒤 그 키로 열린 이벤트 WS 를 닫는다. 로그 WS 는 GateWatch 가 다음 확인 때 닫는다(기술일지 A1-02).
    await ws_manager.revoke_api_key(key)
    return RedirectResponse(url="/devices", status_code=303)
