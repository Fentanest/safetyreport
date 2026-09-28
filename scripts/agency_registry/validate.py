#!/usr/bin/env python3
"""Validate a built registry snapshot (shared/agency-region-registry).

Checks: manifest JSON parses, files[] hashes match current bytes, data files
parse, every region event has the typed schema, agency links are 1:1
(no from_code with 0 or 2+ successors, no cycles, no self loops), vectors
parse with the required keys. Exits non-zero on any problem; a damaged file
or cycle blocks a new rollout (the skill refuses to sync).
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

RELATIONS = {"rename", "rename_under_merge", "transfer", "merge", "split", "merge_parent", "reestablished"}


def fail(message: str, problems: list[str]) -> None:
    problems.append(message)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--snapshot", type=Path, required=True)
    args = ap.parse_args()
    root: Path = args.snapshot
    problems: list[str] = []

    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"manifest unreadable: {exc}")
        return 1
    for key in ("schema_version", "registry_version", "as_of_date", "generated_at", "files"):
        if key not in manifest:
            fail(f"manifest missing key: {key}", problems)
    for rel, digest in manifest.get("files", {}).items():
        target = root / rel
        if not target.is_file():
            fail(f"manifest lists missing file: {rel}", problems)
        elif hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            fail(f"hash mismatch: {rel}", problems)

    try:
        events = json.loads((root / "data" / "region_events.json").read_text(encoding="utf-8"))["events"]
        links = json.loads((root / "data" / "agency_links.json").read_text(encoding="utf-8"))["links"]
        vectors = json.loads((root / "vectors" / "resolve_cases.json").read_text(encoding="utf-8"))["cases"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"data unreadable: {exc}")
        return 1

    for event in events:
        for key in ("event_id", "effective_date", "old_code", "old_name", "new_codes", "relation"):
            if key not in event:
                fail(f"region event missing {key}: {event.get('event_id')}", problems)
        if event.get("relation") not in RELATIONS:
            fail(f"unknown relation: {event.get('relation')}", problems)
        if event.get("relation") in ("rename", "rename_under_merge", "transfer", "merge", "merge_parent") \
                and len(event.get("new_codes", [])) != 1:
            fail(f"{event.get('relation')} must have exactly one successor: {event.get('old_code')}", problems)

    by_from: dict[str, list] = {}
    for link in links:
        for key in ("from_code", "to_code", "effective_date", "institution_id", "evidence"):
            if key not in link:
                fail(f"agency link missing {key}: {link}", problems)
        by_from.setdefault(link.get("from_code", ""), []).append(link)
        if link.get("from_code") == link.get("to_code"):
            fail(f"self loop: {link.get('from_code')}", problems)
    for from_code, outgoing in by_from.items():
        if len(outgoing) != 1:
            fail(f"from_code has {len(outgoing)} successors (must be 1:1): {from_code}", problems)
    # cycle guard along 1:1 chains
    for start in by_from:
        seen = {start}
        node = by_from[start][0]["to_code"]
        while node in by_from:
            if node in seen:
                fail(f"agency link cycle at: {node}", problems)
                break
            seen.add(node)
            node = by_from[node][0]["to_code"]

    if not vectors:
        fail("no resolve_cases vectors", problems)
    for case in vectors:
        if set(case.keys()) != {"name", "kind", "input", "expected"}:
            fail(f"vector keys wrong: {case.get('name')}", problems)
        if case.get("kind") not in ("agency", "region"):
            fail(f"vector kind wrong: {case.get('name')}", problems)

    if problems:
        print("registry validation FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"registry ok: {len(events)} region events, {len(links)} agency links, "
          f"{len(vectors)} vectors, registry {manifest.get('registry_version')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
