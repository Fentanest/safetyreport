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
        index_blob = json.loads((root / "data" / "agency_index.json").read_text(encoding="utf-8"))
        legacy = json.loads((root / "data" / "agency_legacy.json").read_text(encoding="utf-8"))
        institutions = json.loads((root / "data" / "agency_institutions.json").read_text(encoding="utf-8"))["institutions"]
    except (OSError, ValueError, KeyError) as exc:
        print(f"data unreadable: {exc}")
        return 1
    if manifest.get("schema_version") != 2:
        fail(f"schema_version must be 2, got {manifest.get('schema_version')}", problems)

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
        for key in ("from_code", "to_code", "effective_date", "institution_id"):
            if key not in link:
                fail(f"agency link missing {key}: {link}", problems)
        if "evidence" not in link and "rule" not in link:
            fail(f"agency link missing evidence/rule: {link.get('from_code')}", problems)
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

    # v2 index/legacy/institutions coherence
    cols = index_blob.get("cols")
    rows = index_blob.get("rows", [])
    if cols != ["code", "name", "agg", "type", "created", "lookup_name"]:
        fail(f"agency_index cols wrong: {cols}", problems)
    if any(len(r) != len(cols) for r in rows):
        fail("agency_index row width wrong", problems)
    codes = [r[0] for r in rows]
    if codes != sorted(codes):
        fail("agency_index rows not sorted by code", problems)
    if len(set(codes)) != len(codes):
        fail("agency_index duplicate codes", problems)
    index_map = {r[0]: r for r in rows}
    boundaries = set()
    for code, name, agg, _type, _created, lookup_name in rows:
        if len(code) != 7:
            fail(f"index code not 7 chars: {code}", problems)
        if agg not in index_map and agg != code:
            # agg must be a boundary row of the same index... unless the
            # boundary itself is abolished (child of a recoded agency).
            # Those resolve via legacy.forward; flag only when truly dangling.
            fail(f"index agg dangling (not a boundary row): {code} -> {agg}", problems)
        if agg == code:
            boundaries.add(code)
            if not name:
                fail(f"boundary without name: {code}", problems)
            if lookup_name is not None and (not lookup_name or lookup_name == name):
                fail(f"redundant/empty lookup_name: {code}", problems)
        elif lookup_name is not None:
            fail(f"child with lookup_name: {code}", problems)
    forward, multi = legacy.get("forward", {}), legacy.get("multi", {})
    if set(forward) & set(multi):
        fail("legacy forward/multi overlap", problems)
    for old, target in forward.items():
        if target not in boundaries and target not in institutions:
            fail(f"legacy forward target unknown: {old} -> {target}", problems)
    for old in multi:
        if old in index_map:
            fail(f"legacy multi key also in index: {old}", problems)
    for code in boundaries:
        if code not in institutions:
            fail(f"boundary without institution: {code}", problems)
    for link in links:
        for side in ("from_code", "to_code"):
            if link[side] not in institutions:
                fail(f"link endpoint without institution: {link[side]}", problems)
        inst = institutions.get(link["from_code"])
        if inst is not None and link.get("institution_id") != inst and \
                link.get("institution_id") != institutions.get(link["to_code"]):
            fail(f"link institution mismatch: {link['from_code']}->{link['to_code']}", problems)

    if problems:
        print("registry validation FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"registry ok: {len(events)} region events, {len(links)} agency links, "
          f"{len(rows)} index rows, {len(forward)} forwards, {len(multi)} multis, "
          f"{len(vectors)} vectors, registry {manifest.get('registry_version')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
