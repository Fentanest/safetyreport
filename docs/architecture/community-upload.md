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

## 업로더 구조 (EO R-10, 2026-10-05)
- `services/community_uploader.py`: 한 실행의 상태를 `UploadRunContext`(문맥·범위·집계·요청 ID·오류 코드·이분 대기열·대조 보류)로 들고 `_drain` → `_send` → `_apply_error` 가 이것만 받는다
  (예전 `_drain` 은 인자 13개와 여러 dict·클로저). outbox 행 상태 전이 SQL 은 `_Outbox`(만료 회수·in_flight·시도 되돌림·재시도·격리·이분 대기·인증 필요·차단·보류)와 ACK 반영(`_apply_ack`)에만 있다.
  owner 를 받는 전이는 그 실행이 잡고 있는 in_flight 행만 바꾼다. 백그라운드 worker(`start_background`·`wake`·`stop_background`)는 같은 모듈에 남기되 `_drain` 은 `wake()` 만 부른다.
- `services/community_upload_status.py`: 상태 조회(`upload_status`)·재공유(`reshare_candidates`·`request_reshare`)·서버 완료 목록 갱신(`refresh_server_completed`)·outbox 크기 경고.
  업로더 모듈이 같은 이름으로 위임한다. 업로더의 이름(`_now`·`_store`·`request_upload` 등)은 호출 시점에 찾아 써 시험·대체 구현이 바꾼 것을 그대로 쓴다.

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
    이 대기는 scope(service/account) 단위 로컬 재시도 간격이며 300초 바닥을 더 두지 않는다(2026-10-11, §쿨다운 지터·점증 참조 —
    그 바닥을 다시 두면 이 지터·점증이 항상 300초로 뭉개진다).
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
  자정 동시 기동 분산(2026-10-11, cloud-retry): 실제 CronTrigger 기동 시각은 정확히 00:00:00 이 아니라
  설치(기기)별 영속 오프셋 0~1800초(`community_schedule.midnight_offset_seconds`, 첫 호출 때 무작위로 정해 community.db
  meta 에 저장하고 이후 고정)를 더한다. `due_key`/`next_due_at`(어느 날짜 몫인지, 모바일과 공유하는 계약)은 바꾸지 않고
  misfire_grace_time·coalesce 의미도 그대로다 — 모든 PC 가 정각에 동시 기동해 Supabase 를 몰아치지 않게 하는 운영상
  분산일 뿐이다.
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

## 숫자 별점 공유 (observation-v4, 2026-09-28)
- 상세에서 확인한 별점 정수 1..5만 `rating`으로 캡처하고, 없거나 범위 밖이면 null로 보낸다. 별점사유 자유 텍스트는 전송하지 않는다. PC에서 사이트 별점 확인 뒤 capture 재조회 의도(rating_confirmed_refetch)를 기록해 다음 증분 수집의 공식 상세를 다시 읽는다. 점수가 바뀌면 payload SHA-256이 달라져 `completed_observation`이 발급된다.
- 동의 정책 2026-09-28.3에서 별점 평균·건수를 공개한다. 기존 .1/.2 계보는 별점을 공개하지 않는다. DB 교환 스키마는 그대로다.

## 추정 과태료 공유 (fine_estimate, 2026-10-12, `/home/better0101/projects/worktree/fine-estimate-contract.md`)

- `build_payload`: 로컬 통계가 그 신고를 '추정 과태료'로 세는 조건(과태료 처분인데 금액을 모름 + `services/fine_estimate.estimate()` 가 None 아님)과
  **같은 입력·같은 판정**일 때만 `fine_estimate{won, rule, rule_version}` 을 싣는다 — 대상이 아니면 키 자체가 없다(null 금지, payload·payload_sha256 불변).
  판정은 `services/fine_estimate.is_fine_amount_unknown`/`extract_fine_amount`/`estimate_if_unknown`(정본)을 `services/stats/metrics.py` 와 공유한다(중복 구현 없음).
  입력 레코드는 통계와 같은 10열(`FINE_ESTIMATE_FIELDS`: `category, entry_value, 신고명, 위반법규, 차량번호, 사진_첫촬영, 사진_끝촬영, 발생시각, ssnpc_cd, ssnpc_type`)을 쓴다.
  확정 금액(`amount.confirmed_won`)과는 별도 필드·별도 합계로만 쓴다(PROJECT_RULES §3-2).
- `build_adapter_input`에 `report_name`/`photo_first_at`/`photo_last_at` 입력이 늘었다(payload 로는 안 나가고 판정에만 쓰임). 크롤 경로(`core/storage/reports_repo._community_capture_for`)가
  `_prefetch_derived()` 의 사진 촬영 시각과, 이번 응답에 없으면 기존 저장 행의 신고명으로 채워 넘긴다. `발생시각`은 이미 `detail` 에 있어 그대로 쓴다.
