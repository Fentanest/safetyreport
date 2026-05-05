from fastapi import APIRouter, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse, FileResponse
import os
from core.utils.templating import templates
from services import file_service

router = APIRouter(prefix="/file-browser", tags=["file-browser"])

@router.get("", response_class=HTMLResponse)
async def list_files(request: Request):
    return templates.TemplateResponse(request, "file_browser.html", {
        "title": "파일 브라우저",
        "files": file_service.list_browser_groups()
    })

@router.get("/download")
async def download_file(path: str):
    try:
        resolved = file_service.ensure_browser_file(path)
    except PermissionError:
        raise HTTPException(status_code=403, detail="Access denied")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="File not found")

    def iterfile():
        with open(resolved, mode="rb") as file_like:
            yield from file_like

    return StreamingResponse(
        iterfile(), 
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={os.path.basename(resolved)}"}
    )

@router.post("/download-multi")
async def download_multi(request: Request):
    data = await request.json()
    paths = data.get("paths", [])
    
    if not paths:
        raise HTTPException(status_code=400, detail="No files selected")
    zip_buffer, filename = file_service.build_download_zip(paths)
    
    return StreamingResponse(
        zip_buffer,
        media_type="application/x-zip-compressed",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.delete("/delete")
async def delete_file(path: str):
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
    data = await request.json()
    paths = data.get("paths", [])
    if not paths:
        raise HTTPException(status_code=400, detail="No files selected")
    deleted_count, errors = file_service.delete_files(paths)
                
    return {
        "status": "success" if not errors else "partial_success",
        "deleted_count": deleted_count,
        "errors": errors
    }

@router.post("/delete-all")
async def delete_all(request: Request):
    data = await request.json()
    target = data.get("target") # "logs" or "results"
    try:
        deleted_count = file_service.delete_all_in_target(target)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid target")

    return {"status": "success", "deleted_count": deleted_count}
