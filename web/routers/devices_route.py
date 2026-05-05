from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from core.utils.templating import templates
from core.database import database
from core.database.engine import get_engine

router = APIRouter(prefix="/devices")


@router.get("/", response_class=HTMLResponse)
async def view_devices(request: Request):
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
async def get_connected_clients(request: Request):
    from services.ws_manager import ws_manager
    return JSONResponse(ws_manager.get_connected_clients())


@router.post("/api-keys/create")
async def create_api_key(request: Request, key_name: str = Form(...)):
    engine = get_engine()
    new_key = database.create_api_key(engine, key_name.strip() or "unnamed")
    return JSONResponse({"key": new_key, "name": key_name})


@router.post("/api-keys/delete")
async def delete_api_key(request: Request, key: str = Form(...)):
    engine = get_engine()
    database.delete_api_key(engine, key)
    return RedirectResponse(url="/devices", status_code=303)
