"""기존 data_service 호환 레이어.

새 구조에서는:
- 조회/검색 계열은 report_query_service
- 대시보드/통계 계열은 report_stats_service
- 크롤링 상태 파일 계열은 crawl_state_store

이 모듈은 기존 import 경로를 깨지 않기 위한 얇은 facade다.
"""

from services.crawl_state_store import (
    clear_crawl_changes,
    get_and_clear_crawl_changes,
    get_and_clear_crawl_done,
    get_and_clear_crawl_done_ext,
    peek_crawl_changes,
    save_crawl_changes,
    save_crawl_done,
    save_crawl_done_ext,
)
from services.report_query_service import (
    get_all_records,
    get_all_watchlist,
    get_duplicate_records,
    get_other_records,
    get_parking_records,
    get_traffic_records,
    get_unrated_records,
    resolve_to_report_numbers,
    search_by_address,
    search_by_vehicle,
    update_watchlist_status,
)
from services.report_stats_service import get_agency_stats, get_dashboard_stats, get_report_map_stats


def resolve_ids_for_rating(engine, id_list):
    return resolve_to_report_numbers(engine, id_list)
