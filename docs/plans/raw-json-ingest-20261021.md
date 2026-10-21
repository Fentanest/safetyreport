# 원본 JSON 일괄 전송·서버 파싱 계획 (2026-10-21)

상태: **로컬 구현 진행 중 — 미완료 항목은 SOL-REPORT.md 참조**. 대상: 지도(safetyreport-community-map), PC(safetyreport), 모바일(safetyreport-mobile),
동의(safetyreport-community-auth) 네 레포. 이 문서가 정본이고, 구현을 시작할 때 PC·모바일 `docs/plans/` 에 같은 문서를 둔다.
PROJECT_RULES "작업 범위"에 따라 upstream 레포 수정, 동의 정책 발급, 206 적용, 릴리스는 단계마다 사용자 승인을 따로 받는다.

## 0. 사용자 결정 (2026-10-21 인터뷰)

| 항목 | 결정 |
|---|---|
| 전송 내용 | 파싱한 DTO 대신 **상세 원본 JSON 전부**(로컬 저장본 그대로 — 신고자 키 5개만 지운 상태). 새 동의문 + 전원 재동의 |
| 원문 용도 | 지금 지도에 공개되는 항목 외에는 완전 비공개. 본인 신고의 답변 원문 열람(크롬 확장)은 **후속 계획** |
| 서버 파싱 | 206에 **Python 파서 워커**를 두고 PC 파서 모듈을 그대로(복사본+해시 검사) 쓴다 |
| DB화 범위 | PC·모바일처럼 신고 내용·답변 내용 등 파싱 가능한 정보 전부 |
| 같은 신고 교체 | 같은 신고(C_NO)를 다시 받으면 교체. 다른 계정·내용이 같은 다른 신고는 대상 아님 |
| 원본에 없는 값 | 별점·별점사유·사진 촬영시각·목록 값은 **보조 필드**로 함께 보낸다 |
| 전송 대상 | 답변 완료 신고만(현행 유지) |
| 원본 없는 신고 | 이미 받은 DTO 행을 두고, 원본이 오면 교체하는 **과도기** |
| 과도기 검증 | v2 이벤트에 클라이언트 DTO도 함께 실어 서버 파싱 결과와 비교·기록 |
| 1회 전송 전 누락 채우기 | ① 원본 없거나 손상된 신고 재수집 ② 보조 필드 누락 재수집 ③ 로컬에 없는 신고 수집 |
| 모바일 1회 전송 망 | 모든 망(Wi-Fi 한정 없음) |
| 원본 보관 | 동의 철회 때까지(기존과 같음). 동의 철회 시 앱 사용 불가 |
| 구버전 | v1 DTO 경로 병행 후 기한 차단. **차단 날짜 미정**. 구버전 사용자에게 업데이트 안내 |
| 담당 공무원 연락처 | 처리기관 대표 전화번호라 개인정보가 아닌 공공정보로 본다 |
| 동의 철회 | 앱을 처음 상태(1단계 카카오 로그인)로 되돌리는 **소프트 리셋**. 로컬 신고 DB 는 지우지 않고 잠근다 |
| 소프트 리셋 대상 | 이 기기 철회, 다른 기기·지도 웹 철회, 카카오 쪽 앱 연결 끊기·탈퇴, 서버의 기여자 계정 삭제 |
| 서버 확인 불가 시 | 마지막 정상 확인 후 **3일**까지만 로컬 사용 허용 |
| 동의문 변경(outdated) | 재동의 전까지 앱 잠금(새 동의문 화면). 카카오 로그인·로컬 DB 는 유지 |
| 카카오 연결 끊기·탈퇴 (2026-10-21 검수 후) | 카카오 "연결 해제 웹훅"을 auth `community-kakao-unlink` 가 받아 자동 철회(원본 삭제). 지금 동의문(09-28.3)에 이 처리가 없으므로 **새 정책 전환과 같은 때 켠다**. 카카오 콘솔 등록(POST, 대표 어드민 키)·206 배포 전까지 BLOCKED. 앱은 `consent: revoked`(G6)로 리셋된다 |
| 재동의 후 재공개 (2026-10-21 검수 후) | 철회 뒤 같은 계정 재동의 → 답변 완료 신고 전체를 다시 보내 지도에 다시 공개. 동의문에 명시(옛 "자동 재공개 안 함" 약속 대체) |
| outdated 사용자의 지도 열람 (2026-10-21 검수 후) | 지금처럼 허용하고 대시보드에 업데이트·재동의 배너 |
| 공무원·보완 요청 담당자 정보 (2026-10-21 검수 후) | 이름·업무 전화·업무 이메일을 그대로 나열하고 공공 업무정보로 표기 |

## 1. 현황 (2026-10-21 확인)

### 1-1. 지금 전송
- 계약 `community-ingest-v1`, `protocol: 1`, payload observation-v5(17키 + 선택 `fine_estimate`). PC `pc-parser-13`, 모바일 `mobile-parser-13`.
- PC `services/community_capture.py:303-338`, 모바일 `lib/community/capture/observation_rules.dart:395-428` 이 상세 원본을 파싱해 DTO 를 만들고
  journal 에 고정한 뒤 실시간·수동·00:00 KST 에 보낸다.
- 서버 [handler.ts](../../../server/ingest/handler.ts): 본문 256KiB(`MAX_BODY`, :23), 요청당 이벤트 20개, 사용자·연결 버킷 각 60회/분,
  `protocol !== 1` 거절(:191), payload 키 정확 일치. `client_version`·`parser_version` 은 형식만 보고 **저장하지 않는다**.
