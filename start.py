import os
import sys
import subprocess
import time
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
import settings.settings as settings
from core.crawler import driv, login, crawltitle, crawldetail
try:
    from core.crawler import crawltitle_api, crawldetail_api
except ImportError:
    pass
from core.utils import logger
logger.LoggerFactory.create_logger(mode='crawl')
from core.database import database
from core.utils import message_formatter
from core.database.engine import get_engine
from core.utils.path_utils import resource_path, is_frozen, enforce_utf8
from services import crawl_state_store, export_service
enforce_utf8()

def _parse_args():
    args = {
        "force": '--force' in sys.argv,
        "reset": '--reset' in sys.argv,
        "min": '--min' in sys.argv,
        "nonmember": '--nonmember' in sys.argv,
        "queue_file": None,
        "page_range": None
    }
    
    if '--queue' in sys.argv:
        try:
            q_index = sys.argv.index('--queue')
            args["queue_file"] = sys.argv[q_index + 1]
        except (ValueError, IndexError):
            pass

    if '--p' in sys.argv:
        try:
            p_index = sys.argv.index('--p')
            range_str = sys.argv[p_index + 1]
            if ',' in range_str:
                args["page_range"] = list(map(int, range_str.split(',')))
            elif '-' in range_str:
                start, end = map(int, range_str.split('-'))
                args["page_range"] = list(range(start, end + 1))
            else:
                args["page_range"] = [int(range_str)]
        except (ValueError, IndexError):
            pass
    return args

def _validate_settings():
    if not os.path.exists(settings.resultpath):
        os.makedirs(settings.resultpath, exist_ok=True)

def _prepare_database(engine, reset=False):
    if reset:
        logger.LoggerFactory.logbot.warning("--reset 옵션이 사용되어 크롤링 데이터 테이블을 초기화합니다.")
        # 관리자 계정(admin_users), API 키(api_keys), 감시 목록(watchlist)은 보존.
        # 신고 ID 에 매여 있는 사이드카(entry_value, raw_content, duplicate_*)도 같이 비운다.
        data_tables = [
            # 중복 멤버는 group 보다 먼저 — 의미상 group 의 부속이므로
            database.duplicate_member_table,
            database.duplicate_group_table,
            database.entry_value_table,
            database.raw_content_table,
            database.title_table,
            database.detail_traffic_table,
            database.detail_parking_table,
            database.detail_other_table,
            database.merge_traffic_table,
            database.merge_parking_table,
            database.merge_other_table,
        ]
        database.metadata.drop_all(engine, tables=data_tables)
        # sync_meta 는 통째로 drop 하지 않고 watchlist 키만 보존한 채 비운다.
        # last_sync 는 reset 의미상 같이 지운다 — 다음 크롤링이 다시 채워준다.
        with engine.begin() as conn:
            conn.execute(
                database.sync_meta_table.delete().where(
                    database.sync_meta_table.c.key != "watchlist"
                )
            )
    database.upgrade_schema(engine)

def extract_ids_from_queue(engine, queuelist):
    """Returns (resolved_ids, missing_report_numbers) tuple."""
    resolved_ids = []
    missing_rnums = []
    with engine.connect() as conn:
        for item in queuelist:
            item = item.strip()
            if not item: continue
            if item.startswith('SPP-') or '-' in item:
                query = select(database.title_table.c.ID).where(database.title_table.c.신고번호.like(f"%{item}%"))
                res = conn.execute(query).scalar()
                if res:
                    resolved_ids.append(res)
                else:
                    logger.LoggerFactory.logbot.warning(f"큐 신고번호 {item}의 ID를 찾을 수 없습니다. 목록 크롤링 후 재검색합니다.")
                    missing_rnums.append(item)
            else:
                resolved_ids.append(item)
    return resolved_ids, missing_rnums

