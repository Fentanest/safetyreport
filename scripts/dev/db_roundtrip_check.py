#!/usr/bin/env python3
"""서버↔모바일 DB 왕복 검사 (PROJECT_RULES §3-1: 교환·변환에 오류·누락 0).

실제 변환 코드를 그대로 이어서 돌린다.
  A. 서버 DB S0 → (모바일 importFromServerDb, Dart) → M1 → (서버 restore_from_mobile_db) → S2 : S0 == S2
  B. 모바일 DB M1 → (서버 restore) → S2 → (모바일 import) → M3 : M1 == M3
모든 컬럼을 원시 값(NULL 과 '' 구분, 정수/실수 포함)으로 비교한다. 차이가 하나라도 있으면 종료코드 1.

    .venv/bin/python scripts/dev/db_roundtrip_check.py --mobile-repo ../safetyreport-mobile-stats
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_TABLES_BY_ID = [
    "mysafety", "mysafetydetail_traffic", "mysafetydetail_parking", "mysafetydetail_other",
    "mysafetymerge_traffic", "mysafetymerge_parking", "mysafetymerge_other",
    "mysafety_entry_value", "mysafety_raw_content",
]
MOBILE_TABLES = {"reports": "ID", "report_raw": "ID", "sync_meta": "key", "geocode_cache": "주소정규화"}


def _server_env(data_dir: Path) -> dict:
    env = dict(os.environ)
    env["SAFETYREPORT_DATA_DIR"] = str(data_dir)
    env["SAFETYREPORT_FIXTURE_MODE"] = "1"  # 복원 뒤 지오코딩 재개 등 외부 요청 차단
    env["PYTHONPATH"] = str(REPO_ROOT)
    return env


def build_server_db(data_dir: Path) -> Path:
    """fixture 24건 + 까다로운 값(사진 시각, NULL/'' 구분, 줄바꿈, 한글, 실수 좌표)."""
    code = r'''
import sys
from scripts.dev import fixture_server
from core.database.engine import get_engine
from core.utils import logger
from sqlalchemy import text
logger.LoggerFactory.create_logger()
engine = get_engine()
fixture_server.seed_engine(engine)
with engine.begin() as conn:
    for t in ("mysafetydetail_parking",):
        conn.execute(text(f"UPDATE {t} SET 사진_첫촬영='2026-09-22 01:00:00', 사진_끝촬영='2026-09-22 02:30:00', 사진_촬영수=3 WHERE ID='90000101'"))
        conn.execute(text(f"UPDATE {t} SET 사진_촬영수=0 WHERE ID='90000102'"))
        conn.execute(text(f"UPDATE {t} SET 위도=37.5601234, 경도=126.8301234, 주소정규화='서울특별시 강서구 등촌동 101', 지오코딩상태='ok' WHERE ID='90000103'"))
    conn.execute(text("UPDATE mysafetydetail_other SET 처리내용='첫 줄\n둘째 줄\n“따옴표”' WHERE ID='90000201'"))
    conn.execute(text("UPDATE mysafetydetail_traffic SET 벌점=NULL WHERE ID='90000003'"))
    conn.execute(text("UPDATE mysafety SET 별점사유='' WHERE ID='90000001'"))
    conn.execute(text("INSERT OR REPLACE INTO mysafety_sync_meta(key, value) VALUES ('watchlist', 'SPP-2604-9000006,SPP-2608-9000010')"))
    conn.execute(text("INSERT OR REPLACE INTO mysafety_geocode_cache(주소정규화, 원본주소, 행정구역, 위도, 경도, 상태, source, error_message, updated_at) "
                      "VALUES ('서울특별시 강서구 등촌동 101', '서울 강서구 등촌동 101 앞', '서울특별시 강서구', 37.5601234, 126.8301234, 'ok', 'kakao', '', 1790000000123)"))
    conn.execute(text("INSERT OR REPLACE INTO mysafety_geocode_cache(주소정규화, 원본주소, 행정구역, 위도, 경도, 상태, source, error_message, updated_at) "
                      "VALUES ('서울특별시 없는구 1', '서울 없는구 1', '', NULL, NULL, 'failed', 'kakao', 'not found', 1790000000456)"))
from core.database import database
database.merge_final(engine)
print(engine.url.database)
'''
    out = subprocess.run([sys.executable, "-c", code], env=_server_env(data_dir), capture_output=True, text=True, cwd=REPO_ROOT)
    if out.returncode != 0:
        raise SystemExit("server db build failed:\n" + out.stderr[-3000:])
    return data_dir / "data.db"


def prepare_server_copy(source: Path, data_dir: Path) -> Path:
    """외부 서버 DB(예: 운영 사본)를 임시 폴더로 복사하고 서버 기동과 같은 upgrade_schema 를 적용한다. 원본은 읽기만 한다."""
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "data.db"
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(target)
    src.backup(dst)  # WAL 내용까지 일관된 사본
    dst.close()
    src.close()
    code = r'''
from core.database.engine import get_engine
from core.database import database
from core.utils import logger
logger.LoggerFactory.create_logger()
database.upgrade_schema(get_engine())
'''
    out = subprocess.run([sys.executable, "-c", code], env=_server_env(data_dir), capture_output=True, text=True, cwd=REPO_ROOT)
    if out.returncode != 0:
        raise SystemExit("server copy upgrade failed:\n" + out.stderr[-3000:])
    return target


def summarize(diffs: list[str]) -> dict:
    """값·신고 ID 없이 표·열별 차이 개수만 (운영 사본 리허설용, 결정 D-8)."""
    import re
    counts: dict[str, int] = {}
    for d in diffs:
        m = re.match(r"\[(?P<label>[^\]]+)\] (?:(?P<kind>행 누락|행 추가)|[^.]*\.(?P<col>[^: (]+))", d)
        if not m:
            key = "기타"
        elif m.group("kind"):
            key = f"{m.group('label')} :: {m.group('kind')}"
        else:
            key = f"{m.group('label')} :: {m.group('col')}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def table_counts(db: Path, tables) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    result = {}
    for t in tables:
        try:
            result[t] = con.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        except sqlite3.OperationalError:
            result[t] = None
    con.close()
    return result


def restore_mobile_into_server(mobile_db: Path, data_dir: Path) -> Path:
    code = r'''
import sys
from services import db_backup
from core.utils import logger
logger.LoggerFactory.create_logger()
db_backup.restore_from_mobile_db(sys.argv[1])
'''
    data_dir.mkdir(parents=True, exist_ok=True)
    out = subprocess.run([sys.executable, "-c", code, str(mobile_db)], env=_server_env(data_dir), capture_output=True, text=True, cwd=REPO_ROOT)
    if out.returncode != 0:
        raise SystemExit("server restore failed:\n" + out.stderr[-3000:])
    return data_dir / "data.db"


def mobile_import(server_db: Path, mobile_out: Path, mobile_repo: Path, flutter: str) -> Path:
    env = dict(os.environ, SR_RT_MODE="import", SR_RT_SERVER_DB=str(server_db), SR_RT_MOBILE_OUT=str(mobile_out))
    out = subprocess.run([flutter, "test", "test/tool/db_roundtrip_harness_test.dart"], env=env, cwd=mobile_repo, capture_output=True, text=True)
    if out.returncode != 0 or not mobile_out.exists():
        raise SystemExit("mobile import failed:\n" + (out.stdout + out.stderr)[-3000:])
    return mobile_out


def _rows(db: Path, table: str, key: str) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(f'SELECT * FROM "{table}"').fetchall()
    except sqlite3.OperationalError:
        return {}
    finally:
        pass
    result = {str(dict(r)[key]): dict(r) for r in rows}
    con.close()
    return result


def _dup_meta(db: Path, group_table: str, member_table: str) -> tuple[dict, dict]:
    groups = _rows(db, group_table, "group_id")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        members = {f'{r["group_id"]}|{r["report_id"]}': dict(r) for r in con.execute(f'SELECT * FROM "{member_table}"')}
    except sqlite3.OperationalError:
        members = {}
    con.close()
    return groups, members


def compare(label: str, before: dict, after: dict, ignore_cols=()) -> list[str]:
    diffs = []
    for key in sorted(set(before) | set(after)):
        if key not in after:
            diffs.append(f"[{label}] 행 누락: {key}")
            continue
        if key not in before:
            diffs.append(f"[{label}] 행 추가: {key}")
            continue
        for col in sorted(set(before[key]) | set(after[key])):
            if col in ignore_cols:
                continue
            if col not in after[key]:
                diffs.append(f"[{label}] {key}.{col} 컬럼 누락 (값 {before[key].get(col)!r})")
            elif col not in before[key]:
                diffs.append(f"[{label}] {key}.{col} 컬럼 추가 (값 {after[key].get(col)!r})")
            elif before[key][col] != after[key][col] or type(before[key][col]) is not type(after[key][col]):
                diffs.append(f"[{label}] {key}.{col}: {before[key][col]!r} → {after[key][col]!r}")
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mobile-repo", required=True, type=Path)
    parser.add_argument("--flutter", default=shutil.which("flutter") or "flutter")
    parser.add_argument("--keep", action="store_true", help="임시 파일 유지")
    parser.add_argument("--server-db", type=Path, help="fixture 대신 이 서버 DB 의 사본으로 시작(원본은 읽기만)")
    parser.add_argument("--summary-only", action="store_true", help="값·ID 없이 표·열별 차이 개수와 행 수만 출력")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="sr_roundtrip_"))
    try:
        s0 = prepare_server_copy(args.server_db.resolve(), work / "s0") if args.server_db else build_server_db(work / "s0")
        m1 = mobile_import(s0, work / "m1.db", args.mobile_repo.resolve(), args.flutter)
        s2 = restore_mobile_into_server(m1, work / "s2")
        m3 = mobile_import(s2, work / "m3.db", args.mobile_repo.resolve(), args.flutter)

        diffs = []
        # 저장 계약은 두 레포에 바이트 동일해야 한다(저장 계층 재설계 R0).
        import hashlib
        contract_paths = [REPO_ROOT / "contracts" / "storage-contract.json", args.mobile_repo.resolve() / "contracts" / "storage-contract.json"]
        digests = [hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None for p in contract_paths]
        if None in digests or digests[0] != digests[1]:
            diffs.append(f"[contract] 서버·모바일 storage-contract.json 불일치: {digests}")
        keys = {"mysafety_entry_value": "ID", "mysafety_raw_content": "ID"}
        for table in SERVER_TABLES_BY_ID:
            diffs += compare(f"A:{table}", _rows(s0, table, keys.get(table, "ID")), _rows(s2, table, keys.get(table, "ID")))
        diffs += compare("A:mysafety_watchlist", _rows(s0, "mysafety_watchlist", "신고번호"), _rows(s2, "mysafety_watchlist", "신고번호"))
        # map_backfill_state 는 데이터가 아니라 서버 지도 백필 런타임 상태다. 복원 직후 서버가 새로 기록하고, 모바일 import 는 의도적으로 버린다(기존 회귀 테스트).
        meta0 = {k: v for k, v in _rows(s0, "mysafety_sync_meta", "key").items() if k != "map_backfill_state"}
        meta2 = {k: v for k, v in _rows(s2, "mysafety_sync_meta", "key").items() if k != "map_backfill_state"}
        diffs += compare("A:mysafety_sync_meta", meta0, meta2)
        g0, mem0 = _dup_meta(s0, "mysafety_duplicate_group", "mysafety_duplicate_member")
        g2, mem2 = _dup_meta(s2, "mysafety_duplicate_group", "mysafety_duplicate_member")
        diffs += compare("A:duplicate_group", g0, g2)
        diffs += compare("A:duplicate_member", mem0, mem2)
        diffs += compare("A:mysafety_geocode_cache", _rows(s0, "mysafety_geocode_cache", "주소정규화"), _rows(s2, "mysafety_geocode_cache", "주소정규화"))

        for table, key in MOBILE_TABLES.items():
            diffs += compare(f"B:{table}", _rows(m1, table, key), _rows(m3, table, key))
        mg1, mm1 = _dup_meta(m1, "duplicate_group", "duplicate_member")
        mg3, mm3 = _dup_meta(m3, "duplicate_group", "duplicate_member")
        diffs += compare("B:duplicate_group", mg1, mg3)
        diffs += compare("B:duplicate_member", mm1, mm3)

        if args.summary_only:
            server_tables = SERVER_TABLES_BY_ID + ["mysafety_watchlist", "mysafety_sync_meta", "mysafety_geocode_cache", "mysafety_duplicate_group", "mysafety_duplicate_member"]
            mobile_tables = list(MOBILE_TABLES) + ["duplicate_group", "duplicate_member"]
            report = {"diff_count": len(diffs), "diff_by_table_column": summarize(diffs),
                      "rows": {"S0": table_counts(s0, server_tables), "S2": table_counts(s2, server_tables),
                               "M1": table_counts(m1, mobile_tables), "M3": table_counts(m3, mobile_tables)},
                      # 키 이름만(값 제외): last_sync / watchlist / map_backfill_state 같은 메타 키
                      "sync_meta_keys": {"S0": sorted(_rows(s0, "mysafety_sync_meta", "key")), "S2": sorted(_rows(s2, "mysafety_sync_meta", "key")),
                                         "M1": sorted(_rows(m1, "sync_meta", "key")), "M3": sorted(_rows(m3, "sync_meta", "key"))}}
            print(json.dumps(report, ensure_ascii=False, indent=1))
            return 1 if diffs else 0
        report = {"work_dir": str(work), "diff_count": len(diffs), "diffs": diffs}
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 1 if diffs else 0
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
