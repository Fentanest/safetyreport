import os

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from core.database import database
from core.database.engine import get_engine
from services import data_service
from services import sunwi_service
from core.utils.templating import templates
from web.routers.filters import default_dedupe_mode, with_fine_estimates

engine = get_engine()
router = APIRouter()


def _attach_entry_value(records: list[dict]) -> None:
    """추정 판정(services/fine_estimate.classify)에 필요한 entry_value·ssnpc_cd·ssnpc_type 을 보충한다.

    data_service.get_dashboard_stats() 의 watchlist/recent_answers 는 모바일 API(web/routers/api_route.py)
    와 공유하는 고정 열 화이트리스트(services/stats/common._REPORT_FIELDS)를 쓰고, 거기엔 메뉴·분류 코드가
    없다(category 만 별도로 채워 넣음) — 세부 추정 규칙(과태료 종류 구분)이 틀어지므로 이 화면 전용으로
    ID 로 보충해 붙인다. 모바일 API 가 쓰는 _REPORT_FIELDS/get_dashboard_stats() 자체는 건드리지 않는다.
    """
    ids = [record.get("ID") for record in records if record.get("ID")]
    if not ids:
        return
    table = database.report_effective_table
    lookup: dict[str, dict] = {}
    with engine.connect() as conn:
        for offset in range(0, len(ids), 400):
            chunk = ids[offset:offset + 400]
            rows = conn.execute(
                select(table.c.ID, table.c.entry_value, table.c.ssnpc_cd, table.c.ssnpc_type)
                .where(table.c.ID.in_(chunk)))
            lookup.update({str(row.ID): dict(row._mapping) for row in rows})
    for record in records:
        source = lookup.get(str(record.get("ID")), {})
        for field in ("entry_value", "ssnpc_cd", "ssnpc_type"):
            record[field] = source.get(field)


@router.get("/")
def dashboard(request: Request):
    try:
        stats = data_service.get_dashboard_stats(engine, mode=default_dedupe_mode())
        stats["load_error"] = False
        # 금액 미확인 과태료 보조 표기(PROJECT_RULES §3-2) — 판정은 services/fine_estimate 를 그대로 쓴다
        # (web/routers/filters.with_fine_estimates). 감시 목록·최근 답변 표에만 붙이고(통계 카드는 이미
        # 추정을 보이므로 그대로 둠), get_dashboard_stats() 의 캐시 결과는 매 호출 copy.deepcopy 로 반환되어
        # 모바일 API(/api/v1, 같은 함수 공유)가 받는 값은 바꾸지 않는다.
        watchlist_items = stats.get("watchlist") or []
        recent_items = stats.get("recent_answers") or []
        _attach_entry_value(watchlist_items)
        _attach_entry_value(recent_items)
        with_fine_estimates(watchlist_items)
        with_fine_estimates(recent_items)
    except Exception as e:
        # 집계 실패를 0건으로 보여 주면 신고가 없는 것처럼 보인다 — 실패를 화면에 밝힌다(기술일지 O-02)
        from core.utils import logger
        if logger.LoggerFactory.logbot:
            logger.LoggerFactory.logbot.error(f"[dashboard] 대시보드 집계 실패: {type(e).__name__}: {e}")
        stats = {"last_crawl_time": "확인 실패", "load_error": True, "recent_answers": [], "watchlist": []}

    # 전국 안전신고 현황(Sunwi)은 2026-09-28 통계 화면으로 옮겼다(/stats 하단). /sunwi/* 경로는 그대로 쓴다.
    return templates.TemplateResponse(request, "index.html", {
        "title": "대시보드",
        **stats
    })


@router.get("/sunwi/payload")
def sunwi_payload():
    return sunwi_service.get_dashboard_payload()


@router.get("/sunwi/download/top5")
def download_sunwi_top5_csv():
    csv_path = sunwi_service.get_top5_csv_path()
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail="CSV file not found")

    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=os.path.basename(csv_path),
    )


@router.get("/sunwi/download/all")
def download_sunwi_all_csv():
    csv_path = sunwi_service.get_all_csv_path()
    if not os.path.exists(csv_path):
        raise HTTPException(status_code=404, detail="CSV file not found")

    return FileResponse(
        csv_path,
        media_type="text/csv",
        filename=os.path.basename(csv_path),
    )
