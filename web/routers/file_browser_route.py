from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
import os
import time
import zipfile
import io
from datetime import datetime

import settings.settings as settings

router = APIRouter(prefix="/file-browser", tags=["file-browser"])
templates = Jinja2Templates(directory="web/templates")

@router.get("", response_class=HTMLResponse)
async def list_files(request: Request):
    # settings에서 동적으로 경로를 가져옴
    targets = {
        "logs": settings.logpath,
        "results": settings.resultpath
    }
    
    files = {
        "logs": [],
        "results": []
    }
    
    for label, d in targets.items():
        if not os.path.exists(d):
            os.makedirs(d, exist_ok=True)
            
        for filename in os.listdir(d):
            path = os.path.join(d, filename)
            if os.path.isfile(path):
                stats = os.stat(path)
                files[label].append({
                    "name": filename,
                    "dir": label,
                    "path": path,
                    "size": stats.st_size,
                    "mtime": datetime.fromtimestamp(stats.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
                })
    
    # Sort each list by mtime descending
    for label in files:
        files[label].sort(key=lambda x: x['mtime'], reverse=True)
    
    return templates.TemplateResponse("file_browser.html", {
        "request": request,
        "title": "파일 브라우저",
        "files": files
    })

@router.get("/download")
async def download_file(path: str):
    allowed_dirs = [settings.logpath, settings.resultpath]
    if not any(os.path.abspath(path).startswith(os.path.abspath(d)) for d in allowed_dirs):
        raise HTTPException(status_code=403, detail="Access denied")
    
    if not os.path.exists(path) or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")
        
    def iterfile():
        with open(path, mode="rb") as file_like:
            yield from file_like

    return StreamingResponse(
        iterfile(), 
        media_type="application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename={os.path.basename(path)}"}
    )

@router.post("/download-multi")
async def download_multi(request: Request):
    data = await request.json()
    paths = data.get("paths", [])
    
    if not paths:
        raise HTTPException(status_code=400, detail="No files selected")
        
    allowed_dirs = [settings.logpath, settings.resultpath]
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
        for path in paths:
            is_allowed = any(os.path.abspath(path).startswith(os.path.abspath(d)) for d in allowed_dirs)
            if is_allowed and os.path.exists(path):
                zip_file.write(path, os.path.basename(path))
    
    zip_buffer.seek(0)
    
    filename = f"safetyreport_files_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    
    return StreamingResponse(
        zip_buffer,
        media_type="application/x-zip-compressed",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.delete("/delete")
async def delete_file(path: str):
    allowed_dirs = [settings.logpath, settings.resultpath]
    abs_path = os.path.abspath(path)
    if not any(abs_path.startswith(os.path.abspath(d)) for d in allowed_dirs):
        raise HTTPException(status_code=403, detail="Access denied")
    
    # 보호된 파일 (현재 로그) 체크
    current_log = os.path.abspath(os.path.join(settings.logpath, settings.logfile))
    if abs_path == current_log:
        raise HTTPException(status_code=400, detail="현재 사용 중인 로그 파일은 삭제할 수 없습니다.")

    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
        
    try:
        os.remove(path)
        return {"status": "success", "message": "파일이 삭제되었습니다."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/delete-multi")
async def delete_multi(request: Request):
    data = await request.json()
    paths = data.get("paths", [])
    if not paths:
        raise HTTPException(status_code=400, detail="No files selected")
        
    allowed_dirs = [settings.logpath, settings.resultpath]
    current_log = os.path.abspath(os.path.join(settings.logpath, settings.logfile))
    
    deleted_count = 0
    errors = []
    
    for path in paths:
        abs_path = os.path.abspath(path)
        is_allowed = any(abs_path.startswith(os.path.abspath(d)) for d in allowed_dirs)
        
        if not is_allowed:
            errors.append(f"{os.path.basename(path)}: Access denied")
            continue
            
        if abs_path == current_log:
            errors.append(f"{os.path.basename(path)}: 현재 사용 중인 로그 파일은 삭제 대상에서 제외되었습니다.")
            continue
            
        if os.path.exists(path):
            try:
                os.remove(path)
                deleted_count += 1
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {str(e)}")
                
    return {
        "status": "success" if not errors else "partial_success",
        "deleted_count": deleted_count,
        "errors": errors
    }

@router.post("/delete-all")
async def delete_all(request: Request):
    data = await request.json()
    target = data.get("target") # "logs" or "results"
    
    if target == "logs":
        dir_path = settings.logpath
    elif target == "results":
        dir_path = settings.resultpath
    else:
        raise HTTPException(status_code=400, detail="Invalid target")
        
    if not os.path.exists(dir_path):
        return {"status": "success", "deleted_count": 0}
        
    current_log = os.path.abspath(os.path.join(settings.logpath, settings.logfile))
    deleted_count = 0
    
    for filename in os.listdir(dir_path):
        path = os.path.join(dir_path, filename)
        abs_path = os.path.abspath(path)
        
        if os.path.isfile(path):
            if target == "logs" and abs_path == current_log:
                continue
                
            try:
                os.remove(path)
                deleted_count += 1
            except:
                pass
                
    return {"status": "success", "deleted_count": deleted_count}
