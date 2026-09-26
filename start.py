import os
import sys
import subprocess
import time
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
import settings.settings as settings
from core.crawler import driv, login, crawltitle_api, crawldetail_api
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
        "queue_file": None,
        "page_range": None,
        "rebuild": None,
    }

    if '--rebuild' in sys.argv:
        try:
            r_index = sys.argv.index('--rebuild')
            args["rebuild"] = sys.argv[r_index + 1]
        except (ValueError, IndexError):
            pass

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
        # 주소 좌표 캐시(mysafety_geocode_cache)는 주소 단위 재사용 자산이므로 보존한다.
        # 중간 실험 빌드에서 잠시 존재했던 보완 history 테이블도 reset 시 함께 정리한다.
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
        logger.LoggerFactory.logbot.info(
            "--reset에서도 mysafety_geocode_cache는 유지합니다. 재크롤링 시 같은 주소는 캐시 좌표를 재사용합니다."
        )
        # 한 트랜잭션으로 지운다(중간에 멈춰 반쯤 지워진 DB 가 남지 않게 — S-27).
        # 사용자 데이터(수정값·중복 판단·감시목록)와 지오코딩 캐시는 신고 ID 로 다시 이어지므로 보존한다(결정 D-6).
        # last_sync 는 reset 의미상 같이 지운다 — 다음 크롤링이 다시 채워준다.
        with engine.begin() as conn:
            database.metadata.drop_all(conn, tables=data_tables)
            conn.execute(
                database.sync_meta_table.delete().where(
                    database.sync_meta_table.c.key != "watchlist"
                )
            )
            conn.exec_driver_sql("DROP TABLE IF EXISTS mysafety_supplement_history")
    # 크롤링 서브프로세스: 표·열·버전만 확인하고 무거운 정리는 서버 시작 때 한다(S-22). reset 직후엔 비어 있어 정리할 것도 없다.
    database.upgrade_schema(engine, maintenance=False, backup_dir=os.path.join(settings.datapath, "backups"))

def _resolve_report_number_detail(conn, item):
    """(ID 또는 None, 모호 여부). 정확 일치 → 'SPP-' 를 붙인 정확 일치 → 부분 일치가 딱 1건일 때만(S-24).
    부분 일치가 2건 이상이면 (None, True) — 어느 신고인지 정할 수 없다(감사 R6-02)."""
    title = database.title_table
    for candidate in dict.fromkeys([item, item if item.startswith("SPP-") else f"SPP-{item}"]):
        found = conn.execute(select(title.c.ID).where(title.c.신고번호 == candidate)).scalar()
        if found:
            return found, False
    matches = conn.execute(select(title.c.ID).where(title.c.신고번호.like(f"%{item}%")).limit(2)).scalars().all()
    return (matches[0], False) if len(matches) == 1 else (None, len(matches) > 1)


def _resolve_report_number(conn, item):
    """큐 신고번호 → 내부 ID(모호하거나 없으면 None). 예전엔 LIKE '%item%' 의 아무 첫 결과를 썼다."""
    return _resolve_report_number_detail(conn, item)[0]


def extract_ids_from_queue(engine, queuelist, id_to_items=None):
    """Returns (resolved_ids, missing_report_numbers) tuple. id_to_items 를 주면 ID → 큐에 적힌 번호들을 채운다(결과 보고용)."""
    resolved_ids = []
    missing_rnums = []
    with engine.connect() as conn:
        for item in queuelist:
            item = item.strip()
            if not item: continue
            if item.startswith('SPP-') or '-' in item:
                res = _resolve_report_number(conn, item)
                if res:
                    resolved_ids.append(res)
                    if id_to_items is not None:
                        id_to_items.setdefault(str(res), []).append(item)
                else:
                    logger.LoggerFactory.logbot.warning(f"큐 신고번호 {item}의 ID를 찾을 수 없습니다. 목록 크롤링 후 재검색합니다.")
                    missing_rnums.append(item)
            else:
                resolved_ids.append(item)
                if id_to_items is not None:
                    id_to_items.setdefault(str(item), []).append(item)
    return resolved_ids, missing_rnums


def _write_queue_report(queue_file: str, processed, not_found, ambiguous=()) -> None:
    """큐 번호별 결과 보고(감사 R5-01, services/crawl_queue_report). 못 쓰면 기록만 — 부모가 전부 다시 시도한다."""
    from services import crawl_queue_report

    try:
        crawl_queue_report.write(queue_file, processed, not_found, ambiguous)
    except OSError as exc:
        logger.LoggerFactory.logbot.error(f"큐 결과 보고를 쓰지 못함(다음에 다시 시도됨): {exc}")


