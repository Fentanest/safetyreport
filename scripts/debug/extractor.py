"""디버그 추출기 — 신고 1건 이상의 상세 API 응답을 받아 원문과 파싱 결과를 파일로 남긴다(DB 갱신 없음).

2026-09-25 레거시(Selenium HTML) 크롤링 제거로 API 전용이 됐다(예전의 API vs Selenium 비교는 없앴다).
direct_login 세션(브라우저 없음)으로 부른다.

사용법:
    python scripts/debug/extractor.py SPP-2604-1234567      # 신고번호
    python scripts/debug/extractor.py 59216726 40871819     # 내부 ID 여러 개

출력(`data/logs/`): `{id}_api_raw.json`(원시 응답), `{id}_api_parsed.txt`(파서 결과).
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import settings.settings as settings  # noqa: E402
from core.utils import logger  # noqa: E402
import services.parser as doc_parser  # noqa: E402
from sqlalchemy import select  # noqa: E402
from core.database.models import title_table  # noqa: E402


def lookup_id_by_report_number(engine, report_number: str):
    with engine.connect() as conn:
        result = conn.execute(
            select(title_table.c.ID).where(title_table.c.신고번호 == report_number)
        ).first()
        return result[0] if result else None


def _resolve_id(engine, input_arg: str):
    if not input_arg.lstrip('-').isdigit():
        rid = lookup_id_by_report_number(engine, input_arg)
        if rid is None:
            print(f"[오류] DB에서 신고번호 '{input_arg}'를 찾을 수 없습니다.")
            return None
        print(f"신고번호 {input_arg} → 내부 ID: {rid}")
        return str(rid)
    print(f"내부 ID: {input_arg}")
    return input_arg


def _process_one(session, record_id, out):
    from core.crawler import crawldetail_api

    print(f"\n{'=' * 50}\n[처리] ID: {record_id}")
    raw, session = crawldetail_api._fetch_detail(session, record_id)

    raw_path = os.path.join(out, f"{record_id}_api_raw.json")
    with open(raw_path, 'w', encoding='utf-8') as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
    print(f"  원시 JSON 저장: {raw_path}")

    if not isinstance(raw, dict) or "error" in raw or "result" not in raw:
        print(f"  [경고] API 응답 오류: {raw.get('error', '알 수 없음') if isinstance(raw, dict) else raw}")
        return session

    parsed = doc_parser.parse_json_details(raw["result"])
    parsed_path = os.path.join(out, f"{record_id}_api_parsed.txt")
    with open(parsed_path, 'w', encoding='utf-8') as f:
        for k, v in parsed.items():
            f.write(f"{k}: {v}\n")
    print(f"  파싱 결과 저장: {parsed_path}")
    return session


if __name__ == "__main__":
    input_args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not input_args:
        print("사용법: python scripts/debug/extractor.py <신고번호|내부ID> [신고번호|내부ID ...]")
        sys.exit(1)

    logger.LoggerFactory.create_logger()
    from core.database.engine import get_engine
    from core.crawler import direct_login

    engine = get_engine()
    record_ids = [rid for rid in (_resolve_id(engine, arg) for arg in input_args) if rid]
    if not record_ids:
        print("[오류] 처리할 ID가 없습니다.")
        sys.exit(1)

    out = settings.logpath
    os.makedirs(out, exist_ok=True)
    session, _ = direct_login.make_authorized_session()
    for rid in record_ids:
        try:
            session = _process_one(session, rid, out)
        except Exception as e:
            print(f"  [오류] {rid}: {e}")
    print("\n--- 완료 ---")
