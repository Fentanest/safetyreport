# 커뮤니티 공유 업로드 — PC 데이터 경로 (T4)

`contracts/community-ingest/` 계약의 PC 쪽 데이터 경로 구현 기록이다.
게이트·온보딩·초기화 job·`main.py`·스케줄러 본체는 T3 소유이며, 여기서는 인터페이스로만 연결한다.

2026-09-28: 제목의 신고번호를 `report_number` private event 필드로 journal v3에 저장해 업로드한다. Observation 해시는 유지한다. 번호만 새로 확보되면 한 번 더 캡처한다.
2026-09-28 개정2(계정별 기여, 소유 이전 대체): 같은 신고의 타 계정 업로드는 중앙이 `accepted`로 정상 수신한다(각자의 기여로 남고 전체는 고유 1건).
중앙 `transferred`·`cross_account_mismatch`·`report_identity_mismatch`·`ambiguous_existing_owners` ACK는 더 발급되지 않는다(구버전 앱이 받아도 무해 — DURABLE_STATUSES 유지).
새 Edge·migration 배포가 PC 업데이트보다 먼저여야 한다.

2026-09-28(같은 날 확정): 답변 완료만 중앙에 올린다. 적격 = status ∈ {accepted, partial, rejected, completed_unknown}.
처리중·보완요청·취하·이송·other 관측은 이벤트를 만들지 않는다 — `status_correction` 발급 중단, 로컬 `detail_status` 기록만.
로컬 prev 합성에 `server_completed` 를 쓰지 않는다(표·manifest 신선도 검사는 유지). 구버전 잔여 미전송 `status_correction` 행은
업로드 실행 시작 때 `block_superseded_corrections` 가 보내지 않고 `blocked:deprecated_status_correction` 으로 보존한다(drop 없음).
서버는 비적격 payload·`status_correction` 이벤트를 이벤트 단위 `rejected:non_final_not_accepted`(durable=false)로 거절하며 배치 나머지는 정상 처리한다.
답변 완료로 올라간 신고가 나중에 비종결 상태로 돌아가면(드묾) 중앙은 마지막 답변 상태를 유지한다.

## 흐름

```
공식 상세 응답 → parser → build_adapter_input (공식 열만)
  → build_payload/canonical_json/sha (불변 확정)
  → community.db 한 트랜잭션 (detail_status → journal → outbox → report_latest)
  → 개인 DB 저장 → mark_personal_save
  → wake / data_version 폴링 → request_upload → community-ingest → ACK 적용
```

- 공유 DTO 는 공식 상세 응답을 받은 순간의 값으로 확정한다. 개인 수정값(override)·별점
  보강·화면 계산과 합치기 전이며, 이후 어떤 경로도 journal 의 payload 를 바꾸지 않는다.
  전송·재시도·수동·자정 업로드는 journal 문자열을 그대로 보낸다(개인 DB 재조회 금지).
- capture 가 실패하면 그 신고의 개인 저장을 하지 않는다. 다음 수집의 선정 규칙이
  그 신고를 다시 읽으므로 공유 사본을 영구히 놓치지 않는다(S-03).

## 모듈

| 모듈 | 역할 |
|---|---|
| `services/community_capture.py` | 순수 함수(`build_adapter_input/build_payload/canonical_json/payload_sha256/is_eligible/decide_event`) + `capture/mark_personal_save/capture_retry_ids/on_contributions_deleted` |
| `services/community_ingest_client.py` | `POST {supabase_url}/functions/v1/community-ingest` + manifest 조회, ACK 검증·분류 |
| `services/community_uploader.py` | `request_upload/wake/start_background/stop_background/upload_status/reshare_candidates/request_reshare/refresh_server_completed/on_contributions_deleted/block_superseded_corrections` |
| `services/community_crawl_upload.py` | 새 크롤링 전 미전송 자료 소진과 종료 뒤 복구 업로드, 현재 크롤링 로그 기록 |
| `services/community_schedule.py` | KST 순수 함수(`due_key/next_due_at/should_run`) + `register_community_jobs/run_midnight/catch_up_on_start` |
| `web/routers/community_upload_route.py` | `router`(관리자 `/community/upload/*`), `api_router`(API 키 `/api/v1/community/upload/*`) |
| `core/storage/reports_repo.py` | `_save_one` 안 capture 호출 1곳 + `personal_save_state` 표시, 연속 3회 실패 시 중단 |
| `web/templates/report_map.html` | 지도 위 "커뮤니티 공유" 패널 |

## 운영 주의점

