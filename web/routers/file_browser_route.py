from fastapi import APIRouter, Request, Form, HTTPException
from starlette.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse
import os
from core.utils.templating import templates
from core.utils.request_body import json_object, string_list
from core.utils.temporary_response import TemporaryFileResponse
from services import file_service

router = APIRouter(prefix="/file-browser", tags=["file-browser"])

@router.get("", response_class=HTMLResponse)
def list_files(request: Request, target: str = "results"):
    active_target = target if target in {"logs", "results"} else "results"
    return templates.TemplateResponse(request, "file_browser.html", {
        "title": "파일 브라우저",
        "files": file_service.list_browser_groups(),
        "active_target": active_target
    })

@router.get("/download")
def download_file(path: str):
    try:
        resolved = file_service.ensure_browser_file(path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Access denied")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")

    try:
        temporary, _cleanup = file_service.snapshot_live_log_if_needed(resolved)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail='Access denied') from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail='File not found') from exc
    return TemporaryFileResponse(temporary, filename=os.path.basename(resolved), media_type='application/octet-stream')

@router.post("/download-multi")
async def download_multi(request: Request):
    data = await json_object(request)
    paths = string_list(data, "paths")
    
    if not paths:
        raise HTTPException(status_code=400, detail="No files selected")
    try:
        archive_path, filename = await run_in_threadpool(file_service.build_download_zip, paths)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail="Access denied") from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return TemporaryFileResponse(archive_path, filename=filename, media_type="application/x-zip-compressed")


@router.delete("/delete")
def delete_file(path: str):
    try:
        file_service.delete_file(path)
        return {"status": "success", "message": "파일이 삭제되었습니다."}
    except PermissionError:
        raise HTTPException(status_code=403, detail="Access denied")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/delete-multi")
async def delete_multi(request: Request):
    data = await json_object(request)
    paths = string_list(data, "paths")
    if not paths:
        raise HTTPException(status_code=400, detail="No files selected")
    deleted_count, errors = await run_in_threadpool(file_service.delete_files, paths)
                
    return {
        "status": "success" if not errors else "partial_success",
        "deleted_count": deleted_count,
        "errors": errors
    }

@router.post("/delete-all")
async def delete_all(request: Request):
    data = await json_object(request)
    target = data.get("target") # "logs" or "results"
    try:
        deleted_count = await run_in_threadpool(file_service.delete_all_in_target, target)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid target")

    return {"status": "success", "deleted_count": deleted_count}