def _run_crawling_process(driver, engine, args, crawl_type=None, api_browser_fallback=False):
    crawl_type = 'api' if (crawl_type or settings.crawl_type) == 'api' else 'legacy'
    last_page = 0
    titlelist = []
    
    if args.get("queue_file"):
        logger.LoggerFactory.logbot.info("큐 지정 크롤링 모드입니다. 전체 목록 갱신을 건너뜁니다.")
    else:
        if crawl_type == 'api':
            if api_browser_fallback:
                logger.LoggerFactory.logbot.info("[API 방식 - Selenium fallback]으로 신고 목록 크롤링 시작.")
            else:
                logger.LoggerFactory.logbot.info("[API 방식]으로 신고 목록 크롤링 시작.")
            if args["page_range"]:
                titlelist, last_page = crawltitle_api.crawl_titles(
                    driver=driver,
                    page_range=args["page_range"],
                    browser_fallback=api_browser_fallback,
                )
            else:
                titlelist, last_page = crawltitle_api.crawl_titles(
                    driver=driver,
                    browser_fallback=api_browser_fallback,
                )
        else:
            logger.LoggerFactory.logbot.info("[웹 방식(레거시)]으로 신고 목록 크롤링 시작.")
            if args["page_range"]:
                titlelist, last_page = crawltitle.crawl_titles(driver=driver, use_minimal_crawl=args["min"], page_range=args["page_range"])
            else:
                titlelist, last_page = crawltitle.crawl_titles(driver=driver, use_minimal_crawl=args["min"])

        new_report_numbers = database.title_to_sql(dataframes=titlelist, engine=engine)
        if settings.telegram_enabled:
            msg = f"1/5. 신고 목록(Title) 수집 및 DB 저장을 완료했습니다. (총 {last_page} 페이지)\n"
            if new_report_numbers:
                msg += "\n[신규 추가된 신고번호]\n" + "\n".join(new_report_numbers[:30])
                if len(new_report_numbers) > 30:
                    msg += f"\n... 외 {len(new_report_numbers)-30}건"
            
            if is_frozen:
                subprocess.run([sys.executable, "--mode", "notify"], input=msg, text=True)
            else:
                notifier_path = resource_path("core/utils/notifier.py")
                subprocess.run([sys.executable, notifier_path], input=msg, text=True)

    # Prepare detail list
    if args.get("queue_file"):
        with open(args["queue_file"], 'r', encoding='utf-8') as f:
            q_items = f.readlines()
        detaillist, missing_rnums = extract_ids_from_queue(engine, q_items)

        # DB에 없는 신고번호가 있으면 목록 크롤링으로 탐색
        can_search_queue = crawl_type == 'api' or driver is not None
        if missing_rnums and can_search_queue:
            logger.LoggerFactory.logbot.info(
                f"미확인 신고번호 {len(missing_rnums)}건을 목록 크롤링으로 탐색합니다."
            )
            MAX_SEARCH_PAGES = 100
            for page_num in range(1, MAX_SEARCH_PAGES + 1):
                if not missing_rnums:
                    break
                logger.LoggerFactory.logbot.info(f"목록 탐색 중... 페이지 {page_num} (남은 미확인: {len(missing_rnums)}건)")
                try:
                    if crawl_type == 'api':
                        page_dfs, _ = crawltitle_api.crawl_titles(
                            driver=driver,
                            page_range=[page_num],
                            browser_fallback=api_browser_fallback,
                        )
                    else:
                        page_dfs, _ = crawltitle.crawl_titles(driver=driver, page_range=[page_num])
                except Exception as e:
                    logger.LoggerFactory.logbot.warning(f"페이지 {page_num} 탐색 실패: {e}")
                    break
                if not page_dfs:
                    logger.LoggerFactory.logbot.info(f"페이지 {page_num} 이후 데이터 없음. 탐색 종료.")
                    break
                database.title_to_sql(dataframes=page_dfs, engine=engine)
                # 이번 페이지에서 미확인 신고번호 재검색
                still_missing = []
                with engine.connect() as conn:
                    for rnum in missing_rnums:
                        query = select(database.title_table.c.ID).where(database.title_table.c.신고번호.like(f"%{rnum}%"))
                        res = conn.execute(query).scalar()
                        if res:
                            detaillist.append(res)
                            logger.LoggerFactory.logbot.info(f"신고번호 {rnum} → ID {res} 발견 (페이지 {page_num})")
                        else:
                            still_missing.append(rnum)
                missing_rnums = still_missing
            if missing_rnums:
                logger.LoggerFactory.logbot.warning(f"탐색 완료 후에도 찾지 못한 신고번호: {missing_rnums}")
        elif missing_rnums:
            logger.LoggerFactory.logbot.warning(
                f"미확인 신고번호 {len(missing_rnums)}건이 있지만 현재 크롤링 방식에서는 "
                "목록 재탐색에 필요한 브라우저 세션이 없어 건너뜁니다."
            )

        logger.LoggerFactory.logbot.info(f"큐 파일에서 {len(detaillist)}개의 아이템 크롤링 시작.")
    elif args["page_range"]:
        detaillist = []
        for df in titlelist:
            detaillist.extend(df['ID'].tolist())
    else:
        detaillist = database.get_pending_detail_ids(engine=engine, force=args["force"])

    if not detaillist:
        logger.LoggerFactory.logbot.info("크롤링할 상세 내역 없음.")
        return []

    logger.LoggerFactory.logbot.info(f"상세 크롤링 대상 ID: {len(detaillist)} 건 (순차 처리)")
    
    if crawl_type == 'api':
        if api_browser_fallback:
            logger.LoggerFactory.logbot.info("[API 방식 - Selenium fallback] 상세 데이터 추출 시작")
        else:
            logger.LoggerFactory.logbot.info("[API 방식] 상세 데이터 추출 시작")
        detail_datas = list(
            crawldetail_api.crawl_details(
                driver=driver,
                report_ids=detaillist,
                browser_fallback=api_browser_fallback,
            )
        )
    else:
        logger.LoggerFactory.logbot.info("[웹 방식(레거시)] 상세 데이터 추출 시작")
        detail_datas = list(crawldetail.crawl_details(driver=driver, report_ids=detaillist))
        
    changed_item_ids = database.detail_to_sql(dataframes_with_category=detail_datas, engine=engine)
    if settings.telegram_enabled:
        msg = f"2/5. 상세 정보(Detail) 크롤링 {len(detaillist)}건 및 DB 저장을 완료했습니다. (내용 변경/신규 처리: {len(changed_item_ids)}건)"
        # changed_item_ids는 [{"id": ..., "change_type": "신규"/"변경"}] 형식
        if is_frozen:
            subprocess.run([sys.executable, "--mode", "notify", msg])
        else:
            notifier_path = resource_path("core/utils/notifier.py")
            subprocess.run([sys.executable, notifier_path, msg])
    
    return changed_item_ids