- 지도 fact 는 `private.community_report_facts`, 키 `(contributor_id, dataset_key, source_report_key)`. 다른 dataset 의 같은 신고는 `report_owned_elsewhere`.

### 1-2. 클라이언트 원본 저장
- PC `mysafety_report_detail_raw`(3.4.0), 모바일 `report_detail_raw`(2.3.0). 열 `ID, raw_zlib, raw_sha256, raw_size, fetched_at, source`.
  상세 API envelope 전체를 저장하고 `result` 의 `C_NAME, C_EMAIL, C_PHONE, C_PHONE2, C_ID` 만 지운다.
  정규 직렬화(공백 없는 JSON, UTF-8)와 sha256 은 PC·모바일이 바이트까지 같다(`contracts/raw-detail-vectors.json`).
- DB 최적화(`services/db_optimize.py`, `lib/services/db_optimize.dart`)가 원본을 채우고 다시 파싱한다. 완료 표시 `sync_meta.detail_raw_optimize`
  (`target`=파서 세대, `done`, `holds`). `done=true` 여도 404/410·파싱 실패·새 활동 이관 건은 원본이 없을 수 있다.
- 원본 밖 입력: 목록 API 값, 만족도 API 별점·별점사유(`core/crawler/detail_pipeline.py:84`), 사진 EXIF 촬영시각
  (`사진_첫촬영/끝촬영/촬영수`, `services/maintenance_service.py:82`), 모바일 `ssnpc_cd/ssnpc_type`.

### 1-3. 실측 (205 PC 서버, 사용자 본인 신고, 읽기 전용)
- 스키마 7, `detail_raw_optimize` = `done: true`, `pc-parser-13`, 3,137건 전부 refetch, skipped 0, holds 0.
- 신고 3,143건 / 원본 3,143건 / **원본 없는 신고 0건**.
- 원본 크기(압축 전): 평균 7.8KB, p50 7.7KB, p90 9.3KB, p99 11.1KB, 최대 14.5KB. 합계 24.5MB(zlib 9.9MB).
- 운영 지도 신고 약 13.9만 건(2026-10-18) 기준 원본 합계 추정 약 1.1GB(압축 전).

### 1-4. 업데이트 안내 수단
- PC: 사이드바 GitHub 최신 릴리스 비교 표시(`web/static/ui/shell-polling.js:124-143`), 바이너리 기동 시 자동 업데이트(`core/utils/updater.py`).
- 모바일 Standalone: 앱 업데이트 확인 없음.
- 이미 배포된 구버전은 모르는 ingest 오류 코드를 `server_busy` 로 보고 조용히 재시도한다(`services/community_upload_policy.py:246`).
- 구버전은 동의가 `outdated` 여도 로컬 사용을 계속하고(1-5), 새 동의문을 자동으로 띄우지 않는다. 지도 탭 업로드 패널에
  "공유 동의 확인 필요" 한 줄만 보인다(PC `web/templates/report_map.html:486`, 모바일 `lib/widgets/community_upload_panel.dart:318`).
  새 동의문은 사용자가 설정의 동의 카드를 열 때만 보인다(`account-api.md:40-43`).
- **구버전에 확실히 닿는 경로는 지도 웹이다.** 지도 열람은 활성 동의가 필요하므로(PROJECT_RULES 고정 요구 7), 동의가 outdated 인
  사용자는 지도 웹에서 반드시 안내 화면을 거친다.

### 1-5. 게이트 실제 동작 (PC 3.5.1 / 모바일 2.4.1)
`can_enter`=업로드 권한, `can_local`·`canBrowse`=로컬 사용 권한. 3.0.0.3(2026-10-07) 긴급 정책이 동의 상태를 로컬 사용 조건에서 뺐다
(PC `services/community_gate.py:220-226`, 모바일 `lib/community/gate/community_gate.dart:261-264`).

| 경우 | PC | 모바일 |
|---|---|---|
| 서버 확인 불가 + 같은 주인 기존 사용자 | 로컬 허용(무기한) | 로컬 허용(무기한) |
| 서버가 `revoked` 확인 | **로컬 허용** | **로컬 허용** |
| 카카오 로그인만 하고 동의한 적 없는 신규 사용자 | **로컬 허용**(동의 전에도 DB 주인을 적음, `community_gate.py:294-296`) | 차단(주인은 게이트 통과 뒤 적음) |
| 정지·다른 계정 DB·공식 계정 불일치 | 차단 | 차단 |

- PC 는 2026-10-21 기존 시험 장치로 실행해 확인했다: 동의 없음·서버 철회 모두 `{state: consent_required, can_enter: False, can_local: True}`.
  모바일은 `test/community/emergency_offline_test.dart:452-487` 이 none·revoked 의 로컬 허용을 고정한다.
- 철회 버튼은 온보딩으로 보내지만, PC 온보딩은 `can_browse` 면 원래 화면으로 돌려보내고(`community_onboarding_route.py:61-62`),
  모바일은 철회 직후 `_prepareLocalUse` 가 로컬 사용을 다시 켠다. 철회 확인 문구("즉시 필수 설정 화면으로 돌아가고…")와 실제 동작이 다르다.
- 긴급 정책의 원래 목적은 "로그인했던 사용자를 상태 확인 불가 때문에 가두지 않기"였다(사용자 확인). 서버가 명확히 거절한 경우까지
  허용하는 것은 목적보다 넓다 → 7-4 에서 바로잡는다.

## 2. 목표 구조