def _capture_unavailable_class():
    """T4 services.community_capture.CaptureStoreUnavailable. 없으면 None."""
    try:
        from services.community_capture import CaptureStoreUnavailable
        return CaptureStoreUnavailable
    except ImportError:
        return None


def _rebuild_list_labels(titlelist):
    """이번 목록의 ID → 상태(C_NOW 라벨). 영구 실패 pointer(last_list_label)용."""
    labels = {}
    for df in titlelist or []:
        try:
            if "ID" in df.columns and "상태" in df.columns:
                for row in df[["ID", "상태"]].itertuples(index=False):
                    labels[str(row[0])] = row[1]
        except Exception:
            continue
    return labels


def _rebuild_register_list(engine, run_id, titlelist, *, list_ok, note="list_incomplete"):
    """목록 전 페이지 성공 때만 ID 전부 등록 + list_complete=1. 실패면 job failed."""
    from services import community_rebuild as rebuild

    if not list_ok:
        logger.LoggerFactory.logbot.error(f"[rebuild] 목록 수집 실패 — job failed ({note})")
        rebuild.mark_list_failed(run_id, note)
        return False
    report_ids = []
    for df in titlelist or []:
        try:
            report_ids.extend(str(value) for value in df["ID"].tolist())
        except Exception:
            continue
    # 개인 DB 목록 반영(실패 경로는 손대지 않는다). 빈 목록이면 아무 것도 안 한다.
    new_report_numbers = database.title_to_sql(dataframes=titlelist, engine=engine) if report_ids else []
    rebuild.register_list(run_id, report_ids)
    logger.LoggerFactory.logbot.info(
        f"[rebuild] 목록 등록 완료: {len(report_ids)}건 (신규 {len(new_report_numbers)}건)")
    return True


def _rebuild_consume_details(engine, run_id, target_ids, detail_stream, sink, list_labels):
    """상세를 받는 즉시 저장 + rebuild_items 갱신. 반환: changed 목록."""
    from core.storage import reports_repo
    from services import community_rebuild as rebuild

    CaptureUnavailable = _capture_unavailable_class()
    changed = []
    saved = 0
    parser_errors = 0
    auth_seen = False

    def _note_parser_error(item):
        nonlocal parser_errors
        parser_errors += 1
        guess = None
        try:
            first = list(item)[0]
            rows = first.to_dict("records")
            if rows:
                guess = str(rows[0].get("ID"))
        except Exception:
            guess = None
        if guess:
            rebuild.record_item(run_id, guess, "retryable", note="parse_error")

    try:
        for item in detail_stream:
            try:
                record = reports_repo.CrawledDetail.from_legacy_tuple(item)
            except ValueError:
                _note_parser_error(item)
                continue
            try:
                result = reports_repo.save_crawled(engine, [record], refresh_duplicates=False)
            except Exception as exc:
                if CaptureUnavailable is not None and isinstance(exc, CaptureUnavailable):
                    raise
                logger.LoggerFactory.logbot.error(f"[rebuild] ID {record.id} 저장 예외: {exc}")
                rebuild.record_item(run_id, record.id, "retryable", note=str(exc)[:200])
                continue
            saved += result.saved
            failed_ids = {rid for rid, _ in result.failed}
            if record.id in failed_ids:
                note = next((msg for rid, msg in result.failed if rid == record.id), "save_failed")
                rebuild.record_item(run_id, record.id, "retryable", note=str(note)[:200])
            else:
                rebuild.record_item(run_id, record.id, "fetched")
            changed.extend(result.changed)
            # 방금 도달한 상세에서 auth 실패가 보이면 run 전체 paused(auth).
            for rid, (outcome, _note) in sink.items():
                if outcome == "auth":
                    auth_seen = True
                    break
            if auth_seen:
                break
    except Exception as exc:
        if CaptureUnavailable is not None and isinstance(exc, CaptureUnavailable):
            logger.LoggerFactory.logbot.error(f"[rebuild] capture 저장소 불가 — 수집 중단: {exc}")
            rebuild.mark_store_unavailable(run_id)
            return []
        logger.LoggerFactory.logbot.error(f"[rebuild] 상세 크롤링이 중간에 멈췄습니다({saved}건까지 저장됨): {exc}")

    # 스트림에 안 나온 ID 는 sink 분류대로. auth 로 중단됐으면 손대지 않은 건 pending 유지.
    for target in target_ids:
        if target not in sink:
            if auth_seen:
                continue
            rebuild.record_item(run_id, target, "retryable", note="not_fetched")
            continue
        outcome, note = sink[target]
        if outcome == "ok":
            continue
        if outcome == "auth":
            auth_seen = True
            continue
        rebuild.record_item(run_id, target, outcome, note=str(note)[:200],
                            list_label=list_labels.get(target))
    if parser_errors:
        rebuild.note_counts(run_id, parser_errors=parser_errors)
        logger.LoggerFactory.logbot.warning(f"[rebuild] 파서 오류 {parser_errors}건")
    if auth_seen:
        logger.LoggerFactory.logbot.warning("[rebuild] 로그인 만료 — run paused(auth)")
        rebuild.mark_paused_auth(run_id)
    logger.LoggerFactory.logbot.info(f"[rebuild] 상세 저장 {saved}건 (변경/신규 {len(changed)}건)")
    return changed


