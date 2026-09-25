from fastapi import APIRouter

from core.database.engine import get_engine
from services import maintenance_service

router = APIRouter(prefix="/maintenance")
engine = get_engine()


@router.get("/status")
def maintenance_status():
    """모든 화면 하단의 진행 표시줄이 읽는다(base.html). 작업이 없으면 active=False."""
    return maintenance_service.status(engine)