```
PC·모바일 ──v2 이벤트(원본 문자열 + 보조 필드 + 클라이언트 DTO)──▶ Edge community-ingest
                                                                     │ 인증·동의·해시 검사, 원본 저장(파싱 안 함)
                                                                     ▼
                                                        private.community_report_raw (+ 파싱 대기열)
                                                                     │
                                       206 community-parser 워커(Python, PC 파서 복사본)
                                                                     │ 파싱 → 전체 열 저장 → 지도 fact 투영 → DTO 비교
                                                                     ▼
                     private.community_report_parsed · community_report_facts(기존) · community_parse_diffs
                                                                     │
                                                기존 트리거·화면 캐시·rollup → 지도
```

- Edge 는 원본을 **파싱하지 않는다**. 검증하고 저장한 뒤 바로 ACK 한다(`stored`). 지도 반영은 워커가 수 초 안에 한다.
- 지도 집계 파이프라인(facts 트리거, `screen_fact_norm`, 화면 캐시, rollup)은 그대로 둔다. 워커는 facts 에 지금과 같은 열을 쓴다.
- 파서는 PC Python 이 정본이다. 워커는 그 복사본을 쓰고, 모바일 Dart 는 지금처럼 같은 벡터로 맞춘다. TS 파서는 만들지 않는다.

## 3. 계약 v2 (`contracts/community-ingest-v2/`)

v1 파일은 그대로 두고 새 디렉터리를 만든다. 세 레포 사본과 `MANIFEST.sha256` 규칙은 v1 과 같다.

### 3-1. envelope
- `protocol: 2`, `contract: "community-ingest-v2"`. 나머지 키(`source_app, source_mode, connection_id, consent_grant_id, policy_version,
  client_version, parser_version, trigger, events`)는 v1 과 같다. `trigger` 에 `raw_backfill` 을 더한다.
- 요청 본문은 `Content-Encoding: gzip` 을 허용한다. 한도: 압축 해제 후 1MiB, 전송 바이트 512KiB, 이벤트 20개 이하.
  206 앞단 envoy 는 `per_connection_buffer_limit_bytes: 32768`(흐름 제어용, 본문 상한 아님)이다. P1 에서 1MiB 요청을 206 에 실제로 보내 확인한다.
  클라이언트는 **건수와 바이트를 함께** 보고 묶는다(원본 최대 14.5KB × 20 = 290KB 라 256KiB 를 넘을 수 있다).

### 3-2. 이벤트
v1 이벤트 키(`event_id, event_type, source_system, source_report_id, report_number, source_revision, writer_epoch, captured_at`)에 더해:

| 키 | 내용 |
|---|---|
| `raw.body` | 정규 직렬화 바이트를 그대로 담은 **문자열**. 서버가 다시 직렬화하지 않는다(키 순서·숫자 표기 보존) |
| `raw.sha256` | `raw.body` UTF-8 바이트의 sha256. 로컬 `raw_sha256` 과 같은 값 |
| `raw.size`, `raw.fetched_at`, `raw.source` | 로컬 원본 표 값 |
| `supplement` | `{rating, rating_cause, survey_state, photo_first_at, photo_last_at, photo_count, list:{title, report_date, status, entry_value, ssnpc_cd, ssnpc_type}}`. 모르는 값은 키를 빼지 말고 `null` |
| `supplement_sha256` | v1 canonical JSON 규칙으로 만든 해시 |
| `client_observation` | 지금의 v5 DTO(과도기 비교용, 선택). 구버전 차단 때 함께 뺀다 |
| `client_observation_sha256` | v1 `payload_sha256` 과 같은 규칙 |

- 서버는 `raw.sha256`·`supplement_sha256`·`client_observation_sha256` 을 다시 계산해 다르면 422(`raw_hash_mismatch` 등)로 거절한다.
- `raw.body` 가 JSON 객체가 아니거나 `result` 가 객체가 아니면 `raw_invalid`. 신고자 키 5개가 `result` 에 남아 있으면 `raw_reporter_keys_present`(저장하지 않음).
- 멱등성: 같은 `event_id` 는 v1 과 같은 불변 필드 비교. 같은 신고에 `raw.sha256`·`supplement_sha256` 이 저장본과 같으면 `no_change`.

### 3-3. ACK
- v1 ACK 형식을 유지하고 `projection_status` 에 `parse_pending` 을 더한다(클라이언트 문구: "중앙 저장 완료·지도 반영 대기").
- 클라이언트는 `stored` 를 받으면 전송 완료로 본다. 파싱 결과는 다음 manifest·status 로 알 수 있다.

### 3-4. 안내 채널 (`client_notice`)
- `account-api` status 응답과 v2 ACK 에 `client_notice: {level, message, min_version:{pc, mobile}, url:{pc, mobile}, deadline}` 를 더한다.
- 새 클라이언트는 이 값이 있으면 앱 안 배너(필수면 대화상자)로 보인다. 다음 차단·공지는 앱 배포 없이 이 채널로 보낸다.
- 새 오류 코드 `client_upgrade_required`(426)를 정의하고 새 클라이언트가 알게 한다.

## 4. 서버 저장 모델 (지도 레포 migration)