def _run_rebuild_process(driver, engine, args, api_browser_fallback=False):
    """초기화 크롤: 전 페이지 성공 때만 목록 등록 → 미완료 items 만 상세 → 상태 갱신."""
    from services import community_rebuild as rebuild

    run_id = args.get("rebuild")
    store_job = rebuild._get_job(run_id)
    if store_job is None:
        logger.LoggerFactory.logbot.error(f"[rebuild] run {run_id} 없음 — 초기화 크롤을 시작하지 않습니다.")
        return []

    logger.LoggerFactory.logbot.info(f"[rebuild] 목록 수집 시작 (run {run_id})")
    progress: dict = {}
    try:
        titlelist, _last_page = crawltitle_api.crawl_titles(
            driver=driver,
            browser_fallback=api_browser_fallback,
            progress=progress,
        )
    except Exception as exc:
        logger.LoggerFactory.logbot.error(f"[rebuild] 목록 수집 예외: {exc}")
        rebuild.mark_list_failed(run_id, f"list_error: {exc}")
        return []
    list_ok = progress.get("list_ok")
    if list_ok is None:
        # progress 를 모르는 가짜 크롤러 대비: 비어 있으면 실패, 있으면 성공으로 본다.
        list_ok = bool(titlelist)
        note = progress.get("first_error") or "list_incomplete"
    else:
        note = progress.get("first_error") or "list_incomplete"
    list_ok = bool(list_ok)
    if not _rebuild_register_list(engine, run_id, titlelist, list_ok=list_ok, note=str(note)):
        return []

    list_labels = _rebuild_list_labels(titlelist)
    targets = rebuild.pending_detail_ids(run_id)
    if not targets:
        logger.LoggerFactory.logbot.info("[rebuild] 상세 대상 없음 (빈 목록 또는 전부 fetched)")
        return []

    logger.LoggerFactory.logbot.info(f"[rebuild] 상세 크롤링 대상 {len(targets)}건 (checkpoint: 미완료만)")
    sink: dict = {}
    detail_stream = crawldetail_api.crawl_details(
        driver=driver,
        report_ids=targets,
        browser_fallback=api_browser_fallback,
        status_sink=sink,
    )
    return _rebuild_consume_details(engine, run_id, targets, detail_stream, sink, list_labels)


