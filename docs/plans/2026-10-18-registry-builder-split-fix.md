# 기관코드 빌더 1:다 분할 결함 수정과 출시 감사 지적 반영 (2026-10-18)

## 배경
출시 전 Astra 최종 감사(`.agent-runs/final-1018/astra-final.md`)가 새 스냅샷 2026-10-18.1 에서 서울중앙지방공소청 `1282083` 과
부산지방공소청 `1282071` 이 같은 통계 키 `inst:ag-c1280089` 로 합쳐짐을 찾았다. Opus 확인: **이미 출시된 2026-09-29.3 에서도**
서울중앙지검 `1280934` 와 부산지검 `1280389` 가 `ag-c1280089` 로 합쳐져 있었다(빌더 결함). 공소청 개편 연결이 추가되며 새 코드로 번졌다.

원인(Opus 판단, Astra 가 검증할 것): `scripts/agency_registry/build_official_index.py` `build_derived` 는 폐지 코드마다
`boundary(code) → 직접 후속의 경계` 를 `prev_1to1` 링크로 만든다. 같은 경계 Bx 아래의 서로 다른 하위 코드가 서로 다른 후속 경계(A, B)로 이어지면
Bx→A, Bx→B 두 링크가 생기고, `institution_components` 가 이를 union 해 A 와 B 를 한 기관으로 묶는다. 이것은 **경계 수준 1:다**이며
스킬 규칙상 "링크 없이 (구) 보존"해야 한다.

사용자 결정(2026-10-18): 빌더까지 고치고 출시. 버전은 서버 3.5.0 · 앱 2.4.0(Opus 가 올린다 — 너는 VERSION·릴리스 노트를 건드리지 마라).
스냅샷 2026-10-18.1 은 로컬 커밋만 있고 배포·push 되지 않았으므로 **버전 라벨은 2026-10-18.1 그대로** 다시 빌드한다(빌더 버전 문자열은 바꿔도 된다).

## A. 빌더 수정 (서버 정본, 세 레포 전파)
1. 같은 from 경계에서 서로 다른 to 경계로 가는 파생 링크가 2개 이상이면 그 from 의 파생 링크를 모두 만들지 않는다(seed 링크는 예외 — seed 가 우선).
   제거 수를 provenance stats 에 기록(예: `boundary_split_dropped`). 결정적이어야 한다.
2. **불변식 검사**(`scripts/agency_registry/validate.py` 와 빌더): 현존(alive) 경계 코드 두 개가 같은 `institution_id` 를 가지면 실패.
   수정 후 위반이 남으면 사례를 분류해 보고하고(정당한 경우가 있는지), 정당하지 않으면 같은 원리로 고친다. 임의 예외 목록 금지.
3. 벡터(`vectors/resolve_cases.json`)에 추가: `1282083`/`1282071` 서로 다른 키, `1280934`/`1280389` 서로 다른 키(각 현행 표시명). 기존 벡터 기대값이 바뀌면 이유를 표로.
4. 영향 보고: 이전 스냅샷(`git show df36ed0^:shared/agency-region-registry/...` 또는 v3.4.0 태그의 스냅샷) 대비 통계 키가 바뀌는 코드 수와 분류
   (공소청 개편 승계 / 분할 결함 수정으로 갈라짐 / 그 밖). 운영 205 실제 기관 쌍은 `/tmp/claude-1000/-home-better0101-projects-safetyreport/3d71e0a7-a0fa-4c9c-96d5-ea221516df9a/scratchpad/agency_pairs.json`
   (`[code, name, count]`, 개인정보 없음)으로 바뀌는 쌍·신고 수를 보고.
5. 빌드: `python3 scripts/agency_registry/build.py --references-dir .agent-runs/agency-registry-handoff/agency-registry-handoff/references --official-zip .agent-runs/orgcode-20261018/orgcode-full.zip --registry-version 2026-10-18.1 --as-of-date 2026-10-18`
   → `validate.py` → `sync.py --to <mobile> --to <map>` → `check.py --repos ...`. sync 가 만드는 `shared/.agency-region-registry.prev/` 는 **커밋 대상이 아니다**(git add 하지 마라).

## B. 지도(community-map) 배포 안전성 (감사 2·3·4)
- 배포 순서: **Edge(새 스냅샷 번들) 먼저 → singleton migration → recompute**. 기존 배포 문서·CHANGELOG·migration 주석의 순서를 이렇게 고친다
  (구 Edge 가 계산한 행에 새 버전이 찍혀 재계산에서 빠지는 문제). 가능하면 트리거가 실제 계산 버전을 보존하거나 불일치 쓰기를 거절하게 하는 방법을 검토하고,
  범위가 크면 문서 순서 수정 + 이유만 남기고 보고서에 대안을 적어라(운영 DB 를 건드리지 마라, migration 은 파일만).
- `scripts/recompute-agency-keys.mjs`: 기관코드 없는 기존 행의 `inst:` 키를 무조건 보존하는 대신, 저장된 원문 이름으로 새 스냅샷의 별칭 해석이
  **유일하게 성공(code_derived)** 하면 새 키·담당자 키로 갱신하고, 실패·모호하면 기존 키를 유지한다. dry-run 출력에 이 경우 수를 따로 보인다. 시험 추가.
- 롤백 지침(새 migration 주석, deployment 문서): singleton 만 되돌리면 facts 가 안 돌아간다. 이전 Edge 번들 재배포 + singleton 되돌림 + 이전 스냅샷으로 recompute 재실행 절차를 적는다.

## C. 담당자 정규화 일치 (감사 5)
통계의 담당자 묶음과 정확 일치 검색이 같은 정규화를 쓰게 한다: 서버는 `report_policy.norm`, 모바일은 `ReportPolicy.norm`(두 플랫폼이 같은 공백 집합).
- 서버 `services/stats/metrics.py` 담당자 열(`str.strip()`)을 norm 으로, 모바일 `lib/services/local_statistics.dart` 담당자(`trim()`)도 norm 으로.
  정확 일치(`client_report_search` managerExact, `report_filter_spec` personExact, 모바일 `matchesExactText`)는 이미 norm(대소문자 구분) — 그대로.
- 미배정 담당자 판정(`_UNASSIGNED_PERSON_VALUES` 등)도 같은 정규화 뒤 비교. 서버↔모바일 동등성 유지.
- 시험: `Lee` / `﻿Lee` 는 통계 한 행, 정확 일치 목록 그 행 건수와 같음.

## 실행·검증
- 서버 `SAFETYREPORT_DATA_DIR=$(mktemp -d) /home/better0101/projects/safetyreport/.venv/bin/python -m unittest discover -s tests -p "test_*.py"`
- 모바일 `~/development/flutter-3.47.5/bin/flutter test` / `analyze`(샌드박스로 막히면 보고 — Opus 가 실행)
- 지도 `npm test -- --run`
- 세 레포 `python3 scripts/agency_registry/check.py --repos /home/better0101/projects/safetyreport /home/better0101/projects/safetyreport-mobile-wt-statsall /home/better0101/projects/safetyreport-community-map --run-tests`
- 서버↔모바일 `scripts/dev/logic_parity_check.py --mobile-repo <mobile> --flutter ~/development/flutter-3.47.5/bin/flutter`(가능하면)

## 문서
서버 `docs/architecture/community-upload.md` 의 registry 절(빌더 규칙), 세 레포 CHANGELOG 의 2026-10-18 registry 항목 정정(영향 숫자 갱신), 지도 deployment 문서.