| 표 | 키 | 주요 열 |
|---|---|---|
| `private.community_report_raw` | `(contributor_id, dataset_key, source_report_key)` | `raw_text text`(정규 직렬화 그대로), `raw_sha256`, `raw_size`, `fetched_at`, `raw_source`, `supplement jsonb`, `client_observation jsonb`, `writer_epoch`, `source_revision`, `received_at`, `parse_state`(`pending`/`parsed`/`failed`), `parsed_parser_version` |
| `private.community_report_parsed` | 같은 키 | PC `mysafety_report` 열 전체를 영문 snake_case 로(신고명·신고일·처리상태·차량번호·위반법규·범칙금_과태료·벌점·처리기관·처리기관코드·담당자·답변일·발생일자·발생시각·위반장소·위도·경도·종결여부·**신고내용·처리내용**·첨부 메타·보완 6열·사진 촬영 3열·category·entry_value·ssnpc), `parser_version`, `parsed_at` |
| `private.community_parse_diffs` | `(키, parser_version)` | 서버 DTO 와 `client_observation` 의 키별 차이, 클라이언트 파서 버전, 기록 시각 |
| `private.community_parse_queue` | `(키)` | 우선순위(`ingest`/`reparse`), 시도 횟수, 다음 시도 시각 |

- 원본은 `jsonb` 가 아니라 `text` 로 둔다. 바이트를 보존하고 sha 를 다시 확인할 수 있으며, jsonb 변환 비용도 없다(TOAST 압축 사용).
- `community_report_facts` 에 `fact_source`(`dto`/`raw`), `fact_parser_version`, `raw_sha256` 열을 더한다.
- 연결 표에 `last_contract`, `last_client_version`, `last_parser_version`, `last_seen_at` 을 더하고 **v1·v2 요청 모두에서** 갱신한다(구버전 식별용).
- 권한: 새 표는 `private` 스키마에만 두고 anon·authenticated 에 아무 권한도 주지 않는다. 워커 전용 DB 역할 `community_parser` 를 만든다
  (대기열·원본 SELECT, parsed·diffs·queue 쓰기, 투영 함수 EXECUTE만. service_role 미사용).
- 공개 경로(공개 API, 화면 캐시, rollup, CSV, 지도 JSON)는 `community_report_raw`·`community_report_parsed` 를 읽지 않는다. 이를 시험으로 고정한다.
- 보관: 원본·파싱 행은 동의 철회 또는 계정 삭제까지 둔다. 기존 삭제 fence·tombstone 경로가 새 표도 함께 지운다.
  지금 철회(auth `internal_account_revoke_consent`)는 grant 에 `revoked_at` 만 적고, 지도는 화면 쿼리에서 철회된 grant 의 fact 를 빼서
  내린다(fact 행은 남음). 원본·파싱 행은 철회 시 **실제로 지운다**: 지도 migration 에 `community_consent_grants.revoked_at` 갱신 트리거
  (또는 auth 함수 호출 뒤 지도 정리 함수)를 둔다. fact 처리 방식은 지금 그대로 둔다.
- 이벤트 원장: v2 원장에는 원본을 넣지 않는다(메타와 해시만). 동의문이 약속한 원장 30일 정리를 이번에 구현한다(지금은 없음).

## 5. 같은 신고 교체 규칙

- 키는 v1 과 같다. 다른 dataset 의 같은 신고는 지금처럼 `report_owned_elsewhere` 다.
- 순서는 `(writer_epoch, source_revision)` 비교다. 큰 쪽이 이긴다. **같으면 원본(`raw`)이 DTO(`dto`)를 이긴다.**
- 원본이 들어오면 원본 행을 upsert 하고 파싱 대기열에 넣는다. 파싱이 끝나면 fact 를 원본 결과로 바꾸고 `fact_source='raw'`.
- 원본이 없는 신고는 DTO fact 를 그대로 둔다(과도기). 나중에 원본이 오면 위 규칙으로 바뀐다.
- 병행 기간에 v1 DTO 가 더 큰 revision 으로 오면 fact 는 DTO 로 갱신하고(최신 처리상태가 우선) 원본 행은 둔다. 그 클라이언트가 v2 로
  올라와 원본을 다시 보내면 원본 결과로 돌아온다.
- 파싱 실패: 이전 fact 를 그대로 두고 `parse_state='failed'` 와 오류를 남긴다. 조용히 DTO 를 지우지 않는다.

## 6. 206 파서 워커

- 위치: 지도 레포 `server/parser-py/`. Python 3.12, 표준 라이브러리 + psycopg. 206 에 컨테이너 하나로 띄운다(Coolify 또는 supabase compose 옆).
- 파서 복사본: PC `services/parser.py, report_category.py, fine_amount_syntax.py, case_fine_supplement.py, law_values.py, law_citations.py,
  law_selection.py, fine_estimate.py, plate_normalize.py` 와 `community_capture.py` 의 DTO 생성부(`build_adapter_input`·`build_payload`)를
  복사한다. 이 모듈들은 표준 라이브러리만 쓴다(`community_capture.py` 의 `core.utils.fallback` 만 작은 대체 함수로 바꾼다).
  `server/parser-py/MANIFEST.sha256` 에 PC 원본 커밋과 파일 해시를 적고, CI 가 PC 계약 벡터(`parser-vectors`, `law-parser-vectors`,
  `explicit-fine-vectors`, `case-fine-supplement-vectors`, `raw-detail-vectors`, `report-categories.json`)를 돌린다.
- 기관 registry 는 `shared/agency-region-registry` 의 `resolve.py`(정본)를 쓴다.
- 처리 순서(한 건):
  1. `FOR UPDATE SKIP LOCKED` 로 대기열에서 최대 200건을 가져온다.
  2. `raw_text` sha 를 확인하고 `result` 를 파싱한다(PC `parse_json_details` + `supplement` 의 목록·별점·사진 값).
  3. 한 트랜잭션에서 `community_report_parsed` upsert → DTO 생성 → 투영 함수(아래) 호출 → `client_observation` 과 비교해 diff 기록.
