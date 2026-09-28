# Agency/region registry — snapshot schema (v2)

런타임 스냅샷이다. 편집 원본·빌더는 safetyreport `scripts/agency_registry/`에 있고,
세 저장소(`safetyreport`·`safetyreport-mobile`·`safetyreport-community-map`)의
`shared/agency-region-registry/` 사본은 바이트까지 같아야 한다(스킬 `sync`가 확인).

## 파일

- `manifest.json`: `schema_version`(이 문서 버전 2), `registry_version`(예 `2026-09-29.1`),
  `as_of_date`(공식자료 취득일), `generated_at`, `builder`, `source_hashes`(입력 SHA-256),
  `files`(사본 내 파일별 SHA-256), `readers`, `contract`.
- `provenance.json`: 입력별 경로·해시·역할, 공식자료 파생 규칙·통계, 알려진 한계(limits).
  원본 zip 자체는 Git에 커밋하지 않는다(해시·취득시각·행 수만 기록).
- `data/region_events.json`: `{events: [...]}`. 각 사건은
  `event_id, effective_date(YYYY-MM-DD), old_code, old_name, old_closed(없으면 null),
  new_codes[], new_names[], relation, handling_note` 를 가진다.
  `relation` ∈ {rename, rename_under_merge, transfer, merge, split, merge_parent, reestablished}.
  `handling_note`는 사람용 메모이며 런타임이 해석하지 않는다.
- `data/agency_links.json`: `{links: [...]}`. 검증된 1:1 승계만 둔다(경계 수준):
  `from_code, to_code, to_name, effective_date, closed_date(없으면 null),
  link_kind, institution_id(불변 내부 ID), evidence(수기 seed) 또는 rule(공식 연쇄 파생)`.
  `to_name`은 경계 코드의 현행 표시명(위 표시 규칙 적용)이다.
  같은 from에 후속이 2개 이상(경계 수준 1:다)이면 링크를 만들지 않는다.
- `data/agency_index.json`: `{cols: ["code","name","agg","type","created","lookup_name"], rows: [...]}`.
  현존 기관코드 색인(코드 정렬). 경계 코드는 `name`(현행 표시명)을 가지고
  `agg == code`이며, 하위조직 코드는 `name == null`, `agg`가 집계기관 코드다.
  현행 표시명은 공식 '전체기관명'에서 맨 앞의 '경찰청 ' 접두어만 한 번 뗀 값이다
  (2026-09-29 사용자 결정. 공백 경계의 정확한 접두어만 해당:
  '경찰청 광주경찰청 광주동부경찰서' → '광주경찰청 광주동부경찰서',
  본청 '경찰청'·'경찰청장…'·비경찰 이름은 그대로. 빌더가 출력 단계에서만 적용하고
  집계(경계 판정)의 이름 경로 비교는 공식 원문명으로 한다).
  `lookup_name`은 표시명과 다른 공식 원래 전체기관명이며, 같으면 `null`이다.
  하위조직 행은 `name`과 `lookup_name`이 모두 `null`이다. 코드 없는 신고의
  이름 별칭은 경계 코드의 `name`과 `lookup_name` 양쪽을 정확 일치로 조회한다.
  같은 코드가 양쪽에서 일치해도 후보는 하나로 센다. 답변일의 생성일 필터를
  통과한 코드가 유일할 때만 파생하고, 여러 코드면 미확정으로 둔다.
  각 저장소의 원문 `처리기관`은 그대로 보존한다.
  `type`은 `대-중` 유형 태그, `created`는 생성일자(YYYYMMDD, 없으면 null).
  현존 코드 중 유형분류_대 04/05/06/11-17/18/80(입법·사법·헌법·학교·군·금융)은
  제외한다(단 kept 자식이 참조하는 경계는 포함). 제외된 코드의 신고도 원문
  그대로 별도 행(src 키)으로 보존되며 자료 손실은 없다.
- `data/agency_legacy.json`: `{forward: {old: final}, multi: {old: name}}`.
  폐지 코드의 현행 귀결. `forward`는 1:1 연쇄의 최종 현존 경계,
  `multi`는 후속 경계가 2개 이상인 코드의 마지막 알려진 이름((구) 표시용).
  후속 없음·순환·공란은 기록하지 않는다(미확정).
- `data/agency_institutions.json`: `{institutions: {boundary: inst_id}}`.
  모든 경계 코드의 통계 기관 ID. seed 핀 우선, 나머지는 `ag-c<최소코드>`.
- `data-sources/*.csv`: 검토 입력의 바이트 동일 사본(읽기 전용).
- `resolvers/resolve.py(.dart/.ts)`: 리더 3종(알고리즘은 `resolvers/README.md`).
- `vectors/resolve_cases.json`: `{cases: [{name, kind, input, expected}]}`.
  `kind` ∈ {agency, region}. 세 리더 테스트가 같은 파일을 읽는다.
  agency 5종(개명·승계·1:다·미확정·코드 없음 이름만)을 반드시 포함하고,
  표시 규칙 고정 케이스('경찰청 ' 접두어 제거·본청 '경찰청' 유지·비경찰 원문 유지)도 둔다.

## 의미(요약)

| 개념 | 뜻 |
|---|---|
| `institution_id` | 확인된 동일 기관의 불변 내부 ID(파생값, 원본 아님) |
| `agency_stat_key` | `inst:<id>`(확인) 또는 `src:<code>:<name>`(미확정·(구) 별도행) |
| `current_agency_code/name` | 최신 적용 registry가 해석한 현행값(미확정이면 원문 유지) |
| `resolution_status` | `resolved`(현행 표시)·`resolved_as_of_date`(당시 표시)·`historical`((구) 보존)·`unresolved`(원문 유지) |
| `code_derived` | 코드 없이 이름 유일 매칭으로 파생한 경우에만 `true`(원문 코드欄을 덮어쓰지 않음) |
| region `status` | `resolved`(단일 후속 합산)·`historical`((구) 보존)·`gap`(구역 없던 기간)·`current_then`(사건 전 현행)·`unresolved` |
| `registry_version` | 해석·표시의 기준 버전(모든 출력에 포함) |

`source_*`(원문 코드·기관명)는 이 스냅샷이 덮어쓰지 않는다. `(구)`는 표시용이며
원문 문자열에 접두사를 붙여 저장하지 않는다.
