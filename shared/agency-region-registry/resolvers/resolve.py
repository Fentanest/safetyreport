"""Agency/region registry resolver (canonical implementation).

Reads the runtime snapshot (data/agency_index.json, data/agency_legacy.json,
data/agency_links.json, data/region_events.json, manifest.json) and resolves
source values to current display/stat keys.

Rules (from the handoff references, enforced here — never string-replace or
name-hash institutions):
- 집계기관(agg) 경계는 빌드 시점에 확정한다. 런타임은 index 조희만 한다.
  대표기관코드를 통계 키로 쓰지 않는다(여러 경찰서가 경찰청으로 합쳐지지 않음).
- Agency identity follows verified 1:1 links only (previous_code chains).
  A code resolves whether it arrives as the pre-change code OR the post-change
  code (unique backward walk). Empty previous_code never implies succession;
  unknown codes stay unresolved. Multi-successor branches never merge: the old
  code keeps a '(구)' historical display and its own src row.
- Same-code renames resolve by code: the index holds the current name, so an
  old answer name with the same code shows the current name (one stat key).
- Code-less answers resolve ONLY on an exact full-name match that is unique
  across the snapshot for the answered era (code_derived=true; the derived code
  never rewrites the stored source code). Ambiguous or unknown names stay
  unresolved on their legacy name grouping.
- Region lineage follows typed events only. old_region_closed (code-table date)
  and effective_date (event date) are never forced equal.
- '(구)' is a display suffix for known historical nodes only. Unresolved rows
  keep their source text verbatim and never gain the suffix (no '(구)(구)').
- answered_at (the answer's date) and the registry as_of date are different
  inputs: the former interprets the past, the latter renders the present.

Ports (resolvers/resolve.dart, resolvers/resolve.ts) must return equal results
for every case in vectors/resolve_cases.json.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent

SEVEN_ALNUM = frozenset("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


def _is_seven_alnum(code: str | None) -> bool:
    return isinstance(code, str) and len(code) == 7 and all(c in SEVEN_ALNUM for c in code)


def _date_or_max(value: str | None) -> str:
    return value or "9999-12-31"


def _compact8(value: str | None) -> str:
    return (value or "").replace("-", "")[:8]


@dataclass
class Snapshot:
    events_by_old: dict
    links_by_from: dict
    links_by_to: dict
    registry_version: str
    as_of_date: str | None = None
    # code -> [name|None, agg, type|None, created8|None]
    index: dict = field(default_factory=dict)
    compact: dict = field(default_factory=dict)
    forward: dict = field(default_factory=dict)
    multi: dict = field(default_factory=dict)
    institutions: dict = field(default_factory=dict)
    _alias_all: dict | None = None

    @classmethod
    def load(cls, root: Path = SNAPSHOT_DIR) -> "Snapshot":
        data = root / "data"
        events = json.loads((data / "region_events.json").read_text(encoding="utf-8"))["events"]
        links = json.loads((data / "agency_links.json").read_text(encoding="utf-8"))["links"]
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        index_blob = json.loads((data / "agency_index.json").read_text(encoding="utf-8"))
        legacy = json.loads((data / "agency_legacy.json").read_text(encoding="utf-8"))
        institutions = json.loads((data / "agency_institutions.json").read_text(encoding="utf-8"))["institutions"]
        cols = index_blob["cols"]
        index = {row[0]: dict(zip(cols[1:], row[1:])) for row in index_blob["rows"]}
        compact = {code: agg for code, agg in index_blob.get("compact_rows", [])}
        by_old: dict[str, dict] = {}
        for event in events:
            by_old.setdefault(event["old_code"], event)
        by_from: dict[str, list[dict]] = {}
        by_to: dict[str, list[dict]] = {}
        for link in links:
            by_from.setdefault(link["from_code"], []).append(link)
            by_to.setdefault(link["to_code"], []).append(link)
        return cls(events_by_old=by_old, links_by_from=by_from, links_by_to=by_to,
                   registry_version=manifest["registry_version"],
                   as_of_date=manifest.get("as_of_date"),
                   index=index, compact=compact, forward=legacy.get("forward", {}),
                   multi=legacy.get("multi", {}), institutions=institutions)

    def boundary_name(self, code: str) -> str | None:
        row = self.index.get(code)
        name = (row or {}).get("name")
        return name or None

    def alias_all(self, name: str) -> list[str]:
        """표시명 또는 공식 전체기관명을 가진 모든 코드. 호출자가 시대로 거른다.

        8만 행 스캔을 매 신고마다 반복하지 않기 위한 스냅샷별 캐시다.
        """
        if self._alias_all is None:
            table: dict[str, list[str]] = {}
            for code, row in self.index.items():
                for key in (row.get("name"), row.get("lookup_name")):
                    if key:
                        codes = table.setdefault(key, [])
                        if code not in codes:
                            codes.append(code)
            for old, old_name in self.multi.items():
                table.setdefault(old_name, [])
                if old not in table[old_name]:
                    table[old_name].append(old)
            self._alias_all = table
        return self._alias_all.get(name, [])


def _walk_chain(start: str, snap: Snapshot) -> list[dict]:
    """Unique forward chain, else unique backward walk (both sides, one inst)."""
    chain: list[dict] = []
    seen = {start}
    while True:
        outgoing = snap.links_by_from.get(chain[-1]["to_code"] if chain else start, [])
        if len(outgoing) != 1:
            break
        nxt = outgoing[0]
        if nxt["to_code"] in seen:
            break
        chain.append(nxt)
        seen.add(nxt["to_code"])
    if not chain:
        back: list[dict] = []
        cursor = start
        while True:
            incoming = snap.links_by_to.get(cursor, [])
            if len(incoming) != 1:
                break
            link = incoming[0]
            if link["from_code"] in seen:
                break
            back.append(link)
            seen.add(link["from_code"])
            cursor = link["from_code"]
        chain = back[::-1]
    return chain


def _resolve_boundary(boundary: str, name: str | None, answered_at: str | None,
                      snap: Snapshot, as_was_code: str | None = None) -> dict:
    chain = _walk_chain(boundary, snap)
    institution_id = snap.institutions.get(boundary)
    if not chain:
        current_name = snap.boundary_name(boundary) or name
        if institution_id is None:
            institution_id = f"ag-c{boundary.lower()}"
        return {
            "institution_id": institution_id,
            "agency_stat_key": f"inst:{institution_id}",
            "current_agency_code": boundary,
            "current_agency_name": current_name,
            "resolution_status": "resolved",
            "registry_version": snap.registry_version,
        }
    if institution_id is None:
        institution_id = chain[0].get("institution_id") or f"ag-c{boundary.lower()}"
    applied = [link for link in chain if (answered_at or "9999-12-31") >= link["effective_date"]]
    if len(applied) == len(chain):
        head = chain[-1]["to_code"]
        current_name = snap.boundary_name(head) or chain[-1].get("to_name") or name
        return {
            "institution_id": institution_id,
            "agency_stat_key": f"inst:{institution_id}",
            "current_agency_code": head,
            "current_agency_name": current_name,
            "resolution_status": "resolved",
            "registry_version": snap.registry_version,
        }
    anchor = applied[-1] if applied else None
    if anchor is None:
        # Answered before the first verified change: the as-was name is what the
        # answer itself carried (never invent a past name from the snapshot),
        # and the as-was code is the input code (never the forwarded head).
        fallback = as_was_code if _is_seven_alnum(as_was_code) else None
        return {
            "institution_id": institution_id,
            "agency_stat_key": f"inst:{institution_id}",
            "current_agency_code": fallback,
            "current_agency_name": name,
            "resolution_status": "resolved_as_of_date",
            "registry_version": snap.registry_version,
        }
    return {
        "institution_id": institution_id,
        "agency_stat_key": f"inst:{institution_id}",
        "current_agency_code": anchor["to_code"],
        "current_agency_name": anchor.get("to_name") or name,
        "resolution_status": "resolved_as_of_date",
        "registry_version": snap.registry_version,
    }


def _alias_candidates(name: str, answered_at: str | None, snap: Snapshot) -> list[str]:
    """Exact full-name matches unique for the answered era (codes only)."""
    ans8 = _compact8(answered_at) if answered_at else None
    found: list[str] = []
    for code in snap.alias_all(name):
        row = snap.index.get(code)
        created = (row or {}).get("created") or "" if row else ""
        if ans8 and created and created > ans8:
            continue
        found.append(code)
    return found


def resolve_current_agency(code: str | None, name: str | None, snap: Snapshot) -> dict:
    """현행 표시용: registry as_of_date 기준으로 체인 전체를 적용한다."""
    return resolve_agency(code, name, snap.as_of_date or "9999-12-31", snap)


def resolve_agency(code: str | None, name: str | None, answered_at: str | None, snap: Snapshot) -> dict:
    """Resolve a source agency (code/name from one official answer).

    institution_id is stable across verified renames; current_* renders the
    registry present (or the as-was value when answered before the change).
    """
    name = (name or "").strip() or None
    if _is_seven_alnum(code):
        assert isinstance(code, str)
        row = snap.index.get(code)
        if row is not None:
            return _resolve_boundary(row["agg"], name, answered_at, snap, as_was_code=code)
        compact_agg = snap.compact.get(code)
        if compact_agg is not None:
            return _resolve_boundary(compact_agg, name, answered_at, snap, as_was_code=code)
        target = snap.forward.get(code)
        if target is not None:
            got = _resolve_boundary(target, name, answered_at, snap, as_was_code=code)
            return got
        if code in snap.multi:
            display = f"(구){name}" if name else f"(구){snap.multi[code]}"
            return {
                "institution_id": None,
                "agency_stat_key": f"src:{code}:{name or '-'}",
                "current_agency_code": None,
                "current_agency_name": display,
                "resolution_status": "historical",
                "registry_version": snap.registry_version,
            }
        return {
            "institution_id": None,
            "agency_stat_key": f"src:{code}:{name or '-'}",
            "current_agency_code": None,
            "current_agency_name": name,
            "resolution_status": "unresolved",
            "registry_version": snap.registry_version,
        }
    # 코드 없음: 전체기관명 정확 일치 + 해당 시점 유일 후보만 파생한다.
    if name:
        candidates = _alias_candidates(name, answered_at, snap)
        if len(candidates) == 1:
            got = resolve_agency(candidates[0], name, answered_at, snap)
            got = dict(got)
            got["code_derived"] = True
            return got
    return {
        "institution_id": None,
        "agency_stat_key": f"src:{code or '-'}:{name or '-'}",
        "current_agency_code": None,
        "current_agency_name": name,
        "resolution_status": "unresolved",
        "registry_version": snap.registry_version,
    }


def resolve_region(code: str | None, date: str | None, snap: Snapshot) -> dict:
    """Resolve a region code at a date (answer date for facts, as_of for display)."""
    code = (code or "").strip() or None
    event = snap.events_by_old.get(code) if code else None
    if event is None:
        return {"status": "unresolved", "current_codes": [],
                "display": None, "relation": None,
                "registry_version": snap.registry_version}
    if (date or "9999-12-31") < event["effective_date"]:
        return {"status": "current_then", "current_codes": [code],
                "display": event["old_name"], "relation": event["relation"],
                "registry_version": snap.registry_version}
    relation = event["relation"]
    if relation in ("rename", "rename_under_merge", "transfer", "merge"):
        return {"status": "resolved", "current_codes": list(event["new_codes"]),
                "display": event["new_names"][0] if event["new_names"] else None,
                "relation": relation, "registry_version": snap.registry_version}
    if relation == "split":
        return {"status": "historical", "current_codes": list(event["new_codes"]),
                "display": f"(구){event['old_name']}", "relation": relation,
                "aggregate_note": "successors are not assigned; aggregate once under the common parent",
                "registry_version": snap.registry_version}
    if relation == "merge_parent":
        return {"status": "historical", "current_codes": list(event["new_codes"]),
                "display": f"(구){event['old_name']}", "relation": relation,
                "aggregate_note": "kept as a history row under the current parent total",
                "registry_version": snap.registry_version}
    if relation == "reestablished":
        return {"status": "resolved", "current_codes": list(event["new_codes"]),
                "display": event["new_names"][0] if event["new_names"] else None,
                "relation": relation,
                "aggregate_note": "region lineage only; institution continuity is decided separately",
                "registry_version": snap.registry_version}
    return {"status": "unresolved", "current_codes": [],
            "display": None, "relation": relation,
            "registry_version": snap.registry_version}


def resolve_region_gap(code: str | None, date: str | None, snap: Snapshot) -> dict:
    """Dates between old_closed and effective_date (e.g. Bucheon 2016-2023): no current district."""
    result = resolve_region(code, date, snap)
    event = snap.events_by_old.get((code or "").strip()) if code else None
    if (event is not None and event["relation"] == "reestablished" and event["old_closed"]
            and event["old_closed"] <= (date or "9999-12-31") < event["effective_date"]):
        return {"status": "gap", "current_codes": [],
                "display": f"(구){event['old_name']}", "relation": "reestablished",
                "aggregate_note": "no district existed then; never back-allocate to the reestablished districts",
                "registry_version": snap.registry_version}
    return result


def display_agency(source_name: str | None, resolution: dict) -> str | None:
    status = resolution.get("resolution_status")
    if status in ("resolved", "resolved_as_of_date", "historical"):
        return resolution.get("current_agency_name")
    return (source_name or "").strip() or None


def display_region(source_name: str | None, resolution: dict) -> str | None:
    if resolution.get("status") in ("resolved", "current_then"):
        return resolution.get("display")
    if resolution.get("status") in ("historical", "gap"):
        return resolution.get("display")
    return (source_name or "").strip() or None
