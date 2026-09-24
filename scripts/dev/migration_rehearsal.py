#!/usr/bin/env python3
"""서버 DB 업그레이드 리허설 (저장 계층 재설계 R0, 결정 D-8).

운영 DB 사본을 임시 폴더로 복사해 현재 코드의 upgrade_schema 를 돌리고, 표·열별로 '값이 바뀐 행 수'와 행 수만 출력한다.
값·신고 ID 는 출력하지 않고, 원본은 읽기만 하며, 임시 폴더는 끝나면 지운다(외부 전송 없음).

    .venv/bin/python scripts/dev/migration_rehearsal.py <서버 DB 사본>
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


def snapshot(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    result = {}
    for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        info = list(con.execute(f'PRAGMA table_info("{table}")'))
        cols = [r[1] for r in info]
        pk = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]] or ["rowid"]
        rows = {}
        for row in con.execute(f'SELECT {",".join(chr(34) + c + chr(34) for c in pk)}, * FROM "{table}"'):
            rows[tuple(row[: len(pk)])] = dict(zip(cols, row[len(pk):]))
        result[table] = {"columns": cols, "rows": rows}
    con.close()
    return result


def compare(before: dict, after: dict) -> dict:
    report = {}
    for table in sorted(set(before) | set(after)):
        b, a = before.get(table), after.get(table)
        if b is None or a is None:
            report[table] = {"status": "added" if b is None else "removed", "rows_after": len(a["rows"]) if a else 0}
            continue
        changed = {}
        for col in b["columns"]:
            n = sum(1 for key, row in b["rows"].items() if key in a["rows"] and a["rows"][key].get(col) != row.get(col))
            if n:
                changed[col] = n
        report[table] = {
            "rows_before": len(b["rows"]), "rows_after": len(a["rows"]),
            "rows_missing": sum(1 for k in b["rows"] if k not in a["rows"]),
            "new_columns": [c for c in a["columns"] if c not in b["columns"]],
            "changed_rows_by_column": changed,
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    work = Path(tempfile.mkdtemp(prefix="sr_migration_"))
    try:
        data_dir = work / "data"
        data_dir.mkdir()
        target = data_dir / "data.db"
        src = sqlite3.connect(f"file:{args.source.resolve()}?mode=ro", uri=True)
        dst = sqlite3.connect(target)
        src.backup(dst)
        dst.close()
        src.close()
        before = snapshot(target)
        env = dict(os.environ, SAFETYREPORT_DATA_DIR=str(data_dir), SAFETYREPORT_FIXTURE_MODE="1", PYTHONPATH=str(REPO_ROOT))
        code = ("from core.database.engine import get_engine\nfrom core.database import database\nfrom core.utils import logger\n"
                "logger.LoggerFactory.create_logger()\ndatabase.upgrade_schema(get_engine())\n")
        out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, cwd=REPO_ROOT)
        if out.returncode != 0:
            print(json.dumps({"error": "upgrade_schema failed", "stderr_tail": out.stderr[-2000:]}, ensure_ascii=False))
            return 2
        print(json.dumps(compare(before, snapshot(target)), ensure_ascii=False, indent=1))
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