- `contracts/community-ingest/observation.schema.json`·`observation.md`(+MANIFEST, 지도 레포와 같은 정의): `fine_estimate` 선택 필드 추가(additionalProperties false,
  `rule` enum = `bus_lane|parking|hydrant|school_zone|ev_charging|disabled_parking|fire_lane|waste|overnight_truck`). 스키마 이름·`observation-v5` 는 그대로(가산적·선택 필드라 버전 안 올림).
- parser_version `pc-parser-7`.
- **기존 신고 다시 보내기**: `services/community_capture.resend_fine_estimate_updates(engine, data_dir=None)` — `main.py` 시작 때(사진 촬영 시각 한 번 훑기와 같은 자리,
  `skip_in_fixture("startup fine estimate resend")`) 백그라운드 스레드로 한 번 돈다. 후보는 현재 로컬 데이터셋의 `report_latest` 중 그 journal 이
  `deleted_by_user` 가 아닌 신고를 ID 순으로(모바일 `resendFineEstimateIfNeeded` 와 같은 후보 규칙, 2026-10-16). 공유 캡처된 적 없는 개인 DB 신고는 보지 않는다.
  개인 DB의 금액 원문으로 후보를 거르지 않는다. 신고마다 journal 최신 payload의 `fine_estimate`만 다시 정해 기존 `capture()` 경로로 보낸다.
  추정 필드가 바뀐 것만 이벤트가 되고 outbox에 들어간다. 계정 상태(`community_cloud.capture_context`, 계정 상태 파일)는 실행 시작 때 한 번 읽어
  모든 신고에 쓴다(`capture(account_context=...)`) — 업로더가 전송 때 현재 문맥과 다시 대조한다. 완료 표시는 community.db
  `meta['fine_estimate_resend_done']`(규칙 버전 `fine_estimate.RULE_VERSION` + `PARSER_VERSION`) 기준이라 둘 중 하나가 바뀌면 다시 돈다. 삭제 정리가 안 끝났으면
  (`deletion_cleanup_pending`) 이번 실행은 건너뛰고 완료로 표시하지 않는다. 한 건이라도 실패하면 완료로 표시하지 않되, `capture()` 가 이미 반영된 건은 no-op 이라
  다음 실행이 전체를 다시 훑어도 실패한 건만 실제로 새로 처리된다. 동의·계정 문맥이 없거나 철회됐으면(기존 규칙) journal 만 쓰고 outbox 에 넣지 않는다.
  사용자 조작·초기화 크롤링 없음, 기존 outbox 배치·재시도·쿨다운 규칙을 그대로 쓴다(한 번에 몰아치지 않음).
- 재전송 후보의 값은 재사용하지 않는다. 신고마다 개인 DB `BEGIN IMMEDIATE` 뒤 추정 입력만 다시 읽고 캡처까지 연결을 유지한다.
  개인 DB에 없는 신고는 건너뛴다. 실시간 저장도 같은 쓰기 잠금을 캡처 전부터 개인 DB 커밋까지 유지한다.
  연결 유지 중에는 복원 장벽이 DB 교체·데이터셋 회전을 기다린다.
  신고 사이에 데이터셋이 회전하면 실행을 중단하며 완료 마커도 실행 당시 local_dataset_id에 묶어 복원된 DB에 재사용하지 않는다.
- `fine_estimate_backfill` 캡처는 community.db 쓰기 트랜잭션 안에서 같은 로컬 데이터셋·신고의 **최대 revision payload**를 기준으로 삼는다.
  계정 전환·staging 여부와 관계없이 최신 관측을 읽고, 개인 저장 상태(`pending`/`failed`/`saved`)로 보충을 차단하지 않는다.
  `report_latest` 가 가리키는 journal 이나 최신 journal 이 `deleted_by_user` 면 새 revision 을 만들지 않는다 — 삭제 뒤 새 연결에서도 사진 보충·재전송이
  지운 자료를 되살리지 않는다(BL-1 해결, 모바일 capture 의 삭제 차단과 같음). 후보 조회 뒤 삭제가 끼어들 수 있어 발급 트랜잭션 안에서 확인한다.
