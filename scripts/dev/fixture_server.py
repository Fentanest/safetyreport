#!/usr/bin/env python3
"""개발/UI 테스트용 fixture 서버.

운영 data/ 와 분리된 데이터 루트에 합성(가짜) 데이터를 만들고, 외부 부작용을 막은 상태로
실제 FastAPI 앱(main.app)을 띄운다. 운영 기본 동작은 바꾸지 않는다.

    python scripts/dev/fixture_server.py serve --data-dir .agent-runs/fixture/opus --port 18619 --reset
    python scripts/dev/fixture_server.py seed  --data-dir .agent-runs/fixture/opus --reset

- 환경변수 SAFETYREPORT_DATA_DIR / SAFETYREPORT_FIXTURE_MODE=1 을 앱 import 전에 설정한다
  (core/utils/runtime_mode.py 참고).
- 데이터 루트에는 표식 파일(.safetyreport-fixture)이 있어야 재사용/초기화할 수 있다.
  저장소 data/ 이거나 표식 없는 기존 data.db 가 있으면 거부한다.
- 로그인: fixture-admin / fixture-pass-1234 (테스트 전용 계정). API 키는 <data-dir>/fixture-api-key.txt.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKER = ".safetyreport-fixture"
ADMIN_USER = "fixture-admin"
ADMIN_PASSWORD = "fixture-pass-1234"

# 합성 데이터. 실제 신고/인물/차량과 무관하다. 기대값을 손으로 셀 수 있도록 작게 유지한다.
# (category, ID, 신고번호, 신고명, 신고일, 처리상태, 범칙금_과태료, 처리기관, 담당자, 답변일, 위반법규, 별점, 위반장소)
_REPORTS = [
    ("traffic", "90000001", "SPP-2512-9000001", "신호위반", "2025-12-30 23:40:00", "수용", "과태료 40,000원", "서울특별시 강서경찰서 교통과", "김담당", "2026-01-02", "도로교통법 제5조", 5, "서울특별시 강서구 마곡동 1"),
    ("traffic", "90000002", "SPP-2601-9000002", "중앙선 침범", "2026-01-03 08:10:00", "수용", "범칙금 60,000원", "서울특별시 강서경찰서 교통과", "김담당", "2026-01-09", "도로교통법 제13조", 4, "서울특별시 강서구 마곡동 2"),
    ("traffic", "90000003", "SPP-2601-9000003", "진로변경 위반", "2026-01-20 12:00:00", "불수용", "", "경기도 부천원미경찰서", "이담당", "2026-01-25", "도로교통법 제19조", None, "경기도 부천시 원미구 중동 3"),
    ("traffic", "90000004", "SPP-2602-9000004", "신호위반", "2026-02-11 09:30:00", "수용", "경고", "경기도 부천원미경찰서", "이담당", "2026-02-20", "도로교통법 제5조", 3, "경기도 부천시 원미구 중동 4"),
    ("traffic", "90000005", "SPP-2603-9000005", "버스전용차로 위반", "2026-03-05 18:00:00", "수용", "과태료", "서울특별시 강서구청 교통행정과 주정차단속팀 (장문 기관명 표시 확인용)", "박담당", "2026-03-12", "도로교통법 제15조", None, "서울특별시 강서구 화곡동 5"),
    ("traffic", "90000006", "SPP-2604-9000006", "신호위반", "2026-04-01 07:00:00", "처리중", "", "서울특별시 강서경찰서 교통과", "", "", "", None, "서울특별시 강서구 마곡동 6"),
    ("traffic", "90000007", "SPP-2605-9000007", "안전모 미착용", "2026-05-10 10:00:00", "일부수용", "미확인", "인천광역시 서부경찰서", "김담당", "2026-05-25", "도로교통법 제50조", 2, "인천광역시 서구 7"),
    ("traffic", "90000008", "SPP-2606-9000008", "신호위반", "2026-06-15 21:00:00", "기타", "", "인천광역시 서부경찰서", "최담당", "2026-06-16", "", 1, "인천광역시 서구 8"),
    ("traffic", "90000009", "SPP-2607-9000009", "끼어들기", "2026-07-01 08:00:00", "취하", "", "서울특별시 강서경찰서 교통과", "김담당", "2026-07-02", "도로교통법 제23조", None, "서울특별시 강서구 마곡동 9"),
    ("traffic", "90000010", "SPP-2608-9000010", "신호위반", "2026-08-20 08:00:00", "보완요청", "", "서울특별시 강서경찰서 교통과", "김담당", "", "", None, "서울특별시 강서구 마곡동 10"),
    ("traffic", "90000011", "SPP-2609-9000011", "신호위반", "2026-09-01 08:00:00", "수용", "과태료 40,000원", "서울특별시 강서경찰서 교통과", "김담당", "2026-09-20", "도로교통법 제5조", None, "서울특별시 강서구 마곡동 11"),
    ("traffic", "90000012", "SPP-2609-9000012", "신호위반", "2026-09-01 08:05:00", "불수용", "", "서울특별시 강서경찰서 교통과", "김담당", "2026-09-21", "도로교통법 제5조", None, "서울특별시 강서구 마곡동 11"),
    ("parking", "90000101", "SPP-2601-9000101", "불법주정차신고", "2026-01-05 10:00:00", "수용", "과태료", "서울특별시 강서구청 주차관리과", "정담당", "2026-01-06", "", 5, "서울특별시 강서구 등촌동 101"),
    ("parking", "90000102", "SPP-2602-9000102", "불법주정차신고", "2026-02-05 10:00:00", "불수용", "", "서울특별시 강서구청 주차관리과", "정담당", "2026-02-08", "", None, "서울특별시 강서구 등촌동 102"),
    ("parking", "90000103", "SPP-2603-9000103", "불법주정차신고", "2026-03-05 10:00:00", "수용", "과태료", "경기도 부천시청 주차관리과", "김담당", "2026-03-06", "", 4, "경기도 부천시 원미구 103"),
    ("parking", "90000104", "SPP-2605-9000104", "불법주정차신고", "2026-05-05 10:00:00", "처리중", "", "경기도 부천시청 주차관리과", "", "", "", None, "경기도 부천시 원미구 104"),
    ("parking", "90000105", "SPP-2607-9000105", "불법주정차신고", "2026-07-05 10:00:00", "답변완료", "", "서울특별시 강서구청 주차관리과", "정담당", "2026-07-15", "", None, "서울특별시 강서구 등촌동 105"),
    ("parking", "90000106", "SPP-2609-9000106", "불법주정차신고", "2026-09-10 10:00:00", "취하", "", "서울특별시 강서구청 주차관리과", "정담당", "2026-09-11", "", None, "서울특별시 강서구 등촌동 106"),
    ("other", "90000201", "SPP-2601-9000201", "도로 파손", "2026-01-15 14:00:00", "수용", "", "서울특별시 강서구청 도로과", "한담당", "2026-01-30", "", 5, "서울특별시 강서구 발산동 201"),
    ("other", "90000202", "SPP-2603-9000202", "쓰레기, 폐기물", "2026-03-15 14:00:00", "수용", "과태료", "서울특별시 강서구청 청소행정과", "한담당", "2026-03-20", "", None, "서울특별시 강서구 발산동 202"),
    ("other", "90000203", "SPP-2604-9000203", "가로등 고장", "2026-04-15 14:00:00", "일부수용", "", "한국전력공사 서울지역본부", "오담당", "2026-04-18", "", 3, "서울특별시 강서구 발산동 203"),
    ("other", "90000204", "SPP-2606-9000204", "불법 광고물", "2026-06-15 14:00:00", "불수용", "", "서울특별시 강서구청 도시디자인과", "오담당", "2026-06-30", "", None, "서울특별시 강서구 발산동 204"),
    ("other", "90000205", "SPP-2608-9000205", "보도 적치물", "2026-08-15 14:00:00", "처리중", "", "서울특별시 강서구청 건설관리과", "", "", "", None, "서울특별시 강서구 발산동 205"),
    ("other", "90000206", "SPP-2609-9000206", "도로 파손", "2026-09-15 14:00:00", "보완요청", "", "서울특별시 강서구청 도로과", "한담당", "", "", None, "서울특별시 강서구 발산동 206"),
]

_ENTRY_VALUES = {
    "traffic": "자동차·교통위반-신호위반",
    "parking": "불법주정차신고-기타 불법주정차",
    "other": "안전신고-생활안전",
}

# 같은 원문을 가진 두 교통 신고 → payload exact 중복군 1개(자동 감지 대상)
_DUPLICATE_BODY = "(fixture) 동일 원문 중복 신고 확인용 본문입니다. 실제 신고가 아닙니다."
_DUPLICATE_IDS = {"90000011", "90000012"}
_WATCHLIST = ["SPP-2604-9000006", "SPP-2608-9000010"]
_LONG_BODY = ("(fixture) 장문 신고내용 줄바꿈·가독성 확인용 텍스트입니다. " * 12).strip()


def _vehicle(rid: str, index: int, category: str) -> str:
    if rid in _DUPLICATE_IDS:
        return "99가9999"  # 중복 쌍은 차량번호가 같아야 confirmed_duplicate(대표건 축소) 사례가 된다
    return f"{10 + index}가{1000 + index}" if category != "other" else ""


def _refuse(message: str) -> None:
    print(f"[fixture] 거부: {message}", file=sys.stderr)
    raise SystemExit(2)


def prepare_data_dir(raw: str, *, reset: bool) -> Path:
    data_dir = Path(raw).expanduser().resolve()
    production = (REPO_ROOT / "data").resolve()
    if data_dir == production or production in data_dir.parents:
        _refuse(f"저장소 운영 데이터 경로({production})는 fixture 루트로 쓸 수 없습니다.")
    if data_dir.exists() and any(data_dir.iterdir()) and not (data_dir / MARKER).exists():
        _refuse(f"{data_dir} 에 표식({MARKER}) 없는 기존 파일이 있습니다. 빈 디렉터리나 fixture 루트를 지정하세요.")
    if reset and data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / MARKER).write_text("created by scripts/dev/fixture_server.py\n", encoding="utf-8")
    return data_dir


def activate_environment(data_dir: Path) -> None:
    """앱 모듈 import 전에 반드시 호출."""
    if "settings.settings" in sys.modules:
        _refuse("settings 모듈이 이미 import 되었습니다. 환경변수를 먼저 설정해야 합니다.")
    os.environ["SAFETYREPORT_DATA_DIR"] = str(data_dir)
    os.environ["SAFETYREPORT_FIXTURE_MODE"] = "1"
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


def _write_config(data_dir: Path) -> None:
    config = data_dir / "config.ini"
    if config.exists():
        return
    config.write_text(
        "[SETTINGS]\n"
        "normalize_police = True\n"
        "exclude_withdraw = True\n"
        "use_representative_records = True\n"
        "auto_export_excel = False\n"
        "auto_export_sheet = False\n"
        "\n[SCHEDULER]\nenabled = False\n"
        "\n[SELENIUM]\nchrome_mode = desktop\nheadless = True\n",
        encoding="utf-8",
    )


def seed(data_dir: Path) -> dict:
    _write_config(data_dir)
    from core.database import database, models
    from core.database.engine import get_engine
    from core.utils import logger

    logger.LoggerFactory.create_logger()
    engine = get_engine()
    database.upgrade_schema(engine)

    with engine.begin() as conn:
        if conn.execute(models.title_table.select().limit(1)).first() is not None:
            return {"seeded": False, "reason": "already seeded"}

        detail_tables = {
            "traffic": models.detail_traffic_table,
            "parking": models.detail_parking_table,
            "other": models.detail_other_table,
        }
        for index, (category, rid, rnum, name, reported, status, fine, agency, person, answered, law, rating, place) in enumerate(_REPORTS):
            watch = "Y" if rnum in _WATCHLIST else ""
            conn.execute(models.title_table.insert().values(
                ID=rid, 상태=status, 신고번호=rnum, 신고명=name, 신고일=reported,
                만족도조사여부="참여 완료" if rating else ("참여 가능" if answered else ""),
                별점=rating, 별점사유="(fixture) 별점 사유" if rating else None, 감시목록=watch,
            ))
            closed = status not in {"처리중", "보완요청"}
            body = _DUPLICATE_BODY if rid in _DUPLICATE_IDS else (_LONG_BODY if rid == "90000005" else f"(fixture) {name} 신고 본문 {rid}")
            conn.execute(detail_tables[category].insert().values(
                ID=rid, 처리상태=status, 차량번호=_vehicle(rid, index, category),
                위반법규=law, 범칙금_과태료=fine, 벌점="", 처리기관=agency, 담당자=person, 답변일=answered,
                발생일자=reported[:10], 발생시각=reported[11:16], 위반장소=place,
                종결여부="Y" if closed else "N", 신고내용=body,
                처리내용=f"(fixture) {status} 처리 결과 안내" if answered else "",
                지도="", 첨부사진="/static/logo.png" if index % 5 == 0 else "", 첨부파일="",
                보완횟수=1 if status == "보완요청" else 0,
                보완_미응답="Y" if status == "보완요청" else "N",
                보완_요청_내용="보완 요청자: (fixture) 담당 · 요청 일시: 2026-09-02 10:00 · 완료 일시: -\n사진 추가 요청" if status == "보완요청" else "",
            ))
            conn.execute(models.entry_value_table.insert().values(ID=rid, entry_value=_ENTRY_VALUES[category]))
            if rid in _DUPLICATE_IDS:
                conn.execute(models.raw_content_table.insert().values(
                    ID=rid, raw_content=_DUPLICATE_BODY, raw_type="report_body", saved_at=1790000000000,
                ))
        for rnum in _WATCHLIST:
            conn.execute(models.watchlist_table.insert().values(신고번호=rnum))
        conn.execute(models.sync_meta_table.insert().values(key="last_sync", value="2026-09-23T09:00:00"))

    database.merge_final(engine)
    if not database.has_admin_user(engine):
        database.create_admin_user(engine, ADMIN_USER, ADMIN_PASSWORD)
    api_key = database.create_api_key(engine, "fixture-client")
    (data_dir / "fixture-api-key.txt").write_text(api_key + "\n", encoding="utf-8")
    counts = {category: sum(1 for row in _REPORTS if row[0] == category) for category in ("traffic", "parking", "other")}
    return {"seeded": True, "reports": counts, "admin_user": ADMIN_USER}


def serve(data_dir: Path, host: str, port: int) -> None:
    import uvicorn
    import main as app_main

    from core.utils.runtime_mode import is_fixture_mode
    assert is_fixture_mode(), "fixture 모드가 활성화되지 않았습니다."
    print(f"[fixture] data={data_dir} url=http://{host}:{port} user={ADMIN_USER}", flush=True)
    uvicorn.run(app_main.app, host=host, port=port, log_config=app_main._UVICORN_LOG_CONFIG,
                ws_ping_interval=None, ws_ping_timeout=None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["seed", "serve"])
    parser.add_argument("--data-dir", required=True, help="fixture 데이터 루트 (운영 data/ 금지)")
    parser.add_argument("--reset", action="store_true", help="fixture 루트를 지우고 다시 만든다")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18619)
    parser.add_argument("--no-seed", action="store_true", help="serve 시 합성 데이터를 넣지 않는다(빈 DB 확인용)")
    args = parser.parse_args()

    data_dir = prepare_data_dir(args.data_dir, reset=args.reset)
    activate_environment(data_dir)
    if args.command == "seed" or not args.no_seed:
        print("[fixture] seed:", json.dumps(seed(data_dir), ensure_ascii=False), flush=True)
    if args.command == "serve":
        serve(data_dir, args.host, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