def _run_crawling_process(driver, engine, args, api_browser_fallback=False):
    """API 방식 크롤링(레거시 Selenium HTML 크롤링은 2026-09-25 제거). driver 는 브라우저 비상 경로에서만 있다."""
    if args.get("rebuild"):
        return _run_rebuild_process(driver, engine, args, api_browser_fallback)

    last_page = 0
    titlelist = []
    
    if args.get("queue_file"):
        logger.LoggerFactory.logbot.info("큐 지정 크롤링 모드입니다. 전체 목록 갱신을 건너뜁니다.")
    else:
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
    id_to_items = {}  # 큐 모드: ID → 큐 번호(번호별 결과 보고, R5-01)
    missing_rnums = []
    queue_not_found, queue_ambiguous = [], []  # 목록 전 페이지를 성공적으로 훑은 뒤에만 채운다(R6-01·R6-02)
    if args.get("queue_file"):
        with open(args["queue_file"], 'r', encoding='utf-8') as f:
            q_items = f.readlines()
        detaillist, missing_rnums = extract_ids_from_queue(engine, q_items, id_to_items)

        # DB에 없는 신고번호가 있으면 목록 크롤링으로 탐색(API 방식은 브라우저 없이도 가능).
        # '없음'·'모호함'은 목록 전 페이지를 **성공적으로** 훑은 뒤에만 확정한다(감사 R6-01) — 호출 실패·잘린 페이지·
        # 탐색 상한 도달이면 확정하지 않고 큐에 남겨 다음에 다시 찾는다.
        if missing_rnums:
            logger.LoggerFactory.logbot.info(
                f"미확인 신고번호 {len(missing_rnums)}건을 목록 크롤링으로 탐색합니다."
            )
            MAX_SEARCH_PAGES = 100
            search_complete = False
            ambiguous_now = []
            for page_num in range(1, MAX_SEARCH_PAGES + 1):
                if not missing_rnums:
                    break
                logger.LoggerFactory.logbot.info(f"목록 탐색 중... 페이지 {page_num} (남은 미확인: {len(missing_rnums)}건)")
                progress = {}
                try:
                    page_dfs, _ = crawltitle_api.crawl_titles(
                        driver=driver,
                        page_range=[page_num],
                        browser_fallback=api_browser_fallback,
                        progress=progress,
                    )
                except Exception as e:
                    logger.LoggerFactory.logbot.warning(f"페이지 {page_num} 탐색 실패: {e}")
                    break
                if progress.get("first_error") or progress.get("pages_failed") or progress.get("total") is None:
                    logger.LoggerFactory.logbot.warning(f"페이지 {page_num} 탐색 실패: {progress.get('first_error')}")
                    break
                if page_dfs:
                    database.title_to_sql(dataframes=page_dfs, engine=engine)
                # 이번 페이지까지 반영한 DB 에서 같은 규칙으로 다시 해석한다(정확 → 접두어 → 유일한 부분 일치, R6-02)
                still_missing, ambiguous_now = [], []
                with engine.connect() as conn:
                    for rnum in missing_rnums:
                        res, ambiguous = _resolve_report_number_detail(conn, rnum)
                        if res:
                            detaillist.append(res)
                            id_to_items.setdefault(str(res), []).append(rnum)
                            logger.LoggerFactory.logbot.info(f"신고번호 {rnum} → ID {res} 발견 (페이지 {page_num})")
                        else:
                            still_missing.append(rnum)
                            if ambiguous:
                                ambiguous_now.append(rnum)
                missing_rnums = still_missing
                pages_expected = progress.get("pages_expected") or 0
                if page_num >= pages_expected:  # 목록 전 페이지(총 건수 기준)를 성공적으로 훑었다
                    search_complete = True
                    break
            if missing_rnums and search_complete:
                queue_ambiguous = [r for r in missing_rnums if r in ambiguous_now]
                queue_not_found = [r for r in missing_rnums if r not in ambiguous_now]
                if queue_ambiguous:
                    logger.LoggerFactory.logbot.error(
                        f"여러 신고에 걸리는 번호라 어느 신고인지 정할 수 없습니다 — 정확한 신고번호로 다시 요청하세요: {queue_ambiguous}")
                if queue_not_found:
                    logger.LoggerFactory.logbot.warning(f"목록 전체를 찾아도 없는 신고번호: {queue_not_found}")
            elif missing_rnums:
                logger.LoggerFactory.logbot.warning(
                    f"목록 탐색을 끝내지 못해 다음에 다시 찾습니다: {missing_rnums}")
                queue_ambiguous, queue_not_found = [], []
            else:
                queue_ambiguous, queue_not_found = [], []

        logger.LoggerFactory.logbot.info(f"큐 파일에서 {len(detaillist)}개의 아이템 크롤링 시작.")
    elif args["page_range"]:
        detaillist = []
        for df in titlelist:
            detaillist.extend(df['ID'].tolist())
    else:
        detaillist = database.get_pending_detail_ids(engine=engine, force=args["force"])

    if not detaillist:
        logger.LoggerFactory.logbot.info("크롤링할 상세 내역 없음.")
        if args.get("queue_file"):
            _write_queue_report(args["queue_file"], [], queue_not_found, queue_ambiguous)
        return []

    logger.LoggerFactory.logbot.info(f"상세 크롤링 대상 ID: {len(detaillist)} 건 (순차 처리)")
    
    if api_browser_fallback:
        logger.LoggerFactory.logbot.info("[API 방식 - Selenium fallback] 상세 데이터 추출 시작")
    else:
        logger.LoggerFactory.logbot.info("[API 방식] 상세 데이터 추출 시작")
    detail_stream = crawldetail_api.crawl_details(
        driver=driver,
        report_ids=detaillist,
        browser_fallback=api_browser_fallback,
    )

    saved_ids = set()
    changed_item_ids = _save_details_as_they_arrive(engine, detail_stream, saved_ids)
    if args.get("queue_file"):
        processed = [item for rid in saved_ids for item in id_to_items.get(str(rid), [])]
        _write_queue_report(args["queue_file"], processed, queue_not_found, queue_ambiguous)
    if settings.telegram_enabled:
        msg = f"2/5. 상세 정보(Detail) 크롤링 {len(detaillist)}건 및 DB 저장을 완료했습니다. (내용 변경/신규 처리: {len(changed_item_ids)}건)"
        # changed_item_ids는 [{"id": ..., "change_type": "신규"/"변경"}] 형식
        if is_frozen:
            subprocess.run([sys.executable, "--mode", "notify", msg])
        else:
            notifier_path = resource_path("core/utils/notifier.py")
            subprocess.run([sys.executable, notifier_path, msg])
    
    return changed_item_ids

