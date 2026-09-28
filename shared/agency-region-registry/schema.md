# Agency/region registry — snapshot schema (v1)

런타임 스냅샷이다. 편집 원본·빌더는 safetyreport `scripts/agency_registry/`에 있고,
세 저장소(`safetyreport`·`safetyreport-mobile`·`safetyreport-community-map`)의
`shared/agency-region-registry/` 사본은 바이트까지 같아야 한다(스킬 `sync`가 확인).

## 파일

- `manifest.json`: `schema_version`(이 문서 버전 1), `registry_version`(예 `2026-09-28.1`),
  `as_of_date`, `generated_at`, `builder`, `source_hashes`(입력 SHA-256),
  `files`(사본 내 파일별 SHA-256), `readers`, `contract`.
- `provenance.json`: 입력별 경로·해시·역할, 알려진 한계(limits).
- `data/region_events.json`: `{events: [...]}`. 각 사건은
  `event_id, effective_date(YYYY-MM-DD), old_code, old_name, old_closed(없으면 null),
  new_codes[], new_names[], relation, handling_note` 를 가진다.
  `relation` ∈ {rename, rename_under_merge, transfer, merge, split, merge_parent, reestablished}.
  `handling_note`는 사람용 메모이며 런타임이 해석하지 않는다.
- `data/agency_links.json`: `{links: [...]}`. 검증된 1:1 승계만 둔다:
  `from_code, from_name, to_code, to_name, effective_date, closed_date,
  link_kind, institution_id(불변 내부 ID), evidence`.
- `data-sources/*.csv`: 검토 입력의 바이트 동일 사본(읽기 전용).
- `resolvers/resolve.py(.dart/.ts)`: 리더 3종(알고리즘은 `resolvers/README.md`).
- `vectors/resolve_cases.json`: `{cases: [{name, kind, input, expected}]}`.
  `kind` ∈ {agency, region}. 세 리더 테스트가 같은 파일을 읽는다.

## 의미(요약)

| 개념 | 뜻 |
|---|---|
| `institution_id` | 확인된 동일 기관의 불변 내부 ID(파생값, 원본 아님) |
| `agency_stat_key` | `inst:<id>`(확인) 또는 `src:<code>:<name>`(미확정 별도행) |
| `current_agency_code/name` | 최신 적용 registry가 해석한 현행값(미확정이면 원문 유지) |
| `resolution_status` | `resolved`(현행 표시)·`resolved_as_of_date`(당시 표시)·`unresolved`(원문 유지) |
| region `status` | `resolved`(단일 후속 합산)·`historical`((구) 보존)·`gap`(구역 없던 기간)·`current_then`(사건 전 현행)·`unresolved` |
| `registry_version` | 해석·표시의 기준 버전(모든 출력에 포함) |

`source_*`(원문 코드·기관명)는 이 스냅샷이 덮어쓰지 않는다. `(구)`는 표시용이며
원문 문자열에 접두사를 붙여 저장하지 않는다.