def _process_and_save_results(engine, changed_item_ids):
    logger.LoggerFactory.logbot.info("최종 데이터 병합 및 저장 시작")
    duplicate_refresh = database.merge_final(engine=engine, track_duplicate_changes=True) or {}
    database.clear_old_attachments(engine=engine)
    duplicate_changes = list(duplicate_refresh.get("changes") or [])
    total_changed_count = len(changed_item_ids) + len(duplicate_changes)

    # 모바일 개별 알림용 변경 목록 파일 저장 + 완료 마커
    if changed_item_ids or duplicate_changes:
        crawl_state_store.save_crawl_changes(engine, changed_item_ids, duplicate_changes=duplicate_changes)
    else:
        crawl_state_store.clear_crawl_changes()
    crawl_state_store.save_crawl_done(
        total_changed_count,
        report_changed_count=len(changed_item_ids),
        duplicate_changed_count=len(duplicate_changes),
    )

    # 마지막 크롤링 시각을 mysafety_sync_meta.last_sync 에 ISO8601 로 영속 저장.
    # 모바일 sync_engine.dart 가 같은 키/형식으로 자기 sync_meta 에 기록하므로
    # 서버 ↔ 모바일 DB import 시 round-trip 으로 보존된다.
    now_iso = datetime.now().isoformat(timespec="seconds")
    with engine.begin() as conn:
        stmt = sqlite_insert(database.sync_meta_table).values(
            key="last_sync", value=now_iso
        )
        conn.execute(stmt.on_conflict_do_update(
            index_elements=[database.sync_meta_table.c.key],
            set_={"value": now_iso},
        ))

    # get_merged_records_by_ids에 전달할 순수 ID 목록
    all_ids = [item["id"] for item in changed_item_ids]

    # 1. 데이터 저장 (Excel, Google Sheet) - 카테고리별 시트로 분리 저장
    if settings.auto_export_excel or settings.auto_export_sheet:
        export_service.export_results(
            engine,
            save_excel=settings.auto_export_excel,
            save_sheet=settings.auto_export_sheet,
        )

    # 2. 텔레그램 최종 요약 알림 - 대량의 경우 지연이 발생할 수 있으므로 마지막에 처리
    if settings.telegram_enabled:
        msg = "5/5. 최종 데이터 분석 및 요약을 완료했습니다."
        if all_ids:
            changed_records = database.get_merged_records_by_ids(engine, all_ids)
            detail_msg = message_formatter.format_report_list(changed_records, "[내용 변경/신규 처리된 신고 목록]")
            if detail_msg:
                msg += "\n\n" + detail_msg
        
        if is_frozen:
            subprocess.run([sys.executable, "--mode", "notify"], input=msg, text=True)
        else:
            notifier_path = resource_path("core/utils/notifier.py")
            subprocess.run([sys.executable, notifier_path], input=msg, text=True)

