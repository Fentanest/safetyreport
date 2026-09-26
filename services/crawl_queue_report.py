"""대기 큐 크롤의 번호별 결과 보고(감사 R5-01). 자식(start.py)이 쓰고 부모(crawl_manager)가 읽는다.

processed = 상세 저장까지 끝난 번호, not_found = 목록 전 페이지를 성공적으로 훑어도 없는 번호,
ambiguous = 목록 전 페이지를 훑은 뒤에도 여러 신고에 걸려 정할 수 없는 번호(사용자가 정확한 번호로 다시 요청해야 함).
여기 없는 번호(로그인·네트워크·저장 실패, 목록 탐색 실패·상한, 중간 중단)는 부모가 큐에 남겨 다시 시도한다.
자식의 종료 코드는 믿지 않는다 — 실패해도 0 으로 끝날 수 있다.
"""
from __future__ import annotations

import json
import os


def report_path(queue_file: str) -> str:
    return f"{queue_file}.done.json"


def write(queue_file: str, processed, not_found, ambiguous=()) -> None:
    """원자적으로 쓴다(fsync + replace). 실패하면 OSError — 호출자는 기록만 하고, 부모는 보고가 없으니 전부 재시도한다."""
    path = report_path(queue_file)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"processed": sorted(set(processed)), "not_found": sorted(set(not_found)),
                   "ambiguous": sorted(set(ambiguous))}, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def read(queue_file: str) -> tuple[set, list, list]:
    """(끝난 번호 집합, 없는 번호, 모호한 번호). 보고가 없거나 깨졌거나 형식이 다르면 (빈 집합, [], []) — 아무것도 끝나지 않은 것으로 본다
    (감사 R6-05: 유효한 JSON 이라도 필드가 문자열 목록이 아니면 버린다)."""
    try:
        with open(report_path(queue_file), encoding="utf-8") as f:
            report = json.load(f)
    except (OSError, ValueError):
        return set(), [], []
    if not isinstance(report, dict):
        return set(), [], []
    fields = {}
    for key in ("processed", "not_found", "ambiguous"):
        value = report.get(key, [])
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            return set(), [], []
        fields[key] = value
    return set(fields["processed"]) | set(fields["not_found"]) | set(fields["ambiguous"]), fields["not_found"], fields["ambiguous"]


def remove_files(queue_file: str) -> None:
    for path in (queue_file, report_path(queue_file), f"{report_path(queue_file)}.tmp"):
        try:
            os.remove(path)
        except OSError:
            pass


# ── 처리하지 못하고 큐에서 뺀 번호(감사 R7-03) ─────────────────────────────────
UNRESOLVED_FILE = "crawl_queue_unresolved.json"
UNRESOLVED_KEEP = 50


def _unresolved_path() -> str:
    import settings.settings as s
    return os.path.join(s.datapath, UNRESOLVED_FILE)


def record_unresolved(not_found, ambiguous) -> bool:
    """최근 항목부터 최대 UNRESOLVED_KEEP 개. 파일에 남겼으면 True, 못 쓰면 False(호출자는 번호를 큐에 남긴다, 감사 R8-03)."""
    from datetime import datetime, timezone

    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = [{"number": n, "reason": "not_found", "at": at} for n in not_found] + \
          [{"number": n, "reason": "ambiguous", "at": at} for n in ambiguous]
    items = new + [e for e in unresolved() if e.get("number") not in {x["number"] for x in new}]
    path = _unresolved_path()
    try:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items[:UNRESOLVED_KEEP], f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        from core.utils import logger
        logger.LoggerFactory.logbot.warning("[crawl] 처리하지 못한 번호 기록을 저장하지 못함")
        return False
    return True


def unresolved() -> list[dict]:
    try:
        with open(_unresolved_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    return [e for e in data if isinstance(e, dict) and isinstance(e.get("number"), str)] if isinstance(data, list) else []