- **추정 판정(2026-10-16, 일반 캡처와 같은 함수)**: 개인 DB 현재 행에서 일반 캡처와 같은 추정 입력(`BACKFILL_PERSONAL_FIELDS` — 접수 메뉴·분류 코드·차량번호·
  사진 시각·발생시각·신고명·**범칙금_과태료 원문·위반법규**)을 읽어 `build_payload` 와 같은 `_fine_record`/`_fine_estimate_field`(= `estimate_if_unknown`)를 적용한다.
  위반법규는 어댑터 단계에서 `primary_law` 로 대표 법규만 고른다(일반 캡처와 같음). 단, 그 원문을 지금 규칙으로 읽은 금액 판정
  `(amount.confirmed_won, amount.kind, disposition)` 이 journal 최신 payload 의 것과 **같을 때만** 적용한다. 다르면 추정을 싣지 않는다(기존 추정은 걷어 낸다):
  - 개인 DB 가 journal 보다 늦은 경우(실시간 캡처 뒤 개인 저장 실패·중단) — 개인 원문으로 journal 관측에 추정을 붙이면 확정 금액 있는 관측에 추정이 실릴 수 있다.
  - 금액표 보정(규칙 2026-10-13.2 등)으로 옛 journal 의 `confirmed_won=null` 원문에서 지금은 확정 금액이 읽히는 경우 — 예: 일반 주정차 + 제32조 +
    `과태료: 120,0000원` 은 새로 캡처하면 확정 120,000원·추정 없음이므로, 옛 journal(추정 40,000/2026-10-13.1)의 재전송은 추정을 걷어 내고 새 규칙 버전의 추정을 붙이지 않는다.
  결과: `과태료 40,000원`(콜론 없음)·`과태료: 40,000원(감경…)` 처럼 확정 금액이 원문에 있으면 추정 없음, `과태료: 0원`·`범칙금 및 과태료 처분`·`경고 및 과태료 부과` 는
  일반 캡처처럼 추정 있음(PROJECT_RULES 3-2). 공통 사례: `tests/fixtures/community_backfill_vectors.json`(모바일 `test/fixtures/` 와 바이트 동일).
- **재전송은 journal 금액을 보존한다 — 관계 정리**: 보충은 `fine_estimate` 이외의 payload 필드(`amount`·`disposition`·`violation_law` 포함)와 journal 신고번호를
  최신 관측 그대로 둔다. 금액표 보정으로 확정 금액이 새로 읽혀도 `amount.confirmed_won` 은 바꾸지 않는다(버전 상승만으로 과거 공유 확정 금액을 보정하지 않는다는
  기존 결정 유지). 대신 위 일치 검사로 그런 관측에 추정을 싣지 않는다 — 그 동안 중앙에는 "과태료·확정 없음·추정 없음"으로 남고, 확정 금액은 다음 실시간 상세 수집이 갱신한다.
  최신 journal이 없거나 추정 필드가 바뀌지 않으면 이벤트가 없다. 조회와 revision 발급은 같은 트랜잭션이므로 그 사이 다른 관측이 끼어들지 않는다.
  실시간 크롤링은 계속 개인 DB에 저장할 원문으로 전체 payload를 만든다.
- 사진 보충(`photo_capture_time.fill_one`)은 첨부 일치 검사·촬영 시각 저장·effective 갱신 뒤, 쓰기 잠금 안에서 같은 추정 보충 경로를 호출한다.
  재전송 완료 마커와 무관하게 수행하므로 재공유 이력, 개인 DB 커밋~saved 표시 사이, 개인 저장 실패 뒤 재시도 경로에서도 공유 추정액을 갱신한다.
  최신 journal이 없거나 변경이 없어 이벤트가 생기지 않아도 개인 사진 시각은 저장한다. **캡처 예외는 사진 저장을 롤백하지 않는다(2026-10-16)** —
  촬영 시각은 커밋하고(다음 훑기마다 같은 사진을 다시 내려받지 않음) 그 신고를 S-03 재시도 의도(`community_capture_retry.json`, 사유
  `photo_backfill_capture_failed`)에 남겨 다음 증분 수집이 공식 상세를 다시 읽어 전체 캡처하게 한다. 의도 기록까지 실패하면 사진 시각도 롤백하고 실패로 돌려준다(2026-10-17) — 재전송 완료 표시가 있는 종결 신고는 달리 공유 추정을 고칠 경로가 없으므로, 다음 훑기가 사진부터 다시 받아 촬영 시각과 공유 추정을 함께 고친다.
  모바일 `MaintenanceService._fillOne` 도 같은 순서다(촬영 시각 저장 → `refreshFineEstimateFor` → 실패 시 `SyncEngine.queueCaptureRetry` → 그것도 실패하면 `LocalDbService.clearPhotoCapture`).
  네트워크 사진 읽기는 계속 트랜잭션 밖에서 수행한다.

### 코드 대조 정정 — 추정 입력과 저장 순서

