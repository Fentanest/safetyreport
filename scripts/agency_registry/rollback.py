#!/usr/bin/env python3
"""Restore the pre-sync snapshot backup (<dst>/shared/.agency-region-registry.prev).

Re-run sync afterwards to re-verify bytes. Keeps the failed tree aside as
.agency-region-registry.failed-<timestamp> instead of deleting it.
"""
from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, required=True)
    args = ap.parse_args()
    shared = args.repo / "shared"
    dst = shared / "agency-region-registry"
    prev = shared / ".agency-region-registry.prev"
    if not prev.is_dir():
        print(f"no backup at {prev}")
        return 1
    if dst.exists():
        failed = shared / f".agency-region-registry.failed-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
        shutil.move(str(dst), str(failed))
        print(f"moved current tree aside: {failed}")
    shutil.move(str(prev), str(dst))
    print(f"restored backup -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