- 투영 함수 `private.project_raw_fact(...)`: `internal_community_ingest` 의 fact upsert 부분(검증·`deriveFact`·registry 파생)을 SQL 로 떼어
  v1·v2 가 함께 쓴다. v1 Edge 경로의 결과가 바뀌지 않음을 기존 벡터로 확인한다.
- 재파싱: 복사본 `PARSER_VERSION` 이 바뀌면 `parsed_parser_version` 이 다른 행을 `reparse` 우선순위로 대기열에 넣는다.
  화면 캐시 무효화가 몰리지 않게 배치 사이에 쉰다. 13.9만 건 기준 파싱 자체는 수 분 수준으로 예상한다(실측으로 확정).
- 로그에 원본·본문·답변을 찍지 않는다. 오류는 키·파서 버전·예외 종류만 남긴다.

## 7. 클라이언트 (PC·모바일 같은 규칙, 호출 지점 1:1)

버전 가정: PC 3.6.0, 모바일 2.5.0(확정은 릴리스 때).

### 7-1. 전송 전환 조건
- 새 동의(v2 정책) grant 가 active 이고, 서버 status 가 v2 를 받는다고 알릴 때만 v2 로 보낸다. 그 전에는 지금처럼 v1 을 보낸다.
- 전환 뒤 실시간·수동·00:00 KST 전송은 모두 v2 이벤트다. 이벤트 생성 조건은 "답변 완료 상태 + (`raw_sha256` 또는 `supplement_sha256`
  또는 DTO 해시)가 직전 journal 과 다름"이다.

### 7-2. 1회 원본 일괄 전송(`raw_backfill`)
완료 표시: `community.db meta.raw_backfill` = `{contract:"v2", parser_target, local_dataset_id, state, cursor, holds, stats}`.
`local_dataset_id` 가 바뀌면(복원·리셋) 다시 돈다. 커서로 이어 하고, 실패는 기존 DB 최적화처럼 3회 뒤 holds, 로그인 문제·연속 실패는 쉰다.

1. **선행**: DB 최적화가 현재 파서 세대로 `done` 이 아니면 그것부터 끝낸다(이미 있는 작업을 기다린다).
2. **누락 채우기 A — 로컬에 없는 신고**: 목록 API 전 페이지를 받아(`crawl_titles` 의 `list_ok` 전 페이지 성공일 때만 판정) 로컬에 없는 ID 를
   일반 상세 수집 대기열에 넣는다. 그 수집이 원본 저장·파싱·capture 를 한다. 페이지 실패가 있으면 이 단계를 끝내지 않고 다음에 다시 한다.
3. **누락 채우기 B — 원본 없음·손상**: 답변 완료 신고 중 원본이 없거나 sha 가 안 맞는 건은 상세를 다시 받아 원본을 저장하고 다시 파싱한다
   (`source='raw_backfill'`, DB 최적화의 단건 처리 함수 재사용). 404/410 은 `raw_unavailable` 로 기록하고 건너뛴다(서버는 DTO 유지).
4. **누락 채우기 C — 보조 필드**: 만족도 조사를 마친 신고인데 별점·별점사유가 비면 만족도 API 로 채운다. 첨부 사진이 있는데
   촬영시각이 비면 사진 백필로 채운다. 첨부 보관기간(약 6개월)이 지나 못 받는 건은 비운 채 기록한다.
5. **전송**: 답변 완료 신고 전체를 v2 이벤트로 만들어 outbox 에 `trigger='raw_backfill'` 로 넣는다. 업로더는 바이트 기준으로 묶고 gzip 으로 보낸다.
   모든 이벤트가 `stored` ACK 를 받으면 `state='done'`.
6. 진행 표시: 지도 탭 카드에 "원본 전송 n / N" 과 누락 채우기 결과(채움·못 채움 건수)를 보인다.

- 안전신문고 요청은 기존 수집 간격과 로그인 처리를 따른다. 일반 크롤·DB 최적화와 동시에 돌지 않게 기존 잠금을 쓴다.
- 모바일: 포그라운드와 WorkManager 모두에서 이어 한다. 망 제한 없음(사용자 결정). Client 모드는 연결된 PC 서버가 보낸다(지금과 같음).
- 205 기준 규모: 3,143건, 압축 전 24.5MB(gzip 시 10MB 안팎), 20건 요청 약 160회.

### 7-3. 업데이트 안내
- 새 클라이언트: `client_notice`·`client_upgrade_required` 를 표시한다(PC 웹 상단 배너, 모바일 대화상자 + 스토어 링크).
- 모바일 Play 인앱 업데이트(`in_app_update`) 도입은 선택 항목으로 둔다.

### 7-4. 게이트 정리 (사용자 결정 2026-10-21)
원칙: **동의가 없으면 앱을 쓸 수 없다.** 예외는 "예전에 정상 확인된 사용자가 지금 서버 상태를 확인할 수 없을 때"뿐이고 3일로 제한한다.
PC·모바일 같은 규칙, 같은 판정 벡터(`contracts/community-ingest-v2/gate-vectors.json` 신설)를 둔다.

