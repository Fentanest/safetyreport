"""대기 큐 크롤의 번호별 결과 보고(감사 R5-01). 자식(start.py)이 쓰고 부모(crawl_manager)가 읽는다.

processed = 상세 저장까지 끝난 번호, not_found = 목록을 끝까지 찾아도 없는 번호. 여기 없는 번호(로그인·네트워크·저장 실패,
중간 중단)는 부모가 큐에 남겨 다시 시도한다. 자식의 종료 코드는 믿지 않는다 — 실패해도 0 으로 끝날 수 있다.
"""
from __future__ import annotations

import json
import os


def report_path(queue_file: str) -> str:
    return f"{queue_file}.done.json"


def write(queue_file: str, processed, not_found) -> None:
    """원자적으로 쓴다(fsync + replace). 실패하면 OSError — 호출자는 기록만 하고, 부모는 보고가 없으니 전부 재시도한다."""
    path = report_path(queue_file)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"processed": sorted(set(processed)), "not_found": sorted(set(not_found))}, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read(queue_file: str) -> tuple[set, list]:
    """(끝난 번호 집합, 찾을 수 없는 번호 목록). 보고가 없거나 깨졌으면 (빈 집합, [])."""
    try:
        with open(report_path(queue_file), encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, ValueError):
        return set(), []
    if not isinstance(report, dict):
        return set(), []
    processed = [str(v) for v in report.get("processed") or [] if isinstance(v, str)]
    not_found = [str(v) for v in report.get("not_found") or [] if isinstance(v, str)]
    return set(processed) | set(not_found), not_found


def remove_files(queue_file: str) -> None:
    for path in (queue_file, report_path(queue_file), f"{report_path(queue_file)}.tmp"):
        try:
            os.remove(path)
        except OSError:
            pass