| 종전 설명 | 현재 코드 |
|---|---|
| 추정 입력 8열, 분류 코드 제외·parser-6 | `FINE_ESTIMATE_FIELDS` 10열로 분류 코드 포함, parser-7. 대시보드 웹 경로도 메뉴와 두 코드를 함께 보충하며 모바일 응답 화이트리스트는 유지 |
| 개인 DB 트랜잭션 전 캡처·사진 보충과 1회 재전송은 독립 | 개인 DB 쓰기 잠금 안에서 캡처 후 원문 저장. 사진 보충마다 해당 신고 재캡처 |
| 개인 저장 상태·payload 일치 검사로 오래된 관측을 차단 | 두 DB가 달라도 journal 최신 payload를 보존하고 개인 추정 입력으로 `fine_estimate`만 교체한다. 상태값으로 정상 보충을 차단하지 않는다 |
| parser-5/6 금액 오타는 재전송에서 확정액으로 이관 | 보충은 금액 판정을 바꾸지 않는다. 금액 등 원문 갱신은 다음 실시간 상세 수집이 맡는다 |
| (2026-10-16 이전) 추정은 journal `amount.kind=='fine' and confirmed_won is None` 일 때, 위반법규는 journal payload 에서 | 일반 캡처와 같은 `estimate_if_unknown` 을 개인 DB 현재 원문(범칙금_과태료·위반법규 포함)에 적용하고, 그 원문의 지금 금액 판정이 journal 과 같을 때만. 종전 판정은 확정 금액이 원문에 있는 신고(`과태료 40,000원`)에 추정을 싣고, `과태료: 0원`·`범칙금 및 과태료 처분` 의 추정을 지웠다 |
| (2026-10-16 이전) 재전송 후보는 개인 DB 모든 신고 ID, 신고마다 계정 상태 파일 읽기 | `report_latest` 중 삭제되지 않은 신고만, 계정 상태는 실행당 한 번(모바일과 같음) |
| (2026-10-16 이전) 사진 보충의 캡처 예외는 사진 저장도 롤백 | 사진 저장은 유지하고 S-03 재시도 의도에 남긴다 |


## 기관코드 전체자료 대조와 통계 키 전환 (registry 2026-09-29.1, 2026-09-29)

- `shared/agency-region-registry`(스키마 v2, 세 레포 바이트 동일): 공식 전체자료 472,175행 대조.
  현존 색인 84,750(경계 23,224·하위 61,526), 폐지 전달 19,847·1:다 37·미확정 318,111, 경계 링크 9,252.
  제외 유형(입법·사법·헌법·학교·군·금융)은 번들 크기용이며 해당 신고도 src 행으로 보존된다.
- 통계 묶음 기준이 표시 이름에서 `agency_stat_key` 로 바뀌었다(기관·담당자 표, 지도 breakdown·기관수).
  출력 행·breakdown에 `agency_key` 추가(하위호환). 원문 `처리기관`·`처리기관코드`는 덮어쓰지 않으며,
  과거 신고는 조회 시 새 registry 로 다시 계산된다(재크롤링 없음).
- 공용 벡터 25건(개명·승계·1:다·미확정·코드 없음 이름만)과 3개 리더가 같은 결과를 낸다.
- 스킬 `sr-agency-registry`: 로컬 전체자료 → 빌드 → 검증 → 3레포 전파 흐름으로 갱신.


## 3.0.0.3 장애 보존과 동의 후 보충

소유권을 확인한 캡처는 동의/네트워크 상태와 별도로 기존 journal에 저장한다. 그 시점의 상태는 기존 capture_trigger/blocked_reason에 남긴다. 전송 가능한 문맥은 outbox에 대기하고, 비활성 문맥의 캡처도 fingerprint·dataset_key를 보존한다. 실제 서버 동의 복구 뒤 같은 계정/데이터셋만 기존 reshare 계약으로 다시 발급한다. 원본 payload·captured_at은 보존하며, background/recovery가 재개해도 reshare envelope trigger를 유지한다.

기존 활성 동의를 처음 확인한 status는 baseline으로만 저장하며, 이때 빠진 grant ID가 나중에 보충되어도 새 전체 수집을 예약하지 않는다. 명시적 동의 POST 성공은 계정별 `catchup_requested`로 암호화 저장하여 후속 status 장애·재시작 뒤에도 보존하고, 서버가 active grant를 확인하면 한 번 소비한다. 기록된 미동의·철회 등에서 active로 바뀌거나 기존의 non-null grant가 바뀐 경우에도 한 번 예약한다. 이미 등록된 pending/running/succeeded 작업은 덮거나 삭제하지 않는다.

서버가 확인한 grant별 한 번의 보충 작업은 암호화 내부 저장소에 `reshare → crawl → succeeded`로 남는다. 수집은 일반 full + `--force`, `--reset` 없음. 실행 ID를 spawn 전 기록하고 child sidecar의 PID/성공 결과와 실행 lease로 중복을 억제한다. 장애/중단은 같은 작업을 재개하며 매 status poll마다 새 전체 수집을 만들지 않는다. 현재 grant 보충이 완료된 뒤 발생한 오프라인 캡처도 복구 uploader가 따로 대기열에 편입한다.