| 경우 | 판정 근거 | 결과 |
|---|---|---|
| G1 정상 | 서버 응답: 세션·카카오·동의 active·기여자 active | 사용 + 업로드 |
| G2 확인 불가, 유예 안 | 네트워크 없음·timeout·5xx·쿨다운, 같은 카카오 회원·같은 DB 주인으로 마지막 G1 이 72시간 이내 | 로컬 사용, 업로드 보류, "서버 확인 대기" 배너 |
| G3 확인 불가, 유예 지남 또는 기록 없음 | G2 조건 중 하나라도 불충족(신규 설치·초기화 직후 포함) | "연결 확인 필요" 화면(재시도·로그아웃만) |
| G4 동의한 적 없음 | 서버 `consent: none` | 온보딩 2단계(동의) 잠금. 로그인 유지 |
| G5 동의문 변경 | 서버 `consent: outdated` | 새 동의문 화면 잠금. 로그인·로컬 DB 유지 |
| G6 철회 | 서버 `consent: revoked`(이 기기·다른 기기·지도 웹 모두) | **소프트 리셋** |
| G7 카카오 연결 끊김·탈퇴 | 서버가 카카오 연결 없음 확인 | **소프트 리셋** |
| G8 기여자 계정 삭제 | 서버가 기여자 없음 확인 | **소프트 리셋** |
| G9 정지 | 서버 `contributor: suspended` | 정지 안내 화면(기존). 리셋하지 않음 |
| G10 카카오 세션 만료 | 토큰 갱신 거절 | 카카오 재로그인 화면(기존 `kakao_reauth_required`). 같은 계정이면 DB 유지 |
| G11 DB 주인·공식 계정 불일치, 계정 변경 중 | 기존 판정 | 기존 화면 유지 |

- **72시간 기준**: 마지막 G1 시각을 PC `data/auth/community_consent.enc`, 모바일 보안 저장소의 동의 이력에 남긴다. 기기 시계가 그 시각보다
  이르면(시계 되돌림) 유예가 지난 것으로 본다.
- **PC 동의 전 주인 기록 제거**: PC 도 모바일처럼 G1 을 통과한 뒤에만 DB 주인을 적는다. 동의 없이 수집하는 경로가 없어지므로
  `test_no_consent_capture_keeps_owner_and_waits_for_real_grant` 등 긴급 정책 시험은 새 규칙으로 바꾼다.
- **소프트 리셋 절차**:
  1. 로컬 전송 거부를 먼저 기록한다(지금도 있음). 업로드·백그라운드 작업을 멈춘다.
  2. 이 기기에서 철회한 경우 서버 철회를 호출한다. 실패하면 카카오 로그아웃을 하지 않고(철회에 토큰이 필요) "철회 처리 중" 화면에서
     잠근 채 다시 시도한다. G6(원격)·G7·G8 은 서버가 이미 확인했으므로 이 단계를 건너뛴다.
     철회를 요청할 때 그 카카오 세션의 식별(PC `connected_at`, 모바일 세션 확정 시각)을 `revoke_pending` 에 함께 적어 둔다.
  3. 카카오 세션(원격 세션 해제 시도 + 로컬 current/pending/reauth. **단, 철회 대기 중 새로 로그인한 세션이면 끊지 않는다** — 5단계), writer 연결, `community.db` 활성 문맥, 게이트 캐시를 지운다.
     대기 중 outbox 는 `blocked` 로 바꾼다. `raw_backfill` 완료 표시도 지운다(서버 원본이 철회로 지워지므로 재동의 뒤 다시 보낸다).
  4. 지우지 않는 것: 개인 신고 DB(주인 표시 그대로, 잠금), 로컬 원본 표, journal, 동의 이력·정지 기록.
  5. 1단계 카카오 로그인 화면으로 간다. 단, 철회 요청 뒤 새로 한 카카오 로그인(요청 때 적어 둔 세션 식별과 다른 세션)으로 철회가
     끝난 경우는 그 세션을 유지하고 바로 동의 화면(G4)으로 간다(로그인은 한 번만). 철회 요청 때의 원래 세션으로 끝나면 위처럼
     로그아웃하고 카카오 로그인 단계로 간다. 같은 카카오 계정으로 로그인하고 동의하면 DB 를 그대로 쓰고 원본 1회 전송을 다시 한다.
     다른 계정이면 기존 `db_owner_mismatch` 흐름(자료를 지우고 시작)을 탄다.
- 철회 확인 대화상자 문구를 실제 동작에 맞춘다: "철회하면 지도 서비스에 보낸 원본이 삭제되고, 앱은 카카오 로그인부터 다시 시작합니다.
  이 기기의 신고 자료는 남아 있으며 같은 카카오 계정으로 다시 동의하면 이어서 쓸 수 있습니다."
- 모바일 Client 모드: 연결된 PC 서버의 게이트를 따른다. PC 가 리셋되면 Client 는 "서버 동의 필요" 안내와 함께 잠긴다.
- G7·G8 판정은 지금 status 계약으로 가능하다(`account-api.md:20-33`): G7 = `gate.kakao:false`(reason `kakao_missing`),
  G8 = `contributor.status:"none"`. 둘 다 "이 기기에 같은 카카오 회원의 G1 기록이 있을 때"만 리셋으로 본다(신규 사용자와 구분).
- 지금 동의문(`share-consent-2026-09-28.3.md:42`)은 이미 "철회하면 앱은 동의 화면으로 돌아갑니다. 다시 동의하기 전까지는 앱의 일반 기능을
  이용할 수 없습니다"라고 약속한다. 현재 클라이언트(1-5)는 이 약속과 다르게 동작하므로 7-4 는 약속을 지키는 수정이기도 하다.
- 이 정리는 새 클라이언트(PC 3.6.0, 모바일 2.5.0)부터 적용된다. 구버전은 v1 차단 때까지 지금의 넓은 허용이 남는다.