- `community.db` 는 개인 DB(`data.db`)와 별도 파일이다. 백업·다운로드·편집기·변환·초기화
  (`--reset`)가 이 파일을 건드리지 않는다. 비밀(토큰·연결 비밀)은 여기에 두지 않는다.
- 수집 서브프로세스(`start.py`)와 메인 서버가 함께 열므로 WAL + `busy_timeout` 30초,
  쓰기는 짧은 `BEGIN IMMEDIATE` 트랜잭션으로만 한다.
- PC 크롤링 시작 경로(`crawl_control`과 자동 대기 큐)는 subprocess 실행 전에 `recovery` 업로드를 기다린다. `more_pending`이면 계속 보내고,
  다른 실행의 업로드 잠금은 최대 125초 기다린다(OS 종료 뒤 120초 lease 만료 포함). 현재 연결의 미전송 자료가 남으면 시작을 거절한다.
  실패 이유와 건수는 크롤링 로그에 남긴다. 자동 대기 큐는 번호를 보존하고 재시도를 예약한다. 크롤링 중 실시간 업로드와
  종료 뒤 복구 업로드도 같은 로그에 전송·중앙 확인·재시도 건수를 남기며, 종료 뒤 업로드 확인 후 완료 이벤트를 보낸다.
- `source_revision` 은 파일 전체 단조(`meta.next_revision`). 데이터셋 회전으로 초기화하지
  않는다. 중앙 `last_accepted_revision` 보다 작아지면 올린다(`raise_revision_floor`).
- 한 수집 실행에서 capture 가 연속 3회 실패하면 `CaptureStoreUnavailable` 로 수집을 멈춘다.
  재시도 의도는 `community_capture_retry.json`(원자 쓰기)에 남고 증분 선정에 포함된다.
- manifest 신선도: `meta.manifest_scope` 가 현재 연결과 다르면 수집 전에 전 페이지를 받아
  교체한다. 실패하면 수집을 시작하지 않는다(fail-closed). 모든 페이지의 `manifest_token`
  이 같아야 교체하며, 다르면 처음부터 최대 3회 다시 받는다.
- 전송 대상은 outbox 행 중 journal 의
  (project_namespace, contributor_fingerprint, connection_id, consent_grant_id)가 현재
  `context` 와 같은 행만이다. 계정이 바뀌면 이전 계정 행은 보내지 않는다(C04).
- 한 요청에 같은 신고의 이벤트는 하나만 넣는다. 같은 신고의 다음 이벤트는 앞 요청의
  ACK 뒤 다음 요청으로 보낸다(revision 순서 유지).