완료 판정은 유효한 durable ACK와 receipt_id다. 응답 유실은 같은 event_id 재전송으로 확인한다. manifest는 공개 완료 fact의 prefix 목록이며 보편적인 영수증 목록이 아니다. 기존 완전성·페이지 token·count 검사를 유지하고 실패/부분 목록을 삭제 또는 누락으로 간주하지 않는다. 명시적 `deleted_by_user`/`deleted` 차단은 자동 reshare로 되살리지 않는다. 영수증이 있는데 중앙 fact가 사라진 모든 원인을 manifest만으로 구분하는 범용 복구 API는 구현 범위에 없다.

### 코드 대조 정정 — 긴급 정책이 우선

| 종전 설명 | 현재 코드 |
|---|---|
| active status의 grant면 최초 관측에도 보충 작업 등록 | 최초 관측은 baseline. 명시적 동의 성공 또는 기록된 상태·grant 전환 뒤 confirmed active에서만 등록 |
| ~~첫 실패 대기 2.5~5초~~ → ~~실제 재시도 최소300초~~ | 2026-10-11 cloud-retry 로 "실제 재시도 최소300초" 긴급 정책을 철회했다 — `retry_delay_seconds` 는 다시 원래 지터·점증(2.5~300초, 서버 지시가 더 크면 그 값)만 쓴다. Supabase 접속 자체를 300~1800초(점증+지터)로 멈추는 전역 서킷브레이커는 `community_cloud.run()` 이 맡는다(쿨다운 지터·점증, `docs/architecture/community-gate.md` §3.0.0.3) — 이 둘을 중복으로 적용한 것이 지터를 뭉갰던 원인이었다 |
| 서버 Retry-After 최대24시간 제한 | 더 긴 유효 서버 지시는 상한 없이 존중 |
| 이전 업로드 완료 뒤 수집 시작 | background uploader를 깨우고 로컬 수집 시작 |
| reshare는 지도 버튼만 | 실제 동의 전이가 같은 계정 소급 공유를 승인; grant별 보충 및 복구 대기열 재개 |

같은 계정의 개인 DB 복원으로 local_dataset_id가 회전해도 자동 동의 보충은 같은 fingerprint+공식 dataset_key의 이전 journal을 함께 검사한다. 신고별 최신 eligible 후보 하나만 현재 dataset에 reshare하며, 어느 이전 dataset에든 삭제 tombstone이 있으면 제외한다. 원본 journal은 변경하지 않는다.


### 경계 동일성 검증 (2026-10-18.1)

공식 하위 코드의 1:1 이전코드 연결을 부모 경계 전체의 동일성으로 확대하지 않는다.
직접 경계 후속이 둘 이상이면 파생 링크를 모두 제거하고, `forward`를 경계로 접은
그래프에서도 현존 도착 경계가 둘 이상이면 링크와 기관 ID union을 모두 차단한다.
현존 출발 경계도 도착 후보로 세어 부서 전출로 현존 두 기관이 합쳐지지 않게 한다.
seed는 같은 from의 파생 연결보다 우선하되 현존 경계 ID 유일성 불변식은 면제하지 않는다.
코드 자체의 검증된 `forward`와 `multi`((구) 표시)는 유지한다. 이름 추정·예외 목록은 없다.

스냅샷 `agency_index.alive_boundaries`는 공식 존폐여부를 보존한 검증용 경계 목록이다.
빌더는 파일 교체 전에, validator는 배포 전에 서로 다른 현존 경계의 같은 기관 ID를
거절한다. provenance에는 직접 분기 제거와 동일성 그래프 링크/forward union 제거 수를
나눠 기록한다. 세 리더와 원문 저장값은 그대로이며 서버·모바일은 조회 때 재계산한다.

| 코드 대조 정정 | 확인된 코드와 수정 |
|---|---|
| 최초 원인 설명은 직접 분기 링크 제거 누락으로 판단 | 제거는 이미 있었고 `institution_components`의 무조건 forward union이 우회했다. 생존 부모의 부서 전출도 같은 원리로 차단했다. |


## 원본 JSON 전환 후보 (미출시, 2026-10-21)

계획: `../plans/raw-json-ingest-20261021.md`, 계약: `contracts/community-ingest-v2`.
동의 active + 새 원본 정책 일치 + status.ingest.contracts의 2를 모두 확인한 capture만 원본 스냅숏을 journal에 고정한다.
community.db 스키마 4는 source_journal.raw_event_json을 추가한다. 개인 신고 DB·원본 표와 PC↔모바일 교환 형식은 바꾸지 않는다.
업로더는 v1·v2를 섞지 않고 20건·압축 전 1MiB·gzip 전송 512KiB를 적용한다. stored와 parse_pending을 구분한다.
확인 불가 상태의 로컬 사용은 같은 계정·DB의 마지막 G1 뒤 72시간뿐이며, 시계 되돌림·동의 없음·outdated는 잠근다.
확인된 철회·기존 계정의 카카오 연결/기여자 삭제는 신고 DB와 주인을 보존한 소프트 리셋을 한다.
전송 거부를 먼저 기록하고 writer/context를 해제하며 outbox를 blocked로 만들고 raw_backfill 표시를 지운다.
철회 요청 실패 시 토큰과 `revoke_pending`을 남겨 잠근 채 재시도한다. 원본 일괄 전송과 보충 호출 지점은 아래 후속 구현 규칙을 따른다.
실제 브라우저·AVD·소켓 기반 검수는 릴리스 전에 별도로 통과해야 한다.