## 8. 동의문 새 버전 (safetyreport-community-auth)

- 새 정책 버전을 발급한다(`private.community_policy_texts` 에 본문, `community_policy_current` 전환). 기존 grant 는 모두 `outdated` 가 되어 재동의한다.
- 본문 **첫머리**에 업데이트 안내를 넣는다: "PC 3.6.0 / 모바일 2.5.0 이상으로 업데이트해 주세요." 다만 구버전은 이 본문을 자동으로
  띄우지 않으므로(1-4) 보조 경로일 뿐이다. 주 경로는 지도 웹 안내 화면이다(9절).
- 담을 내용:
  - 보내는 것: 안전신문고 상세 원본 전체(신고 글, 처리 답변 원문, 첨부·사진 파일 주소와 이름, 담당 공무원 이름·연락처, 보완 요청·의견,
    위치), 별점·별점사유, 사진 촬영시각.
  - 보내지 않는 것: 신고자 이름·이메일·전화·아이디(기기에서 지움), 사진 파일 자체, 안전신문고 로그인 정보.
  - 공개 범위: 지도에는 지금과 같은 항목만. 원문은 비공개(본인 열람 기능은 추가될 때 따로 안내).
  - 보관: 동의 철회 때까지. 철회하면 지도 서비스의 원본이 삭제되고, 앱은 카카오 로그인부터 다시 시작한다(7-4 소프트 리셋).
  - 동의가 없거나 동의문이 바뀐 뒤 재동의하지 않으면 앱을 쓸 수 없다. 서버 확인이 안 될 때는 마지막 확인 후 3일까지만 쓸 수 있다.
- 담당 공무원 연락처는 처리기관 대표 전화번호로, 개인정보가 아닌 공공정보로 본다(2026-10-21 사용자 판단).
- 지금은 동의 철회·미동의 뒤에도 로컬 사용이 되므로(1-5) 새 클라이언트에서 7-4 로 맞춘다.

## 9. 구버전 대응

- 병행: v1 경로는 차단일까지 그대로 받는다. 서버는 v1 요청에서도 `client_version` 을 연결 표에 남긴다.
- 안내 경로(강한 순서):
  1. **지도 웹 대시보드 배너**: (2026-10-21 검수 정정) 지도 열람 판정은 철회되지 않은 grant 면 정책 버전과 무관하게 통과시키므로
     outdated 사용자는 안내 화면을 거치지 않는다(사용자 결정으로 열람은 계속 허용). 대신 지도 자체 API
     `my-analytics/client-status`(지도 로그인 토큰, SQL `internal_map_client_state`)가 consent·마지막 계약·v2 여부·서버 notice 를 주고,
     notice 가 있고 v2 가 켜져 있으며 (outdated 이거나 v1 연결이 있을) 때만 대시보드에 배너를 띄운다. 지도 웹은 community-account 를
     직접 부르지 않는다(PROJECT_RULES 7, CORS 열지 않음). 버전 문구는 서버 notice 가 정본이며 화면에 하드코딩하지 않는다.
  2. 구버전 앱의 지도 탭 업로드 패널 "공유 동의 확인 필요"(이미 있음, 자동 표시).
  3. 새 동의문 첫머리 문구(사용자가 동의 카드를 열 때만 보임).
  4. 새 클라이언트부터는 `client_notice`.
- 구버전 사용자가 동의 카드에서 새 동의를 하면 v1 으로 계속 보낸다(병행 기간). 서버는 그 grant 로 v1 을 받는다.
- 차단: 날짜 미정. 공유 DB 설정 `private.community_ingest_config.v1_sunset` 이후 v1 을 거절한다. 구버전은 거절을 조용한 재시도로 보므로
  차단 직전에 동의문 안내 문구를 한 번 더 확인한다.
- 차단 때 `client_observation` 비교도 끝낸다(diff 기록이 충분히 0 에 수렴했는지 확인한 뒤).

## 10. 단계와 승인

| 단계 | 레포 | 내용 | 승인 |
|---|---|---|---|
| P1 | 지도 | 계약 v2 문서·스키마·벡터, migration(4절), Edge v2 경로(플래그 기본 꺼짐), 연결 버전 기록, 원장 30일 정리, 공개 경로 차단 시험 | 로컬 구현 허용. 206 적용은 승인 |
| P2 | 지도 | 파서 워커·복사본 MANIFEST·벡터 CI·투영 함수 분리·재파싱 작업·컨테이너 | 로컬 허용. 206 배포는 승인 |
| P3 | auth | 새 동의문 초안 → 사용자 검토 → 정책 발급. `community-account` status 에 `ingest:{contracts:[1,2]}`(v2 수신 가능 신호, 7-1)와 `client_notice`(3-4) 추가 | 문구·발급·배포 모두 승인 |
| P1b | 지도 | 지도 웹 안내 화면에 업데이트 안내(9절), outdated·v1 기여자 배너, 실제 브라우저 증거 | 로컬 허용. Pages 배포는 승인 |
| P4 | PC | v2 이벤트 생성, `raw_backfill`(누락 A·B·C 포함), 바이트 묶기·gzip, `client_notice` 표시, 게이트 정리(7-4)·소프트 리셋, 시험 | upstream 수정 승인 |
| P5 | 모바일 | P4 와 1:1, v2 이벤트 바이트 벡터 공유 | upstream 수정 승인 |
| P6 | 운영 | 206 migration → 워커(대기) → Edge v2 켜기 → 클라이언트 릴리스 → 정책 전환 | 각 단계 승인 |
| P7 | 검증 | 205(사용자 본인 3,143건)를 첫 실제 일괄 전송으로 사용, 결과 보고 | — |

