#!/usr/bin/env python3
"""Fetch official code snapshots (code.go.kr) into the local cache.

USER-INVOKED ONLY: downloads nothing unless --confirm-download is passed.
Records every attempt (source URL, timestamp, SHA-256, byte size, failures)
in data-sources/official/fetch_log.jsonl. An HTTP 200 with an empty body is
logged as a failure (never treated as a successful download). Binaries stay in
the gitignored cache dir and are never committed; only reviewed extracts enter
data-sources/ via the operator.

Endpoints (public reference data, no credentials):
  org  https://www.code.go.kr/stdcode/orgCodeL.do   (7-char agency codes)
  법정동 https://www.code.go.kr/stdcode/regCodeL.do    (10-char region codes)
  공지  https://www.code.go.kr/bbsmng/dataBbsL.do     (change notices)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE.parent.parent / "shared" / "agency-region-registry" / "data-sources" / "official"

ENDPOINTS = {
    "org_codes": "https://www.code.go.kr/stdcode/orgCodeL.do",
    "region_codes": "https://www.code.go.kr/stdcode/regCodeL.do",
    "notices": "https://www.code.go.kr/bbsmng/dataBbsL.do",
}


def log(entry: dict) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    with (CACHE / "fetch_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--confirm-download", action="store_true",
                    help="Actually download. Without it, only print what would be fetched.")
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()
    for name, url in ENDPOINTS.items():
        print(f"{name}: {url}")
        if not args.confirm_download:
            continue
        entry: dict = {"name": name, "url": url,
                       "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        try:
            with urllib.request.urlopen(url, timeout=args.timeout) as res:
                body = res.read()
            entry["status"] = res.status
            entry["bytes"] = len(body)
            entry["sha256"] = hashlib.sha256(body).hexdigest()
            if res.status == 200 and not body:
                entry["result"] = "empty_response_is_failure"
                print(f"  -> HTTP 200 empty body: recorded as FAILURE (not a download)")
            else:
                target = CACHE / f"{name}.bin"
                target.write_bytes(body)
                entry["result"] = "cached"
                entry["path"] = str(target)
                print(f"  -> {entry['result']} {entry['bytes']} bytes sha256={entry['sha256'][:16]}…")
        except Exception as exc:  # noqa: BLE001 - record any fetch failure
            entry["result"] = "failed"
            entry["error"] = str(exc)[:300]
            print(f"  -> failed: {entry['error']}")
        log(entry)
    if not args.confirm_download:
        print("Dry-run only: nothing downloaded. Re-run with --confirm-download to fetch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
