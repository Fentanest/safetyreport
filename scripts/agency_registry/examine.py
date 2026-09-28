#!/usr/bin/env python3
"""Inspect the three repositories: paths, remotes, versions, dirty state.

Read-only. Refuses to proceed-remind: never touches user files, never pulls.
Prints one JSON document to stdout for the skill log.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def git(root: Path, *args: str) -> str:
    try:
        out = subprocess.run(["git", "-C", str(root), *args],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() if out.returncode == 0 else f"ERROR:{out.stderr.strip()[:200]}"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"ERROR:{exc}"


def snapshot_version(root: Path) -> str | None:
    manifest = root / "shared" / "agency-region-registry" / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        return json.loads(manifest.read_text(encoding="utf-8")).get("registry_version")
    except ValueError:
        return "UNREADABLE"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repos", type=Path, nargs="+", required=True)
    args = ap.parse_args()
    report = []
    for repo in args.repos:
        report.append({
            "repo": str(repo),
            "exists": repo.is_dir(),
            "remote": git(repo, "remote", "get-url", "origin"),
            "branch": git(repo, "branch", "--show-current"),
            "head": git(repo, "rev-parse", "--short", "HEAD"),
            "dirty": git(repo, "status", "--porcelain"),
            "registry_version": snapshot_version(repo),
        })
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
