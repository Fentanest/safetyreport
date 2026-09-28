#!/usr/bin/env python3
"""Build shared/agency-region-registry runtime snapshot from reviewed sources.

Deterministic: same inputs always produce byte-identical outputs (sorted keys,
compact separators, UTF-8, generated_at derived from input mtimes).
Re-running with no input change is a no-op — the sync step relies on this to
avoid useless version bumps.

Inputs (reviewed, tracked):
  data-sources/administrative-region-lineage-2014.csv   (handoff copy, read-only)
  data-sources/administrative-region-notices-2014.csv   (handoff copy, read-only)
  <script-dir>/seed_agency_links.json                  (curated verified links)

Outputs (tracked runtime snapshot):
  data/region_events.json      typed region lineage events (relation/handling are
                               data, never parsed at runtime)
  data/agency_links.json       verified 1:1 agency succession links only
  manifest.json                schema/registry versions, dates, hashes, readers
  provenance.json              input hashes, builder version, evidence pointers

Official full snapshots (code.go.kr) are NOT inputs here: see fetch_official.py.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

BUILDER_VERSION = "agency-registry-build/2026-09-28.1"
SCHEMA_VERSION = 1

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE.parent.parent / "shared" / "agency-region-registry"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canon(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def ymd(raw: str) -> str | None:
    raw = (raw or "").strip()
    if not raw:
        return None
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def split_codes(raw: str) -> list[str]:
    return [c.strip() for c in (raw or "").split(";") if c.strip()]


def build_region_events(lineage_csv: Path) -> list[dict]:
    events = []
    with lineage_csv.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            events.append({
                "event_id": row["event"].strip(),
                "effective_date": ymd(row["effective_date"]),
                "old_code": row["old_region_code"].strip(),
                "old_name": row["old_region_name"].strip(),
                # old_region_closed is the code-table abolition date, NOT the
                # event effective date — never force them equal (handoff rule).
                "old_closed": ymd(row["old_region_closed"]),
                "new_codes": split_codes(row["new_region_codes"]),
                "new_names": split_codes(row["new_region_names"]),
                "relation": row["relation"].strip(),
                # handling text is preserved as a human note; runtime uses
                # the typed `relation` + resolvers/README, never parses this.
                "handling_note": row["handling"].strip(),
            })
    events.sort(key=lambda e: (e["effective_date"] or "", e["old_code"]))
    return events


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--references-dir", type=Path, required=True,
                    help="Directory holding the handoff reference CSVs (originals stay untouched)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--registry-version", default="2026-09-28.1")
    ap.add_argument("--as-of-date", default="2026-09-28")
    args = ap.parse_args()

    out: Path = args.out
    data_sources = out / "data-sources"
    data = out / "data"
    data_sources.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)

    lineage_src = args.references_dir / "administrative-region-lineage-2014.csv"
    notices_src = args.references_dir / "administrative-region-notices-2014.csv"
    seed_src = HERE / "seed_agency_links.json"
    for path in (lineage_src, notices_src, seed_src):
        if not path.is_file():
            print(f"missing input: {path}", flush=True)
            return 1

    # data-sources: byte-identical copies of the reviewed inputs (never edited).
    lineage_cp = data_sources / lineage_src.name
    notices_cp = data_sources / notices_src.name
    lineage_cp.write_bytes(lineage_src.read_bytes())
    notices_cp.write_bytes(notices_src.read_bytes())

    events = build_region_events(lineage_cp)
    (data / "region_events.json").write_text(canon({"events": events}) + "\n", encoding="utf-8")

    seed = json.loads(seed_src.read_text(encoding="utf-8"))
    links = sorted(seed["links"], key=lambda l: (l["from_code"], l["to_code"]))
    (data / "agency_links.json").write_text(canon({"links": links}) + "\n", encoding="utf-8")

    generated_at = datetime.fromtimestamp(
        max(p.stat().st_mtime for p in (lineage_src, notices_src, seed_src)),
        tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    # provenance FIRST: the manifest records the hash of these exact bytes, so the
    # file must exist before the manifest hashes it (REVIEW2 중간-4: changed inputs
    # used to fail validate.py with a stale provenance hash).
    # provenance.json is therefore listed in the manifest files[] below and verified.
    provenance = {
        "builder": BUILDER_VERSION,
        "inputs": [
            {"path": "agency-registry-handoff/references/administrative-region-lineage-2014.csv",
             "sha256": sha256_file(lineage_src), "role": "region lineage survey (74 abolished codes, si-do/si-gun-gu)"},
            {"path": "agency-registry-handoff/references/administrative-region-notices-2014.csv",
             "sha256": sha256_file(notices_src), "role": "official change notices (evidence pointers, not lineage)"},
            {"path": "agency-registry-handoff/references/administrative-region-lineage-2014.md",
             "sha256": sha256_file(args.references_dir / "administrative-region-lineage-2014.md"),
             "role": "survey method, event table, non-auto rules"},
            {"path": "agency-registry-handoff/references/agency-lineage-registry.md",
             "sha256": sha256_file(args.references_dir / "agency-lineage-registry.md"),
             "role": "agency 1:1 evidence (Gwangju Dongbu police), non-link cases, ID rules"},
            {"path": "scripts/agency_registry/seed_agency_links.json",
             "sha256": sha256_file(seed_src), "role": "curated verified agency links (reviewed source)"},
        ],
        "limits": [
            "Region CSV covers abolished si-do/si-gun-gu codes since 2014 (74 rows), not every era/dong/agency.",
            "Notices CSV rows are NOT 1:1 with lineage rows; links between events and notices are by evidence text.",
            "Agency seed holds verified 1:1 links only; empty previous_code never implies succession.",
            "Full official snapshots (code.go.kr) are fetched separately via fetch_official.py, not bundled here.",
        ],
    }
    (out / "provenance.json").write_text(canon(provenance) + "\n", encoding="utf-8")
    tracked = ["provenance.json", "schema.md",
               "data/region_events.json", "data/agency_links.json",
               "data-sources/administrative-region-lineage-2014.csv",
               "data-sources/administrative-region-notices-2014.csv",
               "resolvers/resolve.py", "resolvers/resolve.dart", "resolvers/resolve.ts",
               "resolvers/README.md", "vectors/resolve_cases.json"]
    # manifest.json records the snapshot but not itself (self-hash cannot match).
    files = {}
    for rel in tracked:
        target = out / rel
        # resolvers/vectors/schema may not exist yet on the first scaffolding run;
        # the manifest records only what exists (validate.py enforces completeness).
        if target.is_file():
            files[rel] = sha256_file(target)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "registry_version": args.registry_version,
        "as_of_date": args.as_of_date,
        "generated_at": generated_at,
        "builder": BUILDER_VERSION,
        "source_hashes": {
            "administrative-region-lineage-2014.csv": sha256_file(lineage_cp),
            "administrative-region-notices-2014.csv": sha256_file(notices_cp),
            "seed_agency_links.json": sha256_file(seed_src),
        },
        "files": files,
        "readers": ["resolve.py", "resolve.dart", "resolve.ts"],
        "contract": "shared/agency-region-registry (same bytes in safetyreport, safetyreport-mobile, safetyreport-community-map)",
    }
    (out / "manifest.json").write_text(canon(manifest) + "\n", encoding="utf-8")
    print(f"built registry {args.registry_version}: {len(events)} region events, {len(links)} agency links -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