## 원본 일괄 전송·철회 후속 규칙 (미출시, 2026-10-21)

`community.db`의 `meta.raw_backfill`은 contract, local_dataset_id, 현재 계정·grant·dataset·writer 범위(참고용), run_id, state, phase,
cursor, holds 개수, stats 요약만 담는다(크기 상수). 건별 상태(state·attempts·event_id·채움/못 채움)는 같은 파일의 `raw_backfill_items`
표(run_id, report_id, position, state, …)에 두고, 보충 시도 결과는 `raw_backfill_supplements` 표에 남긴다.
항목 상태 변경은 한 트랜잭션에 100건씩 묶어 쓰고, marker 는 run 마다 몇 번만 쓴다(건마다 전체를 다시 쓰지 않는다).
DB 최적화 뒤 목록 전체가 list_ok일 때만 누락 판정을 시작한다. 없는 신고와 손상 원본을 공식 상세 저장 경로로 채우고,
404/410은 raw_unavailable로 남긴다. 참여 완료 별점·사유와 첨부사진 EXIF를 보충하며 6개월 보관기간 밖 사진은 expired로 남긴다.
개별 실패 3회는 holds, 로그인 실패·목록 조회 실패는 시도 횟수를 소모하지 않는 300초 휴지, 연속 실패 5회도 300초 휴지다.
run identity 는 `local_dataset_id` 하나다. 다시 도는 조건은 로컬 데이터셋이 바뀔 때(복원·교체)와 소프트 리셋 뒤 재동의(리셋이 marker 를 지운다)뿐이다.
parser 세대·writer 재결합·연결 변경·정책 개정 재동의(같은 lineage)는 이미 저장된 원본을 다시 보내지 않는다(서버가 재파싱한다).
'다시 시도'는 현재 단계를 유지한 채 보류·휴지만 푼다. 목록이 끝나지 않았으면 채우기로 건너뛰지 않으며, 목록 미완료·미처리 항목이 있으면 done 이 될 수 없다.
삭제했거나 보낼 수 없는 신고(삭제 표시, 기관코드 초과, 적격 아님, 서버가 거절한 건)는 `not_eligible` 로 분모에서 뺀다.
보충(별점·사진 시각) 실패는 null 과 시도 marker 만 남기고 원본은 그대로 보낸다. 이미 조회한 신고는 다시 조회하지 않는다.
supplement 값은 계약 스키마 범위로 정규화한다(별점 1~5 밖은 null, 문자열은 최대 길이로 자름).
원본이 이미 있는 신고는 네트워크 없이 처리하고, 네트워크가 필요한 단계만 0.5초 간격을 둔다. 한 번에 3초 안팎만 예약 작업 예약을 쥐고 놓으며
pass 는 45초 안에 끝낸다. ACK 대기만 남으면 pass 를 끝내고 다음 poll 이 이어 받는다. done 이면 poll 이 스레드를 만들지 않는다.

N은 답변 완료 대상, N′=N−raw_unavailable이다. 모든 N′건의 stored 영수증만 done을 만든다.
parse_pending은 원본 저장 성공이고 파싱 대기는 별도 표시한다. duplicate는 original_status가 stored/no_change일 때만 저장 영수증이다.
stale_ignored·거절은 완료로 세지 않는다. 지도 카드에 전송 n/N′, 원본 없음, 채움/못 채움, holds와 수동 다시 시도를 보인다.
건별 원본·보충값은 캡처 시 journal에 고정하며 업로드 중 개인 DB에서 재구성하지 않는다.

전환은 새 원본 정책 동의 active와 ingest.contracts의 2를 모두 확인할 때다. 고정(sticky)하지 않는다 — 공유 설정이 v2 를 끄면 서버는
v2 요청에 503 `service_unavailable`(재시도 가능)을 주고, 다음 status 에서 2 가 빠지면 클라이언트는 v1 로 돌아간다:
새 수집은 v1 이벤트이고, 대기 중인 실시간·수동·재공유 v2 행은 같은 event_id 의 v1 형식(payload)으로 보내며, 일괄 전송(raw_backfill) 행은
v2 가 돌아올 때까지 60초 간격으로 보류한다. 원본이 없어 raw_required 로 막아 둔 행은 v1 로 돌아가면 풀어 v1 DTO 로 보낸다.
v2 에서 원본이 없는 보충 이벤트는 raw_required 로 보류하고 raw_backfill 에서 복구한다. 전환 전은 기존 v1을 유지한다.
ACK(stored 등)를 받은 journal 의 원본 사본(raw_event_json)은 비교용 해시만 남기고 지운다(journal 이 run 마다 커지지 않게).
v2 원본 스냅숏 준비 실패(저장소·원본 읽기 오류)는 개인 저장·공유 캡처를 막지 않는다.
사진 보충·금액 재전송·재공유·재파싱·일반 수집/알림 수집도 동일 capture 규칙을 따른다.