def wait_for_resume_signal():
    logger.LoggerFactory.logbot.info("비회원 모드 대기 중... 브라우저에서 로그인 후 웹 UI의 '크롤링 재개'를 클릭하세요.")
    sig_file = os.path.join(settings.datapath, 'resume.sig')
    if os.path.exists(sig_file):
        os.remove(sig_file)
    while not os.path.exists(sig_file):
        time.sleep(2)
    os.remove(sig_file)
    logger.LoggerFactory.logbot.info("'크롤링 재개' 신호 수신됨. 작업을 계속합니다.")

def main():
    args = _parse_args()
    _validate_settings()
    engine = get_engine()
    _prepare_database(engine, reset=args["reset"])

    driver = None
    effective_crawl_type = 'api' if settings.crawl_type == 'api' else 'legacy'
    is_nonmember_mode = args["nonmember"]
    api_browser_fallback = False
    try:
        if is_nonmember_mode:
            effective_crawl_type = 'legacy'
            logger.LoggerFactory.logbot.info(
                "비회원(수동) 로그인 모드입니다. 직접 로그인/API 방식은 사용하지 않고 "
                "설정된 Chrome 옵션으로 브라우저 로그인 대기 후 진행합니다."
            )
        elif effective_crawl_type == 'api':
            # API 방식: 먼저 direct_login을 시도하고, 실패 시 Selenium 로그인 후
            # 브라우저 컨텍스트 API 호출($.get) fallback으로 진행
            from core.crawler import direct_login
            try:
                direct_login.get_valid_token()
                logger.LoggerFactory.logbot.info("직접 로그인 토큰 확보 완료.")
            except Exception as e:
                logger.LoggerFactory.logbot.error(f"직접 로그인 실패: {e}")
                logger.LoggerFactory.logbot.warning(
                    f"직접 로그인 최대 재시도({settings.max_retry_attemps}) 실패. "
                    "Selenium 로그인 후 브라우저 기반 API 호출 fallback으로 진행합니다."
                )
                api_browser_fallback = True
        else:
            logger.LoggerFactory.logbot.info(
                "레거시 크롤링 모드입니다. direct_login을 사용하지 않고 Selenium 로그인으로 진행합니다."
            )

        if effective_crawl_type == 'legacy':
            driver = driv.create_driver()
            driver.get(settings.loginurl)

            if is_nonmember_mode:
                if getattr(settings, "chrome_mode", "") == "desktop" and getattr(settings, "headless", False):
                    logger.LoggerFactory.logbot.warning(
                        "비회원(수동) 로그인 모드인데 Headless가 켜져 있습니다. "
                        "브라우저 창이 보이지 않으면 설정에서 '크롬 창 숨기기'를 꺼주세요."
                    )
                wait_for_resume_signal()
            else:
                login_ok = login.login_mysafety(driver=driver)
                if not login_ok:
                    raise RuntimeError("안전신문고 Selenium 로그인에 실패했습니다.")
                if settings.telegram_enabled:
                    if is_frozen:
                        subprocess.run([sys.executable, "--mode", "notify"], input="안전신문고 로그인에 성공했습니다.", text=True)
                    else:
                        notifier_path = resource_path("core/utils/notifier.py")
                        subprocess.run([sys.executable, notifier_path], input="안전신문고 로그인에 성공했습니다.", text=True)
        elif api_browser_fallback:
            driver = driv.create_driver()
            driver.get(settings.loginurl)
            login_ok = login.login_mysafety(driver=driver)
            if not login_ok:
                raise RuntimeError("안전신문고 Selenium 로그인(API fallback)에 실패했습니다.")
        else:
            logger.LoggerFactory.logbot.info("[API 방식] Selenium driver 생성 생략.")

        changed_item_ids = _run_crawling_process(
            driver,
            engine,
            args,
            crawl_type=effective_crawl_type,
            api_browser_fallback=api_browser_fallback,
        )
    except Exception as e:
        logger.LoggerFactory.logbot.error(f"실행 중 치명적 오류 발생: {e}")
        changed_item_ids = []
    finally:
        if driver:
            driver.quit()

    try:
        _process_and_save_results(engine, changed_item_ids)
        logger.LoggerFactory.logbot.info("====== 크롤링 작업 완료 ======")
    except Exception as e:
        logger.LoggerFactory.logbot.error(f"저장 중 오류 발생: {e}")

if __name__ == "__main__":
    main()