- 업로드 제어는 두 앱 공통 규칙 **UC-1**(`contracts/upload-control/vectors.json` + MANIFEST, 모바일과 바이트 동일, 판정 코드
  `services/community_upload_policy.py`). 설계·재현 표: `docs/plans/2026-09-27-upload-hardening-android.md`.
  - **완료** = HTTP 200 ∧ protocol 1 ∧ request_id ∧ results ∧ 보낸 event_id 만·중복 없음 ∧ `durable === true` ∧ durable 상태 ∧
    `receipt_id` UUID. 이때만 journal ack 기록 + outbox 삭제(한 트랜잭션). `conflict` → dead_letter, `rejected` → blocked 보존.
  - 응답 전체 형식 오류(깨진 JSON·HTML·protocol·모르는/중복 id·durable 누락) → `invalid_ack`: 재시도(dead_letter 아님).
    형식이 맞고 일부 id 만 빠지면 `ack_missing` 백오프(같은 실행에서 다시 보내지 않음). 본문 필드가 실제 HTTP 상태를 덮지 않는다.
  - 오류 분류: offline·server_busy(5xx, 오류 envelope 없는 4xx·HTML 404, 3xx 리다이렉트)·invalid_ack → **서비스** cooldown,
    429 → **계정** cooldown, 405/415 → 서비스 cooldown + `failed`, 413·로컬 초과 → 배치 이분(단건만 dead_letter),
    `payload_hash_mismatch`/`event_type_mismatch` → 이분, 모호한 400 `invalid_request`/422 `schema_invalid` 는 단건 대조 요청으로
    판정(다른 이벤트가 저장되면 그 건만 dead_letter, 모두 거절이면 버리지 않고 보류).
  - 대기: 로컬 백오프 `min(300, 5·2^(n-1))·(0.5+0.5u)`, 서버 지시(헤더 Retry-After 초·HTTP-date — 응답 Date 기준, 본문
    `retry_after_seconds`) 최댓값, 실제 = max(둘). 24시간 초과 지시는 24시간. 첫 일시 장애 뒤 남은 배치를 보내지 않는다.
  - 영속 제어 `upload_control`: cooling_down 이면 모든 트리거(실시간·수동·자정·복구·재공유)가 보내지 않고 `cooldown`,
    시각이 지나면 1건짜리 확인(probing) → 성공이면 ready 로 이어서 보낸다. 재시작·연타로 풀리지 않는다.
  - `attempt_count` 는 실제 HTTP 요청마다 그 요청의 이벤트만 +1(게이트·cooldown·lease·토큰 없음·로컬 초과는 세지 않음).
  - 실행 1개(lease `upload`, owner `run:<uuid>:<trigger>`), 요청 전 heartbeat(`renew_lease` — 소유권을 잃으면 멈춤),
    요청 간격 ≥1.1초, 예산 요청 25개·90초(남으면 `more_pending` + 곧바로 다시 깨움), DB 는 200행씩 읽고 payload 는 보낼 배치만.
    시작 때 만료·빈 lease 의 in_flight 만 되돌린다(attempt 유지). 신고마다 가장 앞 revision 하나만 후보(뒤 revision 이 먼저 가지 않음).
  - envelope 크기는 최종 직렬화 UTF-8 바이트로 계산(≤256KiB). 재전송은 저장된 event_id·payload·**journal 의 writer_epoch** 그대로.
  - 예전 코드가 영수증 없이 완료로 적은 행(receipt 없음·UUID 아님)은 manual/midnight/recovery 때 같은 event_id 로 다시 확인받는다.
  - 401 은 거절된 토큰으로 **실제 강제 갱신** 1회 → 재전송. 갱신 네트워크 실패는 offline(cooldown), 갱신 토큰 폐기는 auth_required.
  - 403 `kakao_required`/`session_revoked`·401 은 행을 `auth_required`(journal 차단 없음)로 두고 게이트 무효화 — 재로그인하면
    같은 event_id 로 재개. `consent_*` → blocked + needs_consent, `connection_*`/`writer_superseded`/`contributor_suspended` → blocked.
  - `upload_runs` 는 요청을 보냈거나 조치가 필요한 실행만 기록(최근 500행·30일). 미전송 사본은 정리하지 않는다.
  - 같은 프로세스의 동시 호출은 진행 중 실행에 합류한다. 합류한 manual/midnight/recovery/reshare 는 앞선 실행이 끝난 뒤 **자기 실행**을 하고
    그 결과를 돌려준다(자정 key 를 다른 실행의 결과로 끝내지 않는다). 401 재전송도 lease 연장·요청 간격을 다시 지킨다.
  - 재시도 실행기: `start_background` 1초 루프가 wake·`data_version` 변화(실시간)와 **다음 깨울 시각**(가장 이른 next_retry_at·
    cooldown 끝, 실행 뒤마다 다시 계산) 도달(복구)을 본다. 행마다 타이머를 두지 않는다. 종료 때 새 실행을 시작하지 않는다.
- ACK `projection_status` 5종을 journal 에 저장하고 패널에 표시한다:
  published=지도 반영됨, removed=지도에서 빠짐,
  held=중앙 저장 완료·지도 반영 대기, not_public=중앙 저장(지도 비표시),
  not_applicable=변경 없음.
- 삭제(`contributions-delete`, `web/routers/community_route.py` `_contributions_delete`) — 두 단계 표시(Sol 3·4차):
  1. 중앙 호출 **전** community.db meta 에 `deletion_pending:<id>` = `prepared` 를 쓴다. 못 쓰면 중앙 삭제를 요청하지 않는다.
     prepared 가 있는 동안 업로드·reshare 는 `deferred`/`deletion_cleanup_pending` 으로 막기만 하고 적용·삭제하지 않는다.
  2. 중앙 결과: 확정 거절(4xx)이면 자기 표시만 지운다. 불명(네트워크·5xx)이면 표시를 유지하고 503 `deletion_unconfirmed`
     로 다시 요청하게 한다(중앙 삭제는 반복해도 안전).
  3. 중앙 성공 뒤 모든 표시를 `confirmed` 로 바꾸고 한 트랜잭션에서 적용한다: 그 시점 journal 최대 rowid 까지
     `blocked_reason='deleted_by_user'`, 그 outbox `blocked`, `server_completed` 비움, 표시 삭제. 실패하면 confirmed 표시가
     남아 다음 업로드가 먼저 적용한다(응답 `local_cleanup_pending`).
  4. 그 뒤 writer 연결 파일을 지운다. 실패하면 응답 `writer_reset_pending`(로컬 확정은 이미 끝남).
  모바일(`lib/community/upload_hooks.dart` `requestDeletion`)도 같은 순서다.
