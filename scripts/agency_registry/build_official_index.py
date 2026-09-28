#!/usr/bin/env python3
"""공식 기관코드 전체자료(zip, 로컬) → agency_index/legacy/links 파생 자료.

입력: code.go.kr '기관코드 전체자료' zip (이미 내려받아 둔 로컬 파일만 사용.
외부 다운로드는 fetch_official.py(사용자 직접 실행) 경로이며 여기서는 하지 않음).
원본 zip은 Git에 커밋하지 않고 SHA-256·취득시각·행 수만 provenance에 기록한다.

읽는 내부 파일: '기관코드 전체자료.txt' (cp949, TAB). '유형분류 의미추가' 변형은
같은 행 집합이므로 읽지 않는다(조사용으로만 둔다).

출력(결정적: 코드 정렬, canon JSON):
  data/agency_index.json   현존 코드 색인 {cols, rows:[code,name,agg,type,created]}
  data/agency_legacy.json  폐지 코드 {forward:{old:final}, multi:{old:name}}
  derived links 목록        경계-수준 1:1 이전기관코드 연쇄 (build.py가 seed와 합쳐 기록)

필터 규칙(번들 크기 근거, schema.md·보고서에도 기록):
  현존 코드 중 유형분류_대 in {04 입법, 05 사법, 06 헌법, 11~17 학교, 18 군, 80 금융}
  은 색인에서 제외한다. 안전신문고 민원(생활불편·교통위반 등)의 답변 기관이 될 수
  없는 유형이며, 제외된 코드의 신고도 원문 그대로 별도 행(src 키)으로 보존된다
  (자료 손실 없음 — 묶음 범위만 좁힌다).

집계기관(agg) 규칙(빌드 시점 확정, 런타임은 조회만):
  하위조직 유형은 차상위기관코드 사슬을 타고 올라가 집계기관에서 멈춘다.
  하위 = {(01,02),(01,03),(01,04),(01,05),(01,07),(02,03),(02,08),(02,09),(02,10),
          (03,05),(03,07),(03,08)} 또는 (그 외 유형에서 부모와 (대,중)이 같은 경우).
  단, 부모 전체기관명+' '이 자식 이름의 접두어일 때만 올라간다(다른 조직 병합 방지),
  부모의 차수가 자식보다 작을 때만 올라간다. 나머지는 자기 자신이 집계기관이다.
  대표기관코드를 통계 키로 쓰지 않는다(여러 경찰서가 경찰청으로 합쳐지는 것을 방지).

승계 규칙(handoff §6.1 그대로):
  폐지 코드 X의 후속 = 이전기관코드가 X인 행. 각 후속을 경계 코드로 매핑한 집합이
  정확히 1개(현존 경계 B)면 X→B 전달(forward). 2개 이상이면 multi((구) 보존),
  0개·순환이면 미기록(미확정). 공란(NULL)이면 연결하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

INNER_NAME = "기관코드 전체자료.txt"

EXCLUDED_TOP_TYPES = {"04", "05", "06", "11", "12", "13", "14", "15", "16", "17", "18", "80"}

SUBUNIT_DA_JUNG = {
    ("01", "02"), ("01", "03"), ("01", "04"), ("01", "05"), ("01", "07"),
    ("02", "03"), ("02", "08"), ("02", "09"), ("02", "10"),
    ("03", "05"), ("03", "07"), ("03", "08"),
}

# 집계기관 유형: 이 (대,중)이면 절대 올라가지 않는다(자기 자신이 경계).
# 특별지방행정기관(경찰서·지청 등)이 같은 (01,08) 상위 청으로 합쳐지는 것을 막는다.
AGGREGATE_DA_JUNG = {
    ("01", "01"), ("01", "08"),
    ("02", "01"), ("02", "02"),
    ("03", "01"), ("03", "04"),
}


def _norm(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text or text == "NULL":
        return None
    return text


def _ymd8_to_iso(raw: str | None) -> str | None:
    if raw is None or len(raw) != 8 or not raw.isdigit():
        return None
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"


def load_official(zip_path: Path) -> tuple[list[dict], str, str]:
    """Return (rows, zip_sha256, inner_sha256). Rows are dicts of normalized fields."""
    blob = zip_path.read_bytes()
    zip_sha = hashlib.sha256(blob).hexdigest()
    with zipfile.ZipFile(zip_path) as zf:
        inner = zf.read(INNER_NAME)
    inner_sha = hashlib.sha256(inner).hexdigest()
    text = inner.decode("cp949")
    lines = text.splitlines()
    header = lines[0].split("\t")
    rows: list[dict] = []
    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < len(header):
            parts = parts + [""] * (len(header) - len(parts))
        rec = dict(zip(header, parts))
        rows.append({
            "code": (rec.get("기관코드") or "").strip(),
            "name": (rec.get("전체기관명") or "").strip(),
            "short": (rec.get("최하위기관명") or "").strip(),
            "level": _norm(rec.get("차수")),
            "parent": _norm(rec.get("차상위기관코드")),
            "rep": _norm(rec.get("대표기관코드")),
            "type_top": _norm(rec.get("유형분류_대")),
            "type_mid": _norm(rec.get("유형분류_중")),
            "created": _norm(rec.get("생성일자")),
            "closed": _norm(rec.get("폐지일자")),
            "alive": (rec.get("존폐여부") or "").strip() == "0",
            "prev": _norm(rec.get("이전기관코드")),
        })
    rows = [r for r in rows if r["code"]]
    return rows, zip_sha, inner_sha


def _level_num(raw: str | None) -> int | None:
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def compute_boundary(code: str, by_code: dict[str, dict]) -> str:
    """집계기관 코드. 빌드 시점에만 실행한다."""
    seen = {code}
    node = by_code.get(code)
    if node is None:
        return code
    for _ in range(32):
        da, jung = node.get("type_top"), node.get("type_mid")
        if (da, jung) in AGGREGATE_DA_JUNG:
            return node["code"]
        subunit = (da, jung) in SUBUNIT_DA_JUNG
        parent = by_code.get(node.get("parent") or "")
        if parent is None:
            return node["code"]
        if not subunit and (da, jung) != (parent.get("type_top"), parent.get("type_mid")):
            return node["code"]
        # 하위조직이거나 부모와 같은 (대,중) 내부 단위 → 이름 경로·차수로만 올린다.
        pname = parent.get("name") or ""
        cname = node.get("name") or ""
        if not pname or not cname.startswith(pname + " "):
            return node["code"]
        plevel, clevel = _level_num(parent.get("level")), _level_num(node.get("level"))
        if plevel is None or clevel is None or not plevel < clevel:
            return node["code"]
        if parent["code"] in seen:
            return node["code"]
        seen.add(parent["code"])
        node = parent
    return node["code"]


def build_derived(by_code: dict[str, dict]) -> dict:
    """Index/legacy/links 파생. returns stats + tables."""
    succ: dict[str, set[str]] = {}
    for code, row in by_code.items():
        if row.get("prev"):
            succ.setdefault(row["prev"], set()).add(code)

    boundary_cache: dict[str, str] = {}

    def boundary(code: str) -> str:
        if code not in boundary_cache:
            boundary_cache[code] = compute_boundary(code, by_code)
        return boundary_cache[code]

    final_cache: dict[str, frozenset[str] | None] = {}

    def final_alive(code: str, stack: tuple[str, ...] = ()) -> frozenset[str] | None:
        """code의 후속들을 현존 경계 집합으로. 순환이면 None."""
        if code in final_cache:
            cached = final_cache[code]
            return cached
        if code in stack:
            return None  # Any reachable cycle makes the whole lineage ambiguous.
        row = by_code.get(code)
        if row is None:
            return frozenset()
        if row.get("alive"):
            return frozenset({boundary(code)})
        out: set[str] = set()
        for s in succ.get(code, set()):
            sub = final_alive(s, stack + (code,))
            if sub is None:
                final_cache[code] = None
                return None
            out |= set(sub)
        result = frozenset(out)
        final_cache[code] = result
        return result

    # None is deliberately distinct from no successor: it propagates a cycle
    # even when another branch reaches a live code.

    index_rows: list[list] = []
    boundary_names: dict[str, dict] = {}
    forward: dict[str, str] = {}
    multi: dict[str, str] = {}
    link_pairs: dict[tuple[str, str], dict] = {}
    stats: dict[str, int] = {
        "official_rows": len(by_code),
        "alive_total": 0, "alive_kept": 0, "alive_excluded": 0,
        "boundaries": 0, "children": 0,
        "abolished_total": 0, "legacy_forward": 0, "legacy_multi": 0,
        "legacy_unresolved": 0, "derived_links": 0,
    }

    for code, row in by_code.items():
        if row.get("alive"):
            stats["alive_total"] += 1
        else:
            stats["abolished_total"] += 1

    for code, row in sorted(by_code.items()):
        if row.get("alive"):
            if (row.get("type_top") or "") in EXCLUDED_TOP_TYPES:
                stats["alive_excluded"] += 1
                continue
            stats["alive_kept"] += 1
            agg = boundary(code)
            agg_row = by_code.get(agg, row)
            boundary_names.setdefault(agg, {
                "name": agg_row.get("name") or row.get("name") or "",
                "created": agg_row.get("created"),
            })
            if agg == code:
                stats["boundaries"] += 1
                index_rows.append([code, row.get("name") or "",
                                   code, _type_tag(row), row.get("created")])
            else:
                stats["children"] += 1
                index_rows.append([code, None, agg, None, None])

    # kept 자식이 참조하는데 제외 유형이라 빠진 경계(대학 등)는 이름과 함께 포함한다.
    # 제외 취지는 번들 크기이며 참조된 경계는 소수이므로, dangling agg를 남기지 않는다.
    have = {r[0] for r in index_rows}
    missing_agg = sorted({r[2] for r in index_rows if r[2] not in have})
    for agg in missing_agg:
        agg_row = by_code.get(agg)
        if agg_row is None:
            continue
        stats["alive_kept"] += 1
        stats["boundaries"] += 1
        stats["referenced_excluded_boundaries"] = stats.get("referenced_excluded_boundaries", 0) + 1
        boundary_names.setdefault(agg, {
            "name": agg_row.get("name") or "", "created": agg_row.get("created")})
        index_rows.append([agg, agg_row.get("name") or "",
                           agg, _type_tag(agg_row), agg_row.get("created")])
    index_rows.sort(key=lambda r: r[0])

    for code, row in sorted(by_code.items()):
        if row.get("alive"):
            continue
        if (row.get("type_top") or "") in EXCLUDED_TOP_TYPES:
            stats["legacy_unresolved"] += 1
            continue
        finals = final_alive(code)
        if finals is not None and len(finals) == 1:
            target = next(iter(finals))
            trow = by_code.get(target, {})
            boundary_names.setdefault(target, {
                "name": trow.get("name") or "", "created": trow.get("created")})
            forward[code] = target
            stats["legacy_forward"] += 1
        elif finals is not None and len(finals) >= 2:
            multi[code] = row.get("name") or ""
            stats["legacy_multi"] += 1
        else:
            stats["legacy_unresolved"] += 1
            continue
        # 인접 링크만 만든다(Bx→직접 후속의 경계). 추이 링크는 as-was 표시를
        # 깨뜨리므로 만들지 않는다. legacy.forward가 현행 귀결을 보장한다.
        bx = boundary(code)
        direct = {boundary(s) for s in succ.get(code, set()) if s in by_code}
        if len(direct) == 1:
            nxt = next(iter(direct))
            if nxt != bx:
                if nxt not in boundary_names:
                    nrow = by_code.get(nxt, {})
                    boundary_names[nxt] = {
                        "name": nrow.get("name") or "", "created": nrow.get("created")}
                key = (bx, nxt)
                if key not in link_pairs:
                    bx_row = by_code.get(bx, {})
                    hop_created = next(
                        (by_code[s].get("created") for s in sorted(succ.get(code, set()))
                         if s in by_code and boundary(s) == nxt and by_code[s].get("created")),
                        None)
                    eff8 = hop_created or bx_row.get("closed") or row.get("closed")
                    # from_name은 싣지 않는다(표시명은 인덱스·to_name에서 찾는다).
                    # 번들 크기 절감용이며 판정 근거는 rule+provenance가 대신한다.
                    link_pairs[key] = {
                        "from_code": bx,
                        "to_code": nxt,
                        "to_name": boundary_names[nxt]["name"],
                        "effective_date": _ymd8_to_iso(eff8) or "1970-01-01",
                        "closed_date": _ymd8_to_iso(bx_row.get("closed") or row.get("closed")),
                        "link_kind": "prev_1to1",
                        "rule": "official-prev-chain-1to1",
                    }

    stats["derived_links"] = len(link_pairs)
    # 경계 수준 1:다: 같은 from에 후속이 2개 이상이면 링크를 만들지 않는다
    # (handoff: 1:다는 연결하지 않고 (구)/미확정으로 보존). legacy.forward가
    # 현행 귀결을 보장하므로 현행 표시·통계 키는 그대로 정확하다.
    by_from: dict[str, set[str]] = {}
    for (a, b) in link_pairs:
        by_from.setdefault(a, set()).add(b)
    dropped = sum(1 for v in by_from.values() if len(v) > 1)
    multi_from = {a for a, v in by_from.items() if len(v) > 1}
    link_pairs = {k: v for k, v in link_pairs.items() if k[0] not in multi_from}
    stats["derived_links"] = len(link_pairs)
    stats["boundary_branch_dropped"] = dropped
    return {
        "index_rows": index_rows,
        "boundary_names": boundary_names,
        "forward": forward,
        "multi": multi,
        "link_pairs": link_pairs,
        "stats": stats,
    }


def _type_tag(row: dict) -> str | None:
    top, mid = row.get("type_top"), row.get("type_mid")
    if not top:
        return None
    return f"{top}-{mid}" if mid else top


def institution_components(link_pairs: dict[tuple[str, str], dict],
                            seed_links: list[dict],
                            forward: dict[str, str] | None = None,
                            by_code: dict[str, dict] | None = None) -> dict[str, str]:
    """경계 코드 → institution_id. seed 핀 우선, 나머지는 ag-c<oldest>.
    링크가 끊긴 전달(forward)도 같은 기관으로 묶는다."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    for (a, b) in link_pairs:
        union(a, b)
    for link in seed_links:
        union(link["from_code"], link["to_code"])
    if forward and by_code is not None:
        for old, target in forward.items():
            bx_row = by_code.get(old)
            if bx_row is None:
                continue
            union(compute_boundary(old, by_code), target)
    groups: dict[str, set[str]] = {}
    for code in list(parent):
        groups.setdefault(find(code), set()).add(code)
    seed_inst: dict[str, str] = {}
    for link in seed_links:
        seed_inst[find(link["from_code"])] = link["institution_id"]
    result: dict[str, str] = {}
    for root, members in groups.items():
        inst = seed_inst.get(root)
        if inst is None:
            inst = f"ag-c{min(members).lower()}"
        for code in members:
            result[code] = inst
    return result


def institution_map(link_pairs: dict[tuple[str, str], dict],
                    seed_links: list[dict],
                    derived: dict,
                    by_code: dict[str, dict]) -> dict[str, str]:
    """모든 경계 코드 → institution_id (링크·전달·고립 경계 전부)."""
    comp = institution_components(link_pairs, seed_links,
                                  derived["forward"], by_code)
    boundary_of = derived.get("boundary_of", {})

    def boundary(code: str) -> str:
        if code in boundary_of:
            return boundary_of[code]
        b = compute_boundary(code, by_code)
        boundary_of[code] = b
        return b

    alive_boundaries = {row[2] for row in derived["index_rows"]}
    for old, target in derived["forward"].items():
        alive_boundaries.add(target)
        alive_boundaries.add(boundary(old))
    for (a, b) in link_pairs:
        alive_boundaries.add(a)
        alive_boundaries.add(b)
    for link in seed_links:
        alive_boundaries.add(link["from_code"])
        alive_boundaries.add(link["to_code"])
    for code in sorted(alive_boundaries):
        comp.setdefault(code, f"ag-c{code.lower()}")
    derived["boundary_of"] = boundary_of
    return comp
