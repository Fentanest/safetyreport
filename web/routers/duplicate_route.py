from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from core.database.engine import get_engine
from core.utils.templating import templates
from services import duplicate_group_service


router = APIRouter(prefix="/duplicates")
engine = get_engine()


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


@router.get("/manage")
async def view_duplicate_groups(
    request: Request,
    duplicate_status: str | None = None,
    message: str | None = None,
):
    all_groups = duplicate_group_service.get_duplicate_groups(engine)
    groups = duplicate_group_service.get_duplicate_groups(engine, status=duplicate_status)

    counts = {
        "total": len(all_groups),
        "confirmed_duplicate": sum(1 for item in all_groups if item.get("status") == "confirmed_duplicate"),
        "review_required": sum(1 for item in all_groups if item.get("status") == "review_required"),
        "not_duplicate": sum(1 for item in all_groups if item.get("status") == "not_duplicate"),
    }

    return templates.TemplateResponse(
        request,
        "duplicate_groups.html",
        {
            "title": "중복 신고 관리",
            "groups": groups,
            "counts": counts,
            "current_status": duplicate_status or "",
            "message": message or "",
        },
    )


@router.post("/manage/refresh")
async def refresh_duplicate_groups():
    result = duplicate_group_service.refresh_duplicate_groups(engine)
    return _redirect(
        f"/duplicates/manage?message=중복군%20{result['group_count']}개,%20멤버%20{result['member_count']}건을%20재생성했습니다."
    )


@router.post("/manage/{group_id}/update")
async def update_duplicate_group(
    group_id: str,
    representative_id: str = Form(""),
    duplicate_status: str = Form("confirmed_duplicate"),
    representative_mode: str = Form("auto"),
    note: str = Form(""),
):
    updated = duplicate_group_service.update_duplicate_group(
        engine,
        group_id,
        representative_id=representative_id,
        duplicate_status=duplicate_status,
        representative_mode=representative_mode,
        note=note,
    )
    if updated:
        return _redirect("/duplicates/manage?message=중복군%20설정을%20저장했습니다.")
    return _redirect("/duplicates/manage?message=중복군%20업데이트에%20실패했습니다.")


@router.post("/manage/bulk-status")
async def bulk_update_duplicate_status(
    group_ids: list[str] = Form([]),
    duplicate_status: str = Form("review_required"),
    representative_mode: str = Form("auto"),
):
    updated = duplicate_group_service.bulk_update_duplicate_status(
        engine,
        group_ids,
        duplicate_status,
        representative_mode=representative_mode,
    )
    return _redirect(f"/duplicates/manage?message={updated}건의%20중복군%20설정을%20변경했습니다.")
