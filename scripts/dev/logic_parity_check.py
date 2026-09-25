#!/usr/bin/env python3
"""서버↔모바일 계산 동등성 검사 — 같은 데이터로 대시보드·통계·통계 요약을 양쪽이 각자 계산해 비교한다.

  서버 DB S0 → (모바일 importFromServerDb) → M1
  서버: S0 에서 get_dashboard_stats / get_agency_stats / get_stats_overview
  모바일: M1 에서 computeSummary / computeStats / computeStatsOverview (test/tool/logic_parity_harness_test.dart)
  조합: 취하 제외(exclude_withdraw) × 대표건(canonical/raw) × 경찰 기관명 정규화(통계만)

    .venv/bin/python scripts/dev/logic_parity_check.py --mobile-repo ../safetyreport-mobile-stats
    .venv/bin/python scripts/dev/logic_parity_check.py --mobile-repo ../safetyreport-mobile-stats --server-db <사본> --summary-only

운영 사본은 읽기만 하고 임시 폴더에서만 계산한다(결정 D-8). --summary-only 는 값·신고 없이 경로별 차이 개수만 낸다.
차이가 하나라도 있으면 종료코드 1. 한쪽에만 있는 키는 따로 센다(표시용 필드 차이일 수 있어 실패로 치지 않음).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.dev.db_roundtrip_check import (  # noqa: E402
    REPO_ROOT, _server_env, build_server_db, mobile_import, prepare_server_copy,
)

SUMMARY_KEYS = [
    "total", "acceptCount", "partialCount", "rejectCount", "supplementCount", "processingCount",
    "completedCount", "withdrawCount", "withdrawRawCount", "tFineCount", "tPenaltyCount",
    "tRejectCount", "tUnconfirmedCount",
]

SERVER_CODE = r'''
import json, sys
import settings.settings as st
from core.database.engine import get_engine
from core.utils import logger
from services import report_stats_service as rs
logger.LoggerFactory.create_logger()
engine = get_engine()
keys = json.loads(sys.argv[2])
out = {}
for ew in (False, True):
    for rep in (False, True):
        st._instance.exclude_withdraw = ew
        mode = "canonical" if rep else "raw"
        tag = f"ew={str(ew).lower()}|rep={str(rep).lower()}"
        d = rs.get_dashboard_stats(engine, mode=mode)
        out["summary|" + tag] = {k: d.get(k) for k in keys}
        for np_ in (False, True):
            st._instance.normalize_police = np_
            out[f"stats|{tag}|np={str(np_).lower()}"] = rs.get_agency_stats(engine, None, mode=mode)
        st._instance.normalize_police = False
        out["overview|" + tag] = rs.get_stats_overview(engine, None, mode=mode)
# 필터 조합: 가장 최근 연도, 교통 첫 법규, 법규 없음, 연도+법규 (값은 이 데이터에서 고른다)
st._instance.exclude_withdraw = False
base = rs.get_agency_stats(engine, None, mode="raw")
years = base.get("available_years") or []
laws = base["traffic"].get("available_laws") or []
filters = []
if years:
    filters.append({"year": years[0]})
if laws:
    filters.append({"law": laws[0]})
    if years:
        filters.append({"year": years[0], "law": laws[0]})
filters.append({"law": "__없음__"})
for f in filters:
    for ew in (False, True):
        for rep in (False, True):
            st._instance.exclude_withdraw = ew
            mode = "canonical" if rep else "raw"
            ftag = ",".join(f"{k}={v}" for k, v in sorted(f.items()))
            tag = f"ew={str(ew).lower()}|rep={str(rep).lower()}|f=" + ftag
            out["fstats|" + tag] = rs.get_agency_stats(engine, dict(f), mode=mode)
            out["foverview|" + tag] = rs.get_stats_overview(engine, dict(f), mode=mode)
out["_filters"] = filters
json.dump(out, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, default=str)
'''

_ROW_KEYS = ("law", "agency", "person", "month")


def _row_key(row: dict) -> str:
    # 빈 식별 필드(예: 기관표 행의 person="")는 없는 것으로 본다
    return "|".join(f"{k}={row.get(k)}" for k in _ROW_KEYS if row.get(k) not in (None, ""))


def _num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return v


def compare(server, mobile, path, diffs, one_side):
    if isinstance(server, dict) and isinstance(mobile, dict):
        for k in sorted(set(server) | set(mobile)):
            if k not in mobile:
                one_side.append(f"{path}.{k} (서버만)")
            elif k not in server:
                one_side.append(f"{path}.{k} (모바일만)")
            else:
                compare(server[k], mobile[k], f"{path}.{k}", diffs, one_side)
        return
    if isinstance(server, list) and isinstance(mobile, list):
        if server and isinstance(server[0], dict) and any(k in server[0] for k in _ROW_KEYS):
            sm = {_row_key(r): r for r in server}
            mm = {_row_key(r): r for r in mobile}
            for k in sorted(set(sm) | set(mm)):
                if k not in mm:
                    diffs.append((f"{path}[]", f"행이 서버에만: {k}"))
                elif k not in sm:
                    diffs.append((f"{path}[]", f"행이 모바일에만: {k}"))
                else:
                    compare(sm[k], mm[k], f"{path}[]", diffs, one_side)
            return
        if len(server) != len(mobile):
            diffs.append((path, f"길이 서버 {len(server)} / 모바일 {len(mobile)}"))
            return
        for i, (a, b) in enumerate(zip(server, mobile)):
            compare(a, b, f"{path}[{i}]", diffs, one_side)
        return
    a, b = _num(server), _num(mobile)
    if isinstance(a, float) and isinstance(b, float):
        if abs(a - b) > 1e-9:
            diffs.append((path, f"서버 {server} / 모바일 {mobile}"))
    elif (a in (None, "") and b in (None, "")) or a == b:
        return
    else:
        diffs.append((path, f"서버 {server!r} / 모바일 {mobile!r}"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mobile-repo", type=Path, required=True)
    parser.add_argument("--flutter", default=shutil.which("flutter") or "flutter")
    parser.add_argument("--server-db", type=Path, help="fixture 대신 이 서버 DB 의 사본으로 시작(원본은 읽기만)")
    parser.add_argument("--summary-only", action="store_true", help="값 없이 경로별 차이 개수만")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="sr_logic_parity_"))
    try:
        s0 = prepare_server_copy(args.server_db.resolve(), work / "s0") if args.server_db else build_server_db(work / "s0")
        m1 = mobile_import(s0, work / "m1.db", args.mobile_repo.resolve(), args.flutter)

        server_json = work / "server.json"
        run = subprocess.run([sys.executable, "-c", SERVER_CODE, str(server_json), json.dumps(SUMMARY_KEYS)],
                             env=_server_env(s0.parent), capture_output=True, text=True, cwd=REPO_ROOT)
        if run.returncode != 0:
            raise SystemExit("server compute failed:\n" + run.stderr[-3000:])
        mobile_json = work / "mobile.json"
        server_filters = json.loads(server_json.read_text(encoding="utf-8")).get("_filters", [])
        env = dict(os.environ, SR_LP_MOBILE_DB=str(m1), SR_LP_OUT=str(mobile_json),
                   SR_LP_FILTERS=json.dumps(server_filters, ensure_ascii=False))
        run = subprocess.run([args.flutter, "test", "test/tool/logic_parity_harness_test.dart"], env=env,
                             cwd=args.mobile_repo.resolve(), capture_output=True, text=True)
        if run.returncode != 0 or not mobile_json.exists():
            raise SystemExit("mobile compute failed:\n" + (run.stdout + run.stderr)[-3000:])

        server = json.loads(server_json.read_text(encoding="utf-8"))
        mobile = json.loads(mobile_json.read_text(encoding="utf-8"))
        server.pop("_filters", None)
        diffs, one_side = [], []
        for key in sorted(set(server) | set(mobile)):
            if key not in server or key not in mobile:
                diffs.append((key, "조합이 한쪽에만"))
                continue
            compare(server[key], mobile[key], key, diffs, one_side)

        by_path: dict[str, int] = {}
        for p, _ in diffs:
            generic = re.sub(r"(ew|rep|np)=(true|false)", r"\1=*", p)
            generic = re.sub(r"\|f=[^.]*", "|f=*", generic) if args.summary_only else generic
            by_path[generic] = by_path.get(generic, 0) + 1
        report = {
            "combinations": len(server),
            "diff_count": len(diffs),
            "diff_by_path": dict(sorted(by_path.items())),
            "one_side_keys": sorted({re.sub(r"(ew|rep|np)=(true|false)", r"\1=*", k) for k in one_side}),
        }
        if not args.summary_only:
            report["diff_samples"] = [f"{p}: {m}" for p, m in diffs[:40]]
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 1 if diffs else 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
