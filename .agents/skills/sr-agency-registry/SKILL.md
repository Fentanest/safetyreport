---
name: sr-agency-registry
description: 기관·행정구역 코드 최신 변경을 확인하고, 추적 가능한 변경과 (구)로 남길 변경을 정리해서 safetyreport·safetyreport-mobile·safetyreport-community-map에 반영해. (공식 전체자료 → 빌드 → 검증 → 3레포 sync)
---

# 기관·지역 코드 갱신·전파

정본: safetyreport의 `shared/agency-region-registry/`(런타임 스냅샷) +
`scripts/agency_registry/`(빌더·검증·전파). community-ingest 업로드 계약의
정본(`safetyreport-community-map/contracts/community-ingest`)과 다르다 —
계약은 MAP에서 고치고 `--to`로 복사한다.

먼저 `shared/agency-region-registry/schema.md`와 `resolvers/README.md`를 읽는다.
원문 기관코드·기관명은 사실로 보존하고, 통계용 ID·현행 표시는 버전이 붙은
이 스냅샷에서만 계산한다. `이전기관코드` 공란을 승계로 간주하지 않고,
이름 해시·문자열 치환으로 기관을 잇지 않으며, `(구)`는 알려진 역사 노드에만
붙인다(미확정은 원문 유지).

## 입력: 공식 기관코드 전체자료(로컬 파일만)

공식 전체자료(zip)는 Git에 커밋하지 않는다. 이미 내려받아 둔 로컬 파일만
`--official-zip`으로 지정한다(외부 다운로드는 `fetch_official.py
--confirm-download`를 사용자가 직접 실행할 때만). 빌더는 zip·내부 파일의
SHA-256·취득시각·행 수를 `provenance.json`에 기록한다.

## 모드별 실행 명령 (저장소 루트에서)

세 저장소 루트를 `<pc> <mobile> <map>`이라 한다. 사용자 변경이 있으면 먼저
보호하고, 범위 밖 수정·운영 DB·외부 전송·push를 하지 않는다.

```sh
# 1. inspect — 세 저장소 경로·remote·버전·dirty 상태 확인(읽기 전용)
python3 scripts/agency_registry/examine.py --repos <pc> <mobile> <map>

# 2a. 공식 자료 확인 (다운로드 금지 — 로컬 파일 해시만 대조)
sha256sum <official-zip>
python3 scripts/agency_registry/fetch_official.py   # dry-run: 받을 URL만 표시

# 2b. build/validate — 공식 전체자료 + 검토 입력을 스냅샷으로 빌드하고 검증
python3 scripts/agency_registry/build.py --references-dir <handoff-references> --official-zip <official-zip> --registry-version <YYYY-MM-DD.N> --as-of-date <YYYY-MM-DD>
python3 scripts/agency_registry/validate.py --snapshot shared/agency-region-registry

# 3. dry-run — 지역/기관 표시·분류 변화와 영향(테스트 포함)을 먼저 보고
python3 scripts/agency_registry/sync.py --to <mobile> --to <map> --dry-run
python3 scripts/agency_registry/diff.py --old <이전스냅샷> --new shared/agency-region-registry --out /tmp/agency-diff.json

# 4. 테스트 (데이터만 바뀌어도 실행; 3개 리더를 함께 갱신)
SAFETYREPORT_DATA_DIR=$(mktemp -d) .venv/bin/python -m unittest tests.test_agency_registry
(cd <map> && npm test -- --run)            # tests/product/agencyRegistry.test.ts 포함
(cd <mobile> && flutter test test/storage/registry_vectors_test.dart)

# 5. sync/check — 세 저장소에 동일 버전 전파·해시 확인
python3 scripts/agency_registry/sync.py --to <mobile> --to <map>
python3 scripts/agency_registry/check.py --repos <pc> <mobile> <map> --run-tests

# 6. rollback — 전파 실패 시 이전 스냅샷 복원 후 check 재실행
python3 scripts/agency_registry/rollback.py --repo <mobile>
```

## 스냅샷 내용(v2, 2026-09-29~)

- `data/agency_index.json`: 현존 코드 색인 `[code,name,agg,type,created]`
  (집계기관 접기 완료 — 런타임은 조회만). 제외 유형(입법·사법·헌법·학교·군·금융)
  과 번들 크기(압축 전/후)는 provenance·보고서에 기록한다.
- `data/agency_legacy.json`: 폐지 코드 `{forward, multi}`.
- `data/agency_links.json`: seed(수기·증거) + 공식 1:1 연쇄 파생(`rule`).
  경계 수준 1:다는 링크 없이 `(구)` 보존한다.
- `data/agency_institutions.json`: 경계 코드 → 통계 기관 ID.
- `vectors/resolve_cases.json`: agency 5종(개명·승계·1:다·미확정·코드 없음 이름만)
  + region. 세 리더가 같은 파일로 통과해야 한다.

통계 묶음 기준은 이름이 아니라 `agency_stat_key`다(PC·모바일 표·지도 팝업 공통).
원문 `처리기관`은 덮어쓰지 않는다. 저장된 사실의 파생값 재계산은
MAP `supabase/migrations/2026-09-29*` + `scripts/recompute-agency-keys.mjs`
(dry-run 먼저), PC·모바일은 조회 시 재계산이라 재크롤링이 필요 없다.

## 규칙

- 변경이 없으면 버전·파일·commit을 만들지 않는다(빌더는 결정적이라 재실행이 no-op이다).
- 검증 전 `latest` 취급 파일을 덮어쓰지 않는다. 손상 파일·순환·hash 불일치·schema
  비호환은 새 배포를 막고 마지막 정상본을 유지한다. 정상 분할·이전코드 누락은
  해당 연결만 미해결로 남긴다.
- 한 곳이 실패하면 성공/실패 상태를 보존하고 재실행 가능하게 한다(전역 트랜잭션 가정 금지).
- 호환 reader/수신기를 먼저 준비한 뒤 같은 버전의 데이터로 전환한다.
- 개인정보 원문은 로그에 노출하지 않는다. 비밀을 로그·커밋에 넣지 않는다.
- 정기 실행은 설정하지 않는다(명시적 운영 설정이 있을 때만 연결).