status 의 client_notice 가 정본이다(null 이거나 키가 없으면 저장된 안내와 426 표시를 지운다). ACK 의 client_notice 키도 같은 규칙이고,
성공 ACK 는 426 표시를 푼다. 안내는 이 앱 버전이 `min_version.pc` 보다 낮을 때만 보인다(info/warning 은 상단 배너, required 는 배너와 하루 한 번의
대화상자). 426 은 이 앱 버전에서만 유효한 필수 업데이트 표시이며 서버 문구가 없으면 버전 없는 일반 문구 "앱을 최신 버전으로 업데이트해 주세요."만 쓴다.
426 을 받은 행은 blocked 가 아니라 1시간 간격 재시도로 남고, 표시가 풀리면(업데이트·서버 안내 해제) 바로 다시 보낸다.

철회는 로컬 거부를 먼저 저장하고 작업을 중단한다. revoke_pending은 서버 확인 전까지 로그인 토큰과 함께 남으며
로컬 화면과 새 동의를 잠근다. 서버 확인 뒤 writer·문맥·일괄 전송 표식을 비우고 카카오 로그인부터 재시작한다.
개인 신고 DB·원본·DB 주인·동의 이력은 보존한다. 같은 계정 재동의는 기존 DB에서 새 raw_backfill을 시작하고 다른 계정은 db_owner_mismatch로 간다.
서버 확인 실패는 성공으로 표시하지 않는다. 재시도는 서버 확인을 마칠 때까지 영속 대기한다.

## 최종 검수 반영 (2026-10-21, 미출시)

- **크롤·복원과 원본 1회 전송**: 원본 전송은 rating 토큰을 `owner='raw_backfill'` 로 쥔다. 예약/수동 크롤(`start_crawl`)·복원·로그아웃 자료 삭제(`hold_for_restore`)는
  그 토큰이 원본 전송 것이면 "양보 대기 요청"(`_yield_waiters`)을 올리고 최대 10초(slice 3초 + 한 건 여유) 기다린다. 원본 전송은 건마다 `backfill_should_yield()` 를 보고 즉시 토큰을 놓고 멈추며
  대기 요청이 있는 동안은 다시 잡지 않는다(다음 poll 이 이어 받는다). 별점 등 다른 점유는 기다리지 않는다. 네트워크 없는 단계를 토큰 없이 처리하는 최적화는 하지 않았다.
- **재동의 catch-up(v2)**: 비활성→active 로 계보가 바뀐 작업은 `lineage_changed` 를 달고 있으며, v2 에서는 `raw_backfill` marker 를 지운 뒤 시작해 새 run 으로 전체를 다시 보낸다.
  active 유지 중 grant 갱신은 다시 보내지 않는다.
- **원본 사본 축약**: durable ACK 이면 v1/v2 무관하게 `raw_event_json` 을 해시만 남긴다. 데이터셋 회전·소프트 리셋은 전체를 축약한다.
- **trigger**: `raw_backfill` trigger 는 프로토콜 2 봉투에서만 쓴다(v1 봉투는 `recovery`).
- **캡처 문맥 변경**: 읽은 뒤 활성 문맥이 바뀌면 개인 저장·크롤을 막지 않고 그 관측은 inactive 로 journal 에만 남긴다(`CaptureStoreUnavailable('capture_scope_changed')` 폐기).
- **v2 꺼짐 비용**: `raw_backfill.start()` 는 v2 준비가 아니면 즉시 반환(스레드·lease 없음). `seed_g1` 은 읽기로 조건을 먼저 확인하고 대상일 때만 잠금·쓰기.
- **공식 로그인 실패(원본 전송)**: `로그인 거부`·계정 설정 없음은 `official_login_required`(permanent) — 계정 설정/토큰 지문이 바뀔 때(설정 저장·수동 로그인)까지 멈춘다. 그 외 실패(`official_login_retry`, 상세 401/403)는 300초부터 두 배씩 최대 1시간 백오프.
- **DB 최적화 보류만 남음**: 현행 파서 세대 부분 완료(holds 있음, done=false, 진행 중 아님)는 원본 전송 준비 완료(`db_optimize.only_holds_left`). 그 신고들은 원본 전송이 원본을 다시 받는다.

