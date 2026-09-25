import time
import requests
import settings.settings as settings
from core.utils import logger
from core.database import database
from core.database.engine import get_engine
from services import rating_eligibility
from services.satisfaction_fetcher import _PAGE_URL, _extract_cause_from_page_html

def _site_result(session, report_id):
    """사이트의 이 신고 만족도 조사 상태. 대상이 없으면 None, HTTP 오류는 예외."""
    check_url = f"https://www.safetyreport.go.kr/api/v1/portal/statistics/satisfactionstatistics/score/{report_id}/{settings.phone_number}"
    session.headers.update({"Referer": f"https://www.safetyreport.go.kr/html/common/popup/satisfaction.html?seq={report_id}&pn={settings.phone_number}"})
    check_res = session.get(check_url, timeout=10)
    if check_res.status_code != 200:
        raise requests.exceptions.RequestException(f"체크 실패 (HTTP {check_res.status_code})")
    result = check_res.json().get("result") or None
    # 점수는 있는데 사유가 비어 오면 만족도 팝업 HTML 에서 읽는다(satisfaction_fetcher, 모바일 fetchSatisfaction 과 같음)
    if result and _site_score(result) and not str(result.get("STSFDG_CAUSE") or "").strip():
        try:
            page_res = session.get(_PAGE_URL.format(spp=report_id, phone=settings.phone_number), timeout=10)
            if page_res.status_code == 200:
                result = {**result, "STSFDG_CAUSE": _extract_cause_from_page_html(page_res.text)}
        except Exception:
            pass
    return result


def _site_score(result) -> int:
    raw = (result or {}).get("STSFDG_SCORE", 0)
    return int(raw) if str(raw).strip().isdigit() else 0