def _save_details_as_they_arrive(engine, detail_stream, saved_ids=None):
    """상세를 받는 즉시 1건씩 저장한다(저장 계층 재설계 R2, S-9). 크롤러가 중간에 멈춰도 받은 만큼은 남는다.
    중복군 재계산은 뒤의 merge_final 에서 한 번만 한다."""
    from core.storage import reports_repo

    changed, failed, saved = [], [], 0
    try:
        for item in detail_stream:
            try:
                record = reports_repo.CrawledDetail.from_legacy_tuple(item)
            except ValueError:
                continue
            result = reports_repo.save_crawled(engine, [record], refresh_duplicates=False)
            if result.saved and saved_ids is not None:
                saved_ids.add(str(record.id))
            changed.extend(result.changed)
            failed.extend(result.failed)
            saved += result.saved
    except Exception as exc:
        logger.LoggerFactory.logbot.error(f"상세 크롤링이 중간에 멈췄습니다({saved}건까지 저장됨): {exc}")
    logger.LoggerFactory.logbot.info(
        f"상세 저장 {saved}건 (변경/신규 {len(changed)}건, 실패 {len(failed)}건)"
    )
    if failed:
        logger.LoggerFactory.logbot.error("저장 실패 ID: " + ", ".join(rid for rid, _ in failed[:50]))
    return changed


def _process_and_save_results(engine, changed_item_ids):
    try:  # 종결돼 다시 안 받는 주정차 신고의 사진 촬영 시각 재시도(S-8). 실패해도 크롤링 결과 저장은 계속.
        from services import photo_capture_time

        filled = photo_capture_time.backfill_missing(engine)
        if filled:
            logger.LoggerFactory.logbot.info(f"[photo] 촬영 시각 재시도로 {filled}건 채움")
    except Exception as exc:
        logger.LoggerFactory.logbot.warning(f"[photo] 촬영 시각 재시도 실패: {exc}")
    logger.LoggerFactory.logbot.info("최종 데이터 병합 및 저장 시작")
    duplicate_refresh = database.merge_final(engine=engine, track_duplicate_changes=True) or {}  # 6개월 첨부 가림도 여기서 적용
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

def main():
    args = _parse_args()
    _validate_settings()
    engine = get_engine()
    _prepare_database(engine, reset=args["reset"])

    rebuild_run_id = args.get("rebuild")
    if rebuild_run_id:
        # T4 capture 가 run id 를 알 수 있게 환경변수로 전달한다.
        os.environ["SAFETYREPORT_REBUILD_RUN_ID"] = str(rebuild_run_id)

    driver = None
    api_browser_fallback = False
    try:
        # API 방식만 쓴다(레거시 Selenium HTML 크롤링·비회원 로그인은 2026-09-25 제거).
        # 먼저 direct_login 을 시도하고, 실패하면 Selenium 로그인 후 브라우저 컨텍스트 API 호출($.get) 비상 경로로 진행.
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

        if api_browser_fallback:
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
            api_browser_fallback=api_browser_fallback,
        )
    except Exception as e:
        logger.LoggerFactory.logbot.error(f"실행 중 치명적 오류 발생: {e}")
        if rebuild_run_id:
            # 로그인 실패·403 등은 성공이 아니다 — job failed 로 기록한다(0건 성공 금지).
            try:
                from services import community_rebuild as _rebuild

                _rebuild.mark_login_failed(rebuild_run_id, f"login_failed: {e}")
            except Exception:
                pass
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
