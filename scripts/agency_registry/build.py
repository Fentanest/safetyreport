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
  --official-zip: code.go.kr '기관코드 전체자료' zip (로컬 파일만 사용, Git 미커밋.
    SHA-256·취득시각·행 수만 provenance에 기록한다. 외부 다운로드는 하지 않음)

Outputs (tracked runtime snapshot):
  data/region_events.json      typed region lineage events (relation/handling are
                               data, never parsed at runtime)
  data/agency_links.json       verified 1:1 agency succession links (seed + derived)
  data/agency_index.json       현존·폐지 경계 rows + 폐지 하위조직 compact_rows [code,agg]
  data/agency_legacy.json      폐지 코드 {forward,multi}
  manifest.json                schema/registry versions, dates, hashes, readers
  provenance.json              input hashes, builder version, evidence pointers
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BUILDER_VERSION = "agency-registry-build/2026-09-29.3"
SCHEMA_VERSION = 2

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE.parent.parent / "shared" / "agency-region-registry"

sys.path.insert(0, str(HERE))
import build_official_index as official  # noqa: E402


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
    ap.add_argument("--official-zip", type=Path, required=True,
                    help="Local code.go.kr '기관코드 전체자료' zip (already downloaded; never fetched here, never committed)")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--registry-version", default="2026-09-29.3")
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
    if not args.official_zip.is_file():
        print(f"missing official zip: {args.official_zip} (local file only; "
              f"fetch_official.py --confirm-download is user-invoked)", flush=True)
        return 1

    # data-sources: byte-identical copies of the reviewed inputs (never edited).
    lineage_cp = data_sources / lineage_src.name
    notices_cp = data_sources / notices_src.name
    lineage_cp.write_bytes(lineage_src.read_bytes())
    notices_cp.write_bytes(notices_src.read_bytes())

    events = build_region_events(lineage_cp)
    (data / "region_events.json").write_text(canon({"events": events}) + "\n", encoding="utf-8")

    seed = json.loads(seed_src.read_text(encoding="utf-8"))
    seed_links = sorted(seed["links"], key=lambda l: (l["from_code"], l["to_code"]))

    # --- official agency index stage (v2) ---
    official_rows, zip_sha, inner_sha = official.load_official(args.official_zip)
    by_code = {r["code"]: r for r in official_rows}
    for link in seed_links:
        for side in ("from_code", "to_code"):
            if link[side] not in by_code:
                print(f"warning: seed {side} {link[side]} not in official snapshot", flush=True)
    derived = official.build_derived(by_code)
    seed_pairs = {(l["from_code"], l["to_code"]) for l in seed_links}
    kept_pairs = {k: v for k, v in derived["link_pairs"].items() if k not in seed_pairs}
    inst_map = official.institution_map(kept_pairs, seed_links, derived, by_code)
    links = []
    for l in seed_links:
        entry = dict(l)
        # seed 원본은 그대로 두고, 출력 링크의 표시명에만 표시 규칙을 적용한다.
        if entry.get("to_name"):
            entry["to_name"] = official.display_agency_name(entry["to_name"])
        links.append(entry)
    for (frm, to) in sorted(kept_pairs):
        entry = dict(kept_pairs[(frm, to)])
        entry["institution_id"] = inst_map.get(frm, inst_map.get(to, f"ag-c{frm.lower()}"))
        links.append(entry)
    links.sort(key=lambda l: (l["from_code"], l["to_code"]))
    (data / "agency_links.json").write_text(canon({"links": links}) + "\n", encoding="utf-8")
    (data / "agency_index.json").write_text(canon({
        "cols": ["code", "name", "agg", "type", "created", "lookup_name"],
        "rows": derived["index_rows"],
        "compact_rows": derived["compact_rows"]}) + "\n", encoding="utf-8")
    (data / "agency_legacy.json").write_text(canon({
        "forward": derived["forward"], "multi": derived["multi"]}) + "\n", encoding="utf-8")
    (data / "agency_institutions.json").write_text(canon({
        "institutions": inst_map}) + "\n", encoding="utf-8")

    zip_mtime = datetime.fromtimestamp(os.stat(args.official_zip).st_mtime,
                                       tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    generated_at = datetime.fromtimestamp(
        max([p.stat().st_mtime for p in (lineage_src, notices_src, seed_src)]
            + [os.stat(args.official_zip).st_mtime]),
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
            {"path": args.official_zip.name,
             "sha256": zip_sha, "role": "code.go.kr 기관코드 전체자료 zip (local only, never committed)",
             "inner_file": official.INNER_NAME, "inner_sha256": inner_sha,
             "acquired_at": zip_mtime, "encoding": "cp949/TAB",
             "official_rows": derived["stats"]["official_rows"]},
        ],
        "official_derivation": {
            "filter": "현존 코드 중 유형분류_대 04/05/06/11-17/18/80 제외 "
                      "(입법·사법·헌법·학교·군·금융 — 안전신문고 답변 기관이 될 수 없는 유형). "
                      "2026-09-29.3부터 2014-01-01 이후 후속 없는(forward/multi 없음) 폐지 비제외 코드도 색인에 둔다: "
                      "하위조직은 답변 당시 소속 집계기관(지자체는 대표기관이 차상위 조상일 때 시도/시군구까지, "
                      "경찰은 경찰서 단위)을 agg로 하고 [code,agg] 압축 행으로 기록, "
                      "집계기관이 개명·1:1 승계됐으면 현행 경계로, 후속 없이 폐지된 집계기관은 "
                      "마지막 알려진 이름의 경계 행과 함께. forward/multi 보유 코드는 기존 귀결 유지.",
            "display": "경계 코드 저장명(현행 표시명)은 공식 전체기관명에서 맨 앞의 "
                       "'경찰청 ' 접두어만 한 번 제거(2026-09-29 사용자 결정). "
                       "공백 경계의 정확한 접두어만 해당: 본청 '경찰청'·'경찰청장…'·"
                       "비경찰 이름은 그대로. 집계(경계 판정)의 이름 경로 비교는 "
                       "공식 원문명으로 하며 multi((구) 역사 표시)는 원문을 둔다. "
                       "lookup_name은 표시명과 다를 때 공식 전체기관명을 보존한다.",
            "stats": derived["stats"],
        },
        "limits": [
            "Region CSV covers abolished si-do/si-gun-gu codes since 2014 (74 rows), not every era/dong/agency.",
            "Notices CSV rows are NOT 1:1 with lineage rows; links between events and notices are by evidence text.",
            "Agency seed holds verified 1:1 links only; empty previous_code never implies succession.",
            "Official snapshot holds one (current) name per code: same-code renames resolve by code, "
            "past names of the same code are not recoverable from the snapshot alone.",
            "Excluded-type and pre-2014 successor-less abolished codes stay unresolved (src rows, never merged).",
        ],
    }
    (out / "provenance.json").write_text(canon(provenance) + "\n", encoding="utf-8")
    tracked = ["provenance.json", "schema.md",
               "data/region_events.json", "data/agency_links.json",
               "data/agency_index.json", "data/agency_legacy.json",
               "data/agency_institutions.json",
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
            "orgcode_full_zip": zip_sha,
            "orgcode_full_inner": inner_sha,
        },
        "files": files,
        "readers": ["resolve.py", "resolve.dart", "resolve.ts"],
        "contract": "shared/agency-region-registry (same bytes in safetyreport, safetyreport-mobile, safetyreport-community-map)",
    }
    (out / "manifest.json").write_text(canon(manifest) + "\n", encoding="utf-8")
    print(f"built registry {args.registry_version}: {len(events)} region events, {len(links)} agency links "
          f"({len(seed_links)} seed + {len(links) - len(seed_links)} derived), "
          f"{len(derived['index_rows'])} index rows + {len(derived['compact_rows'])} compact rows, "
          f"{len(derived['forward'])} legacy forwards, {len(derived['multi'])} multis -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