def run_batch_rating(ids, score=5, cause=""):
    """
    ids: 신고번호(SPP-...) 목록
    score: 1 to 5
    cause: 공통 사유(선택). 사이트 STSFDG_CAUSE 로 보낸다.
    로그는 logger.LoggerFactory.star_log → current_rating.log 로 기록됨.
    모바일 Client 가 로그 줄(`[SPP-…] N점 별점 부여 성공`, `스킵: [SPP-…] 사유`, `실패: …`, `최종 실패: …`, 마지막 요약)을 읽는다 — 형식을 바꾸지 말 것.
    성공은 제출 뒤 사이트에서 점수를 다시 읽어 확인됐을 때만 기록하고, 사이트가 돌려준 점수·사유를 저장한다(모바일 Standalone 과 같음).
    """
    engine = get_engine()
    log = logger.LoggerFactory.star_log
    cause = rating_eligibility.normalize_cause(cause)

    log.info(f"=== 별점 처리 작업 시작 (요청 횟수: {len(ids)}건, 목표 점수: {score}점{', 사유 있음' if cause else ''}) ===")

    # 사전 확인: 목록과 같은 대상 규칙(services/rating_eligibility, 모바일 ineligibleReason 과 동일).
    # ids 는 신고번호다(예전엔 ID 열로 찾아 사전 확인이 사실상 동작하지 않았다).
    skipped = {}
    for rec in database.get_merged_records_by_report_numbers(engine, ids):
        reason = rating_eligibility.ineligible_reason(rec.get('만족도조사여부'), rec.get('처리상태'))
        if reason:
            skipped[rec['신고번호']] = reason
    ids_to_process = [rid for rid in ids if rid not in skipped]

    skip_count = len(skipped)
    if skip_count > 0:
        log.info(f"DB 사전 확인: 별점을 줄 수 없는 {skip_count}건을 로컬에서 스킵합니다. (실제 요청: {len(ids_to_process)}건)")
        for rid, reason in skipped.items():
            # 모바일 Client 가 이 줄을 읽는다(`스킵: [SPP-…] 사유`) — 문구 형식을 바꾸지 말 것
            log.warning(f"  - 스킵: [{rid}] {reason}")

    success_count = 0
    fail_count = 0
    total = len(ids_to_process)

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": "https://www.safetyreport.go.kr",
        "X-Requested-With": "XMLHttpRequest"
    })

    # TCP RST 우회: 첫 연결 전 워밍업 요청 (RST 대비 2회 시도)
    for _ in range(2):
        try:
            session.get("https://www.safetyreport.go.kr/", timeout=5)
            break
        except Exception:
            pass

    def save_site_values(report_id, result):
        try:
            database.sync_rating_status(
                engine, report_id,
                score=_site_score(result) or None,
                cause=result.get("STSFDG_CAUSE"),
            )
        except Exception as db_e:
            log.error(f"[{report_id}] DB 갱신 실패: {db_e}")

    def confirm_success(report_id, result):
        site_score = _site_score(result)
        log.info(f"  - [{report_id}] {site_score}점 별점 부여 성공 (API)")
        if site_score != score:
            log.warning(f"  - 경고: [{report_id}] 사이트 점수({site_score}점)가 보낸 점수({score}점)와 다릅니다.")
        site_cause = rating_eligibility.normalize_cause(result.get("STSFDG_CAUSE"))
        if cause and site_cause != cause:
            log.warning(f"  - 경고: [{report_id}] 사이트에 저장된 사유가 보낸 사유와 다릅니다(사이트가 사유를 받지 않았거나 바꿈).")
        save_site_values(report_id, result)

    for idx, report_id in enumerate(ids_to_process, 1):
        log.info(f"[{idx}/{total}] {report_id} 처리 중...")

        success = False
        posted = False  # 이번 실행에서 이미 제출했는가(확인 전에 끊겨 재시도한 경우 성공으로 센다)
        for attempt in range(settings.max_retry_attemps + 1):
            if attempt > 0:
                log.warning(f"  - [재시도 {attempt}/{settings.max_retry_attemps}] {report_id} 다시 시도 중... ({settings.retry_interval}초 대기)")
                time.sleep(settings.retry_interval)

            try:
                # 1. 상태 확인 (이미 별점을 주었는지)
                result = _site_result(session, report_id)
                if result is None:
                    log.error(f"  - 실패: [{report_id}] 대상 신고건이 없거나 폰 번호가 맞지 않습니다.")
                    fail_count += 1
                    success = True  # 중단용 (재시도 의미 없음)
                    break
                if _site_score(result) != 0:
                    if posted:
                        confirm_success(report_id, result)
                        success_count += 1
                    else:
                        log.warning(f"  - 스킵: [{report_id}] 이미 만족도 조사에 참여하셨습니다.")
                        save_site_values(report_id, result)
                        skip_count += 1
                    success = True
                    break

                # 2. 별점 제출
                post_url = "https://www.safetyreport.go.kr/api/v1/portal/statistics/satisfactionstatistics"
                payload = {
                    "STTEMNT_NO": report_id,
                    "C_PHONE2": settings.phone_number,
                    "STSFDG_SCORE": str(score),
                    "STSFDG_CAUSE": cause,
                }
                post_res = session.post(post_url, data=payload, timeout=10)
                if post_res.status_code != 200:
                    raise requests.exceptions.RequestException(f"제출 실패 (HTTP {post_res.status_code})")
                posted = True

                # 3. 제출 확인 — HTTP 200 만으로는 성공으로 보지 않는다
                verify = _site_result(session, report_id)
                if verify is not None and _site_score(verify) != 0:
                    confirm_success(report_id, verify)
                    success_count += 1
                    success = True
                    break
                raise requests.exceptions.RequestException("제출 후 사이트에서 점수를 확인하지 못했습니다")

            except Exception as e:
                msg = f"[{report_id}] 오류 발생: {e}"
                if attempt < settings.max_retry_attemps:
                    log.error(f"  - 오류: {msg} (재시도 예정)")
                else:
                    log.error(f"  - 최종 실패: {msg}")
                    fail_count += 1

        time.sleep(1)  # 부하 방지

    final_msg = f"별점 처리가 종료되었습니다. (성공: {success_count}, 스킵: {skip_count}, 실패: {fail_count})"
    log.info(f"\n=== {final_msg} ===")
    return success_count, fail_count
