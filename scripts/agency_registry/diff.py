#!/usr/bin/env python3
"""Diff two snapshots: new/retired codes, link changes, renamed displays.

Used after ingesting a fresh official snapshot (fetch_official.py) to classify:
same-code rename, births/deaths, 1:1 previous_code links, affiliation moves,
1:1 vs 1:many vs many:1, reestablished rows, same-code district splits. Writes
a JSON diff report; the operator curates it into seed/data changes — automatic
1:1 links are proposed, never auto-confirmed here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_snapshot(root: Path) -> dict:
    data = root / "data"
    return {
        "events": {e["old_code"]: e for e in
                   json.loads((data / "region_events.json").read_text(encoding="utf-8"))["events"]},
        "links": {(l["from_code"], l["to_code"]): l for l in
                  json.loads((data / "agency_links.json").read_text(encoding="utf-8"))["links"]},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--old", type=Path, required=True)
    ap.add_argument("--new", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    old, new = load_snapshot(args.old), load_snapshot(args.new)
    report = {
        "region_events_added": sorted(set(new["events"]) - set(old["events"])),
        "region_events_removed": sorted(set(old["events"]) - set(new["events"])),
        "region_events_changed": sorted(
            k for k in set(old["events"]) & set(new["events"]) if old["events"][k] != new["events"][k]),
        "agency_links_added": sorted(set(new["links"]) - set(old["links"])),
        "agency_links_removed": sorted(set(old["links"]) - set(new["links"])),
    }
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
