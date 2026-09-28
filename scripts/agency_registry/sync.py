#!/usr/bin/env python3
"""Sync the canonical snapshot to the three repositories and verify bytes.

Copies shared/agency-region-registry (this repo) into the mobile and map
worktrees (or any --to roots), then checks every tracked file (manifest
files[] + manifest/provenance/schema/resolvers/vectors) is byte-identical.
A mismatch fails loudly instead of half-syncing. Run validate.py first.

Rollback: sync keeps the previous tree in <dst>/shared/.agency-region-registry.prev
before replacing; rollback.py restores it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "shared" / "agency-region-registry"
PREV_SUFFIX = ".agency-region-registry.prev"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tracked_files(root: Path) -> list[str]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    return list(dict.fromkeys(["manifest.json", "provenance.json", "schema.md",
                               *manifest["files"].keys()]))


def sync_one(dst_root: Path, dry_run: bool) -> list[str]:
    problems: list[str] = []
    dst = dst_root / "shared" / "agency-region-registry"
    files = tracked_files(SRC)
    for rel in files:
        src_file = SRC / rel
        if not src_file.is_file():
            problems.append(f"canonical missing: {rel}")
    if problems:
        return problems
    if dry_run:
        missing = [rel for rel in files if not (dst / rel).is_file()]
        different = [rel for rel in files
                     if (dst / rel).is_file() and sha256_file(dst / rel) != sha256_file(SRC / rel)]
        print(f"dry-run {dst_root}: missing={len(missing)} different={len(different)}")
        for rel in [*missing, *different]:
            print(f"  - {rel}")
        return []
    prev = dst.parent / PREV_SUFFIX
    if dst.exists():
        if prev.exists():
            shutil.rmtree(prev)
        shutil.copytree(dst, prev)
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    for rel in files:
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(SRC / rel, target)
    for rel in files:
        if sha256_file(dst / rel) != sha256_file(SRC / rel):
            problems.append(f"copy mismatch: {rel}")
    extra = {str(p.relative_to(dst)) for p in dst.rglob("*") if p.is_file()} - set(files)
    problems += [f"unlisted {e}" for e in sorted(extra)]
    print(f"synced {dst_root}: {len(files)} files identical" if not problems else f"sync FAILED {dst_root}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--to", type=Path, action="append", required=True, help="Repository root(s)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    problems: list[str] = []
    for dst_root in args.to:
        problems += sync_one(dst_root, args.dry_run)
    for problem in problems:
        print(f"  - {problem}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