순서 이유: 새 클라이언트는 새 동의가 active 일 때만 v2 를 보내므로, 서버·클라이언트를 먼저 깔고 정책을 마지막에 바꾸면 중간 상태에서도
전송이 끊기지 않는다(정책 전환 전에는 새 클라이언트도 v1 을 보낸다).

## 11. 완료 기준

- 계약 v2 벡터를 PC·모바일·Edge 가 같은 바이트·해시로 통과한다. v1 기존 벡터는 그대로 통과한다.
- 워커 복사본 MANIFEST 가 PC 원본과 일치하고 파서 벡터를 전부 통과한다.
- 205 일괄 전송 뒤:
  - 서버 원본 3,143건(답변 완료 건 기준 수와 일치), `parse_state='failed'` 0건.
  - `community_parse_diffs` 가 0건이거나, 남은 차이마다 원인(파서 버전 차이 등)을 적는다.
  - 지도 공개 수치를 전송 전과 비교해 바뀐 항목과 이유를 보고한다.
- 공개 API·화면 캐시·CSV·DOM 에 원본·신고내용·처리내용이 없다는 시험이 통과한다.
- 동의 철회 시 원본·파싱 행이 지워진다는 시험이 통과한다.
- 게이트 판정 벡터 G1~G11 을 PC·모바일이 같은 결과로 통과한다. 소프트 리셋 뒤 같은 계정 재로그인·재동의로 로컬 DB 가 그대로
  이어지고, 다른 계정은 `db_owner_mismatch` 로 가는 것을 실제 앱(PC 브라우저, 모바일 AVD)에서 확인한다.
- 지도 화면 변경(배너 등)은 실제 브라우저 증거(스크린샷·console·network)를 남긴다(AGENTS 완료 정의).

## 12. 위험

- 파싱 결과 차이로 과도기에 지도 수치가 바뀐다 → diff 기록과 단계별 비교로 설명한다.
- 원본에 담긴 비공개 정보(신고 본문, 보완 요청자 전화, 첨부 URL) → private 저장, 권한 시험, 로그 금지, 백업 보관도 동의 철회를 따른다.
- 동의문 전환 시점에 새 클라이언트 사용자는 전원 재동의 전까지 잠긴다(G5) → 정책 전환을 클라이언트 릴리스 뒤로 미루고, 재동의 화면 한 번으로 끝나게 한다.
- 72시간 유예는 서버 장애가 길면 기존 사용자도 잠근다 → 206 장애 대응 시간을 이 기준에 맞춘다.
- 대량 재파싱이 화면 캐시 무효화를 몰고 온다 → 배치 휴지와 야간 실행.
- 구버전은 서버가 직접 알릴 수 없다 → 동의문·지도 배너로 알리고 차단일을 늦게 잡는다.
- 파서 복사본이 PC 와 어긋난다 → MANIFEST 해시 CI, PARSER_VERSION 을 올리지 않는 파서 수정(예: 1a67d01)도 해시 변화로 잡힌다.

## 13. 남은 결정

- 구버전 차단 날짜.
- 동의문 최종 문구(P3 에서 초안 제시).
- 클라이언트 릴리스 버전 번호(가정: PC 3.6.0, 모바일 2.5.0).
- 후속: 크롬 확장 본인 원문 열람(카카오 본인 확인 → 자기 contributor 의 `community_report_parsed` 만 반환하는 API).

## 14. 로컬 구현 상세 정정 (2026-10-21)

- v2 수신 여부·새 정책 버전·v1 차단일·안내는 `private.community_ingest_config`가 단일 정본이다.
  account status와 ingest가 같은 `internal_community_ingest_capabilities` RPC를 읽는다. 서로 다른 Edge 환경 변수로
  capability와 실제 수신이 어긋나는 것을 피한다. 기본 v2=false, sunset=null이며 이 작업에서 켜지 않는다.
- v1 계약 파일을 불변으로 두라는 지시와 account-api 문서 갱신 지시는 새 `community-ingest-v2/account-api.md`로 해결한다.
  최신 계정 계약은 새 v2 폴더와 auth/docs/account-api.md, map/docs/account-api.md에 같은 바이트로 둔다.
  기존 community-ingest/account-api.md는 v1 역사 문서로 보존한다.
- 정책 초안 발급 SQL은 auth/supabase/policy-drafts에 두며 자동 migration 목록에 넣지 않는다.
  사용자 검토·승인 전 적용하지 않는다. 초안 본문에도 미발급임을 표시한다.
- 공용 SQL 함수는 v1 fact 대입·동의 lineage·순서 비교를 분리한다. Edge의 기존 검증/deriveFact는 유지하며,
  Python 워커는 기관 정본 resolve.py와 같은 파생 함수를 사용한다. 두 언어의 결과를 모든 v1 observation 벡터로 대조한다.
- 원장 30일 정리는 payload를 제거하고 event_id 중복 응답에 필요한 불변 메타·해시·영수증을 남기는 방식이다.
  행 자체를 지우면 오래된 v1 재시도가 새 이벤트가 되어 멱등성이 깨진다. 최소 메타의 보관 문구는 정책 초안에 명시했다.
- P6/P7과 실제 UI 브라우저·AVD 검수는 이 로컬 작업의 검수자가 수행한다. P4/P5의 raw_backfill A/B/C 및
  모든 capture 호출 지점 대응은 아직 완료되지 않았으며, 이 정정이 범위를 완료 처리하는 근거는 아니다.
