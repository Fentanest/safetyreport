#!/usr/bin/env python3
"""공식 기관코드 전체자료(zip, 로컬) → agency_index/legacy/links 파생 자료.

입력: code.go.kr '기관코드 전체자료' zip (이미 내려받아 둔 로컬 파일만 사용.
외부 다운로드는 fetch_official.py(사용자 직접 실행) 경로이며 여기서는 하지 않음).
원본 zip은 Git에 커밋하지 않고 SHA-256·취득시각·행 수만 provenance에 기록한다.

읽는 내부 파일: '기관코드 전체자료.txt' (cp949, TAB). '유형분류 의미추가' 변형은
같은 행 집합이므로 읽지 않는다(조사용으로만 둔다).

출력(결정적: 코드 정렬, canon JSON):
  data/agency_index.json   현존·폐지 경계 rows + 폐지 하위조직 compact_rows:[code,agg]
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
  지방자치단체(유형 02)는 대표기관이 실제 차상위 연쇄의 시도/시군구 조상이면
  그 자치단체까지 올린다. 경찰은 경찰서(시도경찰청 직할이면 시도경찰청)에서 멈추며
  대표기관코드 1320000을 집계 경계로 쓰지 않는다.

승계 규칙(handoff §6.1 그대로):
  폐지 코드 X의 후속 = 이전기관코드가 X인 행. 각 후속을 경계 코드로 매핑한 집합이
  정확히 1개(현존 경계 B)면 X→B 전달(forward). 2개 이상이면 multi((구) 보존),
  0개·순환이면 미기록(미확정). 공란(NULL)이면 연결하지 않는다.

폐지 하위조직 색인 규칙(2026-09-29.3):
  2014-01-01 이후 폐지된 후속 없는(forward/multi에 없는) 비제외 코드는 색인에 둔다. agg는
  차상위기관코드 연쇄로 찾은 답변 당시 소속 집계기관이며 현행과 같은
  compute_boundary 규칙을 쓴다(경찰은 경찰서 단위에서 멈춘다 — 대표기관코드로
  올리지 않는다). 집계기관이 이후 개명·1:1 승계됐으면(forward 보유) agg는 그
  최종 현존 경계를 가리킨다. 집계기관 자체가 후속 없이 폐지됐으면(예: 여수시
  4810000 — 전남광주통합특별시 출범 2026-07-01로 폐지, 새 코드 5785000의
  이전기관코드가 NULL이라 자동 연결 금지) 그 경계 행도 마지막 알려진 이름과
  함께 둔다. 폐지 하위조직 행은 [code,agg]로 압축한다.
  forward/multi 보유 코드는 기존 귀결을 유지하므로 색인에
  중복 등재하지 않는다(리졸버가 색인을 먼저 보므로 순서가 바뀌면 안 된다).

표시명 규칙(2026-09-29 사용자 결정):
  경계 코드의 저장명(= 현행 표시명)은 공식 '전체기관명'에서 맨 앞의 '경찰청 '
  접두어만 한 번 뗀다. 공백 경계가 있는 정확한 접두어만 해당한다:
  '경찰청 광주경찰청 광주동부경찰서' → '광주경찰청 광주동부경찰서',
  본청 '경찰청'(접두어 뒤에 아무것도 없음) → '경찰청' 그대로,
  '경찰청장…' 같은 다른 이름·비경찰 이름 → 그대로.
  집계(경계 판정)의 이름 경로 비교는 공식 원문명으로 하며, 제거는 출력 단계에서만
  적용한다. 별칭 조회용 lookup_name은 표시명과 다를 때 공식 원문명을 보존한다.
  multi(폐지 (구) 표시용 마지막 알려진 이름)는 역사 표시이므로 원문을 둔다.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

INNER_NAME = "기관코드 전체자료.txt"

# 현행 표시명 접두어 규칙(2026-09-29 사용자 결정): 공식 전체기관명 맨 앞의
# '경찰청 ' 한 번만. 공백 경계가 있는 정확한 접두어만 뗀다.
POLICE_DISPLAY_PREFIX = "경찰청 "


def display_agency_name(official_name: str | None) -> str:
    """공식 전체기관명 → 현행 표시명. '경찰청 ' 접두어 한 번만 제거한다."""
    name = (official_name or "").strip()
    if name.startswith(POLICE_DISPLAY_PREFIX):
        return name[len(POLICE_DISPLAY_PREFIX):]
    return name


def lookup_agency_name(official_name: str | None) -> str | None:
    """표시명과 다른 공식 전체기관명만 별칭 조회용으로 추가한다."""
    name = (official_name or "").strip()
    return name if name and name != display_agency_name(name) else None

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

ABOLISHED_SINCE = "20140101"


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
    # 유형 02-02는 시·군·구 자체와 그 내부의 '국'에 함께 쓰인다.
    # 대표기관이 실제 차상위 연쇄의 지자체인 경우에만 그 지자체까지 올린다.
    # 경찰(유형 01)은 이 경로를 타지 않아 경찰서 경계가 유지된다.
    rep = node.get("rep")
    if node.get("type_top") == "02" and rep and rep != code:
        ancestor = node
        for _ in range(32):
            parent = by_code.get(ancestor.get("parent") or "")
            if parent is None or parent["code"] == ancestor["code"]:
                break
            if parent["code"] == rep:
                if parent.get("type_top") == "02" and parent.get("type_mid") in {"01", "02"}:
                    return rep
                break
            ancestor = parent
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
    compact_rows: list[list[str]] = []
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
        "abolished_index_children": 0, "abolished_index_boundaries": 0,
        "abolished_index_forward_agg": 0, "abolished_index_self_agg": 0,
        "abolished_before_2014_omitted": 0,
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
                "name": display_agency_name(agg_row.get("name") or row.get("name")),
                "created": agg_row.get("created"),
            })
            if agg == code:
                stats["boundaries"] += 1
                index_rows.append([code, display_agency_name(row.get("name")),
                                   code, _type_tag(row), row.get("created"),
                                   lookup_agency_name(row.get("name"))])
            else:
                stats["children"] += 1
                index_rows.append([code, None, agg, None, None, None])

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
            "name": display_agency_name(agg_row.get("name")), "created": agg_row.get("created")})
        index_rows.append([agg, display_agency_name(agg_row.get("name")),
                           agg, _type_tag(agg_row), agg_row.get("created"),
                           lookup_agency_name(agg_row.get("name"))])
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
                "name": display_agency_name(trow.get("name")), "created": trow.get("created")})
            forward[code] = target
            stats["legacy_forward"] += 1
        elif finals is not None and len(finals) >= 2:
            # (구) 표시용 역사 이름: 현행 표시가 아니라 마지막 알려진 원문을 둔다.
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
                        "name": display_agency_name(nrow.get("name")), "created": nrow.get("created")}
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
    # --- 폐지 하위조직 색인(2026-09-29.3): 후속 없는 폐지 비제외 코드 ---
    # forward/multi 보유 코드는 건드리지 않는다(기존 귀결 유지 — 리졸버가 색인을
    # 먼저 보므로 중복 등재하면 순서가 바뀐다). 하위조직 행은 차상위 연쇄의 답변
    # 당시 소속 집계기관을 agg로 가지며, 집계기관 자체가 후속 없이 폐지됐으면
    # 그 경계 행을 마지막 알려진 이름과 함께 둔다. dangling agg를 남기지 않는다.
    for code, row in sorted(by_code.items()):
        if row.get("alive"):
            continue
        if not row.get("closed") or row["closed"] < ABOLISHED_SINCE:
            stats["abolished_before_2014_omitted"] += 1
            continue
        if (row.get("type_top") or "") in EXCLUDED_TOP_TYPES:
            continue
        if code in forward or code in multi:
            continue
        agg0 = boundary(code)
        if agg0 == code:
            stats["alive_kept"] += 1
            stats["boundaries"] += 1
            stats["abolished_index_boundaries"] += 1
            index_rows.append([code, display_agency_name(row.get("name")),
                               code, _type_tag(row), row.get("created"),
                               lookup_agency_name(row.get("name"))])
            continue
        agg_row = by_code.get(agg0)
        if agg_row is not None and (agg_row.get("type_top") or "") not in EXCLUDED_TOP_TYPES \
                and agg0 not in forward and agg0 not in multi:
            agg = agg0
        elif agg_row is not None and agg0 in forward:
            # 집계기관이 이후 개명·1:1 승계됐으면 기존 규칙대로 현행 경계로 잇는다.
            agg = forward[agg0]
            stats["abolished_index_forward_agg"] += 1
        else:
            # 집계기관이 제외 유형·다분기(multi)·원자료 부재면 자기 경계로 둔다
            # (알려진 역사 노드로서 스스로 묶이며 미확정 src 행은 피한다).
            stats["alive_kept"] += 1
            stats["boundaries"] += 1
            stats["abolished_index_boundaries"] += 1
            stats["abolished_index_self_agg"] += 1
            index_rows.append([code, display_agency_name(row.get("name")),
                               code, _type_tag(row), row.get("created"),
                               lookup_agency_name(row.get("name"))])
            continue
        stats["alive_kept"] += 1
        stats["children"] += 1
        stats["abolished_index_children"] += 1
        compact_rows.append([code, agg])
    # 폐지 자식이 참조하는데 행이 없는 집계기관은 이름과 함께 포함한다.
    # dangling agg를 남기지 않는다.
    have = {r[0] for r in index_rows}
    missing_agg = sorted({r[2] for r in index_rows if r[2] not in have}
                         | {r[1] for r in compact_rows if r[1] not in have})
    for agg in missing_agg:
        agg_row = by_code.get(agg)
        if agg_row is None:
            continue
        stats["alive_kept"] += 1
        stats["boundaries"] += 1
        stats["abolished_index_boundaries"] += 1
        boundary_names.setdefault(agg, {
            "name": display_agency_name(agg_row.get("name")), "created": agg_row.get("created")})
        index_rows.append([agg, display_agency_name(agg_row.get("name")),
                           agg, _type_tag(agg_row), agg_row.get("created"),
                           lookup_agency_name(agg_row.get("name"))])
    if missing_agg:
        stats["abolished_backfill_boundaries"] = len(missing_agg)
    index_rows.sort(key=lambda r: r[0])
    compact_rows.sort(key=lambda r: r[0])

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
        "compact_rows": compact_rows,
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
    alive_boundaries.update(row[1] for row in derived["compact_rows"])
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