- 자정 업로드: KST 00:00 due, 키 범위
  (project_namespace, contributor_fingerprint, local_dataset_id, writer_epoch,
  schedule_key). 최신 키 1회만 보충하며 이전 날짜 누락은 따로 실행하지 않는다.
  그날 key 는 결과가 `sent`/`no_pending` 일 때만 succeeded. `not_due`·`cooldown`·`busy_other_run`·`needs_*`·`blocked_gate`·
  `more_pending` 은 deferred(사유 저장), 그 밖은 failed. 자정 성공과 무관하게 미전송 복구는 재시도 실행기가 따로 한다.
  cron `misfire_grace_time` 6시간·coalesce, 시작 보충(`catch_up_on_start`)은 백그라운드 스레드(시작을 막지 않음).
- 파일 200MB 초과 시 경고만 하고 자동 삭제하지 않는다(`outbox_size_warning`).
- `personal_save_state='pending'` 이고 10분 지난 행의 시작 시 정리는 미구현이다
  (표시용 reconcilation — T3 또는 후속 작업에서 `local-store.md` 규칙대로 추가한다).
- `report_map.html` 패널의 POST 에 쓰는 CSRF 토큰은 `stats.py` 지도 뷰가 내려준다
  (T4 범위 밖 2줄 추가 — T3 확인 필요, `.agent-runs/T4/REQUESTS.md` 참조).

## 코드 대조 정정

| 날짜 | 내용 |
|---|---|
| 2026-09-27 | UC-1 업로드 장애 대응: 이전 서술(400/413/422 → dead_letter, 1초→1시간 백오프, 401 은 캐시 토큰 재사용, 자정 success/no_change)은 코드와 달라졌다 — 위 규칙이 현재 코드. `community.db` v2(`upload_control`). API `result` 값은 호환 유지, 새 코드는 `outcome`. |
| 2026-09-26 | `contracts/community-ingest/` 사본이 `.gitignore` 로 2개 파일만 추적되던 것을 857185d 로 21개 전부 추적. 이 문서의 규칙 서술은 코드·계약과 일치함을 벡터 테스트로 확인. |

## 원문 기관코드 수집 (observation-v3, 2026-09-28)

- 파서가 선택 답변의 `C_MANAGE_ORG` 원문을 `처리기관코드`(TEXT, detail·merge·`/api/v1`·교환·백업 동일)로 저장한다. 7자리 영숫자·선행 0 보존, 정수 변환 금지, 없으면 NULL.
  `처리기관`(원문 기관명)은 덮어쓰지 않으며 개인 수정값은 merge 표시 전용으로 커뮤니티 원문으로 승격하지 않는다(capture는 파서 원본만 읽음).
- payload `source_agency_code`(v3, null 가능): 같은 답변의 코드·기관명·담당자를 함께 보낸다. 서버는 저장만 하고 공개 projection에 내보내지 않는다(동의 범위 미확정 — 보고).
  v1/v2 payload 도 계속 받는다. 코드가 새로 확보되면 같은 해시여도 새 이벤트를 발급한다(번호 백필과 같은 규칙).
- DB 스키마 버전 5(레거시 거부·초기화 후 재수집은 기존 정책 유지). 교환 계약 `storage-contract.json` 동일(서버 5, 모바일 16 — 모바일도 함께 변경).
- parser_version `pc-parser-3`/`mobile-parser-3`.

## 위반법규 공유 (observation-v2, 2026-09-28)
- payload 에 `violation_law` 를 추가했다: 파서가 처리내용에서 뽑아 저장하는 위반법규 열(법 이름·조항, 60자 이내)만 보내고 처리내용 원문은 보내지 않는다. 비어 있으면 null.
- 계약 `observation-v2`(`contracts/community-ingest`, 지도 레포 정본 사본), 필수 동의 정책 `2026-09-28.2`(위반법규 공개 항목 추가). 당시 parser_version `pc-parser-2`/`mobile-parser-2`(현행 v3는 위 절).
- 중앙은 v1(12키) payload 도 받는다. 기존 공유 자료에는 위반법규가 없으므로, 배포 때 사용자 결정으로 중앙 공유 자료를 초기화하고 다시 올린다(초기화는 배포 절차, 코드에서 자동 실행하지 않음).
- 배포 순서: 중앙 SQL·auth 정책 migration → Edge Function → 앱. 앱이 먼저 나가면 중앙이 v2 를 몰라 422 로 보류된다(잃지 않음).
