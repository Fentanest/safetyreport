"""Agency/region registry resolver (canonical implementation).

Reads the runtime snapshot (data/region_events.json, data/agency_links.json,
manifest.json) and resolves source values to current display/stat keys.

Rules (from the handoff references, enforced here — never string-replace or
name-hash institutions):
- Agency identity follows verified 1:1 links only (previous_code chains).
  Empty previous_code never implies succession; unknown codes stay unresolved.
  Sub-organisation splits and multi-successor branches never merge.
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
from dataclasses import dataclass
from pathlib import Path

SNAPSHOT_DIR = Path(__file__).resolve().parent.parent

SEVEN_ALNUM = frozenset("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")


def _is_seven_alnum(code: str | None) -> bool:
    return isinstance(code, str) and len(code) == 7 and all(c in SEVEN_ALNUM for c in code)


def _date_or_max(value: str | None) -> str:
    return value or "9999-12-31"


@dataclass
class Snapshot:
    events_by_old: dict
    links_by_from: dict
    registry_version: str
    as_of_date: str | None = None

    @classmethod
    def load(cls, root: Path = SNAPSHOT_DIR) -> "Snapshot":
        data = root / "data"
        events = json.loads((data / "region_events.json").read_text(encoding="utf-8"))["events"]
        links = json.loads((data / "agency_links.json").read_text(encoding="utf-8"))["links"]
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        by_old: dict[str, dict] = {}
        for event in events:
            by_old.setdefault(event["old_code"], event)
        by_from: dict[str, list[dict]] = {}
        for link in links:
            by_from.setdefault(link["from_code"], []).append(link)
        return cls(by_old, by_from, manifest["registry_version"],
                   manifest.get("as_of_date"))


def resolve_current_agency(code: str | None, name: str | None, snap: Snapshot) -> dict:
    """현행 표시용: registry as_of_date 기준으로 체인 전체를 적용한다.

    답변일(answered_at)은 과거 식별용이 아니라 현행 표시용이 아니다 — 통계·표시
    그룹 키는 항상 현행명으로 계산하고, 과거명 확인이 필요하면 resolve_agency 의
    answered_at 경로를 직접 쓴다.
    """
    return resolve_agency(code, name, snap.as_of_date or "9999-12-31", snap)


def resolve_agency(code: str | None, name: str | None, answered_at: str | None, snap: Snapshot) -> dict:
    """Resolve a source agency (code/name from one official answer).

    institution_id is stable across verified renames; current_* renders the
    registry present (or the as-was value when answered before the change).
    """
    name = (name or "").strip() or None
    chain: list[dict] = []
    if _is_seven_alnum(code):
        seen = {code}
        while True:
            outgoing = snap.links_by_from.get(chain[-1]["to_code"] if chain else code, [])
            if len(outgoing) != 1:
                break
            nxt = outgoing[0]
            if nxt["to_code"] in seen:
                break
            chain.append(nxt)
            seen.add(nxt["to_code"])
    if not chain:
        return {
            "institution_id": None,
            "agency_stat_key": f"src:{code or '-'}:{name or '-'}",
            "current_agency_code": None,
            "current_agency_name": name,
            "resolution_status": "unresolved",
            "registry_version": snap.registry_version,
        }
    institution_id = chain[0]["institution_id"]
    # Walk the chain only up to the answered date; later links had not happened yet.
    applied = [link for link in chain if (answered_at or "9999-12-31") >= link["effective_date"]]
    if len(applied) == len(chain):
        current = chain[-1]
        return {
            "institution_id": institution_id,
            "agency_stat_key": f"inst:{institution_id}",
            "current_agency_code": current["to_code"],
            "current_agency_name": current["to_name"],
            "resolution_status": "resolved",
            "registry_version": snap.registry_version,
        }
    anchor = applied[-1] if applied else None
    return {
        "institution_id": institution_id,
        "agency_stat_key": f"inst:{institution_id}",
        "current_agency_code": anchor["to_code"] if anchor else code,
        "current_agency_name": anchor["to_name"] if anchor else name,
        "resolution_status": "resolved_as_of_date",
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
    if status == "resolved":
        return resolution.get("current_agency_name")
    if status == "resolved_as_of_date":
        return resolution.get("current_agency_name")
    return (source_name or "").strip() or None


def display_region(source_name: str | None, resolution: dict) -> str | None:
    if resolution.get("status") in ("resolved", "current_then"):
        return resolution.get("display")
    if resolution.get("status") in ("historical", "gap"):
        return resolution.get("display")
    return (source_name or "").strip() or None
