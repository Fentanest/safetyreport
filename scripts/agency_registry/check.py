#!/usr/bin/env python3
"""One-shot verification: validate the canonical snapshot, compare the three
copies byte-for-byte, and run the Python vector tests.

Usage: check.py --repos <pc> <mobile> <map> [--run-tests]
Exits non-zero on any mismatch. Never writes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repos", type=Path, nargs=3, required=True, metavar=("PC", "MOBILE", "MAP"))
    ap.add_argument("--run-tests", action="store_true", help="Also run the PC vector tests")
    args = ap.parse_args()
    problems: list[str] = []

    canon = args.repos[0] / "shared" / "agency-region-registry"
    try:
        manifest_files = json.loads((canon / "manifest.json").read_text(encoding="utf-8"))["files"].keys()
        files = list(dict.fromkeys(["manifest.json", "provenance.json", "schema.md", *manifest_files]))
    except (OSError, ValueError) as exc:
        print(f"canonical manifest unreadable: {exc}")
        return 1
    for repo in args.repos[1:]:
        other = repo / "shared" / "agency-region-registry"
        for rel in files:
            a, b = canon / rel, other / rel
            if not b.is_file():
                problems.append(f"{repo.name}: missing {rel}")
            elif sha256_file(a) != sha256_file(b):
                problems.append(f"{repo.name}: bytes differ {rel}")
    if args.run_tests:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "tests.test_agency_registry", "-v"],
            cwd=args.repos[0], capture_output=True, text=True, timeout=120,
            env={"SAFETYREPORT_DATA_DIR": "/tmp", "PATH": "/usr/bin:/bin"})
        # unittest -v reports to stderr; show the tail either way.
        print((proc.stderr or proc.stdout)[-1500:])
        if proc.returncode != 0:
            problems.append("PC vector tests failed (see output above)")

    if problems:
        print("registry check FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"registry check ok: {len(files)} files identical across "
          f"{', '.join(r.name for r in args.repos)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
