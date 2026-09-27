"""크롤링 로그와 영속 공유 업로드 대기열의 시작/종료 경계."""
from __future__ import annotations


class PendingUploadError(RuntimeError):
    """이전 공유 자료가 아직 전송되지 않아 새 크롤링을 시작할 수 없음."""


def log_background_progress(message: str) -> None:
    """수집 중 실시간 업로드도 현재 크롤링 로그에 남긴다."""
    import os
    from services.crawl_log_service import get_current_crawl_log_path
    from services.crawl_manager import crawl_manager

    # pre-crawl flush 는 CrawlManager._state_lock 안에서 업로더를 기다린다.
    # 이 콜백은 관찰용이라 잠금을 다시 잡으면 서로 기다리게 된다.
    if crawl_manager._active_process is None and not crawl_manager._post_upload_active:
        return
    path = get_current_crawl_log_path()
    if os.path.exists(path):
        with open(path, "a", encoding="utf-8") as out:
            out.write(f"[Supabase] {message}\n")


def flush(log_file: str, *, before_crawl: bool) -> None:
    import time
    from services import community_uploader

    def log(message: str) -> None:
        with open(log_file, "a", encoding="utf-8") as out:
            out.write(f"[Supabase] {message}\n")

    log("이전 공유 자료 업로드 확인 중..." if before_crawl else "수집한 공유 자료 업로드 중...")
    lease_deadline = time.monotonic() + 125
    waiting_for_lease = False
    try:
        while True:
            result = community_uploader.request_upload("recovery", progress=log)
            status = community_uploader.upload_status()
            remaining = status["pending"] + status["auth_required"]
            if remaining == 0:
                log("대기 중인 공유 자료가 없습니다.")
                return
            if result["result"] == "busy_other_run" and time.monotonic() < lease_deadline:
                if not waiting_for_lease:
                    log("다른 업로드의 저장소 잠금이 풀리기를 기다립니다.")
                    waiting_for_lease = True
                time.sleep(2)
                continue
            if result["result"] != "more_pending":
                break
        reason = result.get("error_code") or result["result"]
        log(f"{remaining}건 업로드 대기 중 ({reason}).")
        if before_crawl:
            raise PendingUploadError(f"이전 공유 자료 {remaining}건이 업로드되지 않았습니다 ({reason}). 업로드 후 다시 시작하세요.")
    except PendingUploadError:
        raise
    except Exception as exc:
        log(f"업로드 확인 실패 ({type(exc).__name__}).")
        if before_crawl:
            raise PendingUploadError("이전 공유 자료 업로드 상태를 확인하지 못했습니다. 다시 시작하세요.") from exc