## 1회 전송 재개 규칙 (미출시, 2026-10-22)

규칙 정본: 지도 레포 `docs/implementation/backfill-resume-20261022/CLIENT-RULES.md` 1~4절. 모바일 `raw_backfill.dart`·`raw_backfill_runtime.dart` 와 1:1.
위 2026-10-21 절의 "개별 실패 3회는 holds, … 300초 휴지" 는 아래로 바뀌었다.

- **오류 분류** `classify_backfill_error(exc) -> 'transient'|'fixed'`(`services/community_raw_backfill.py`). 일시적: 네트워크·DNS·TLS(requests·urllib3·curl_cffi·
  `OSError` 네트워크 errno), 시간 초과, 공식 상세 API 5xx·429·`result` 없음(`BackfillTransient`, 이전의 `RuntimeError('detail_retry')`), DB 잠김. 그 밖(파서 예외·저장 실패·
  분류 못 한 예외)은 고정적. 404/410 은 지금처럼 `raw_unavailable`, `BackfillBlocked` 는 `not_eligible`.
- **일시적** → 신고 시도 횟수·커서를 그대로 두고 run `paused`(`reason`=사유 코드), 연속 횟수 `transient_failures` 1·2·3·4회 이상 = 60·300·900·3600초.
  한 건이라도 진행하면 0. 목록 실패·연속 고정적 오류 5회도 같은 간격. 공식 로그인 문제는 예전 규칙 그대로.
- **고정적** → 신고별 3회면 `hold`. 항목 `detail_json` 에 `hold_count`·`next_attempt_at`·`first_held_at`·`stale`. 간격 15분·1시간·6시간·24시간(이후 24시간).
  채우기 slice 시작에 시각이 지난 보류를 `pending`(attempts=2)으로 되돌린다. 30일이 지나면 `stale=true` 만 표시하고 하루 1번 계속 시도한다.
  '다시 시도' 버튼은 시각과 관계없이 모든 보류를 처음부터(attempts·hold_count 0).
- **업로드 확인** `retry` 3회 → `hold`(`error='upload_retry'`, 같은 자동 재시도). `blocked`(outbox `blocked`·`dead_letter`)만 `not_eligible`+`upload_rejected`.
- **업로드 확인 단계에 남은 pending**: 확인 단계 끝에 `pending` 항목이 있으면(1회 초기화로 되돌린 항목 등) 채우기(cursor 0)로 돌아간다(모바일과 같음).
- **보류만 남은 run**(`holds`, 확인 대기 없음): marker `next_attempt_at` = 가장 이른 보류 시각, 스케줄러 1회 작업 `community-raw-backfill-wake` 가 깨운다(60초 poll 도 그대로).
- **첫 실행 1회 초기화**(`unstick_once`, meta `raw_backfill_unstick="2026-10-22.1"`): 보류 → pending, `upload_rejected` → pending(eligible), `paused`(수동 로그인 필요 제외)의
  휴지를 0으로, 되돌린 항목이 있는 `done`·`holds` 는 `filling` 으로 다시 연다. 한 트랜잭션, `start()` 에서 끝난 run 검사보다 먼저.
- **업로더 깨우기**: 채우기 slice 에서 새로 `queued` 된 항목이 있으면 한 번 `community_uploader.wake()`(run 한 번에 최대 1회). 중지·잠금 중에는 깨우지 않는다.
- **업로드 일시 중지 중**: run 은 `paused`/`maintenance`, `next_attempt_at` = 재개 시각+지터(없으면 null, 타이머 없음). 신고 횟수 안 셈. 중지가 풀리면 바로 이어 간다.
  업로더는 요청을 보내지 않고(`cooldown`/`maintenance`), 503 `maintenance` 를 받은 행은 attempt 를 되돌리고 `retry_wait`(다음 시도 = 재개 시각), 일찍 재개되면 바로 보낸다.
- **필수 버전 잠금 중**: `start()`·`run()` 이 아무것도 하지 않는다(marker 그대로). 업로더는 `blocked_gate`/`update_required`.
- **사진 보조 값**: 6개월 보관 기간이 지나 시도하지 않은 사진은 `raw_backfill_supplements` 에 `expired` 로 남긴다(예전에는 `success`).
- **진행 보고(2-1)**: `services/community_progress_trigger.tick()` 이 60초 게이트 확인·게이트 통과 때 trigger(`start`·`state_change`·`periodic`·`daily`)와 값
  (진행 보고 계약 `backfill`·`optimize`·`official_login`, PC 는 `pace` 없음)을 만들어 `community_progress_report.maybe_send` 에 넘긴다. 15분·하루 1번·60초 간격은 보고 쪽 판정.
  marker 에 `run_started_at`·`updated_at`(epoch 초)을 더했다(이전 run 은 null).
