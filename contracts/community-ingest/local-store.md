# 앱 쪽 `community.db` v1 (PC·모바일 같은 의미)

위치: PC `<data>/community.db`, 모바일 앱 문서 폴더 `community.db`. 개인 DB(`data.db`, `mysafetyreport.db`)와 **별도 파일**이다.
DB 백업·다운로드·DB 편집기·서버↔모바일 변환·clearAll·초기화는 이 파일을 읽거나 지우지 않는다(교환 계약 3-1 불변).
WAL, `synchronous=FULL`(PC) / sqflite 기본 + `PRAGMA synchronous=FULL`, 일반 영속 테이블만(TEMP 금지). 비밀(토큰·연결 비밀)은 이 파일에 두지 않는다.
시간은 UTC ISO-8601 `…Z` 문자열, 불리언은 0/1 정수.

```sql
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
-- schema_version=1, project_namespace=sha256(COMMUNITY_SUPABASE_URL 정규화)[:16], local_dataset_id=uuid,
-- source_account_namespace=dataset_key 와 같은 값(공식 계정), next_revision=정수, dataset_history=JSON 배열

CREATE TABLE context (            -- 단일 행(id=1). 메인 프로세스/앱이 중앙 status 확인 뒤 기록, 수집 쪽은 읽기만
  id INTEGER PRIMARY KEY CHECK (id = 1),
  state TEXT NOT NULL CHECK (state IN ('active','inactive')),
  contributor_fingerprint TEXT, connection_id TEXT, writer_epoch INTEGER, dataset_key TEXT,
  consent_grant_id TEXT, policy_version TEXT, consent_text_sha256 TEXT, source_app TEXT, source_mode TEXT,
  verified_at TEXT, inactive_reason TEXT);

CREATE TABLE source_journal (      -- 불변 관측 사본. ack/save 상태 열만 갱신
  event_id TEXT PRIMARY KEY,
  project_namespace TEXT NOT NULL, local_dataset_id TEXT NOT NULL, dataset_key TEXT,
  source_report_id TEXT NOT NULL, source_revision INTEGER NOT NULL,
  event_type TEXT NOT NULL, captured_at TEXT NOT NULL, capture_trigger TEXT NOT NULL,
  rebuild_run_id TEXT, schema_version INTEGER NOT NULL, parser_version TEXT NOT NULL,
  payload_json TEXT NOT NULL, payload_sha256 TEXT NOT NULL, eligible INTEGER NOT NULL,
  contributor_fingerprint TEXT, connection_id TEXT, writer_epoch INTEGER, consent_grant_id TEXT,
  personal_save_state TEXT NOT NULL DEFAULT 'pending' CHECK (personal_save_state IN ('pending','saved','failed')),
  ack_status TEXT, receipt_id TEXT, acked_at TEXT, projection_status TEXT, blocked_reason TEXT,
  UNIQUE (local_dataset_id, source_revision));
CREATE INDEX source_journal_report ON source_journal(local_dataset_id, source_report_id, source_revision);

CREATE TABLE outbox (              -- 전달 상태만
  event_id TEXT PRIMARY KEY REFERENCES source_journal(event_id),
  state TEXT NOT NULL CHECK (state IN ('pending','in_flight','retry_wait','auth_required','blocked','dead_letter')),
  attempt_count INTEGER NOT NULL DEFAULT 0, next_retry_at TEXT, lease_owner TEXT, lease_until TEXT,
  last_error_code TEXT, last_request_id TEXT, enqueued_trigger TEXT NOT NULL, enqueued_at TEXT NOT NULL);
CREATE INDEX outbox_due ON outbox(state, next_retry_at);

CREATE TABLE report_latest (       -- 신고별 최신 journal 행(이벤트 결정용). rebuild 는 report_latest_staging 에 쓰고 cutover
  local_dataset_id TEXT NOT NULL, source_report_id TEXT NOT NULL, event_id TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL, eligible INTEGER NOT NULL, source_generation INTEGER NOT NULL,
  PRIMARY KEY (local_dataset_id, source_report_id));
CREATE TABLE report_latest_staging (run_id TEXT NOT NULL, source_report_id TEXT NOT NULL, event_id TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL, eligible INTEGER NOT NULL, PRIMARY KEY (run_id, source_report_id));

CREATE TABLE detail_status (       -- 마지막으로 capture 에 성공한 상세의 C_NOW 라벨(목록 상태 변경 감지, S-12)
  local_dataset_id TEXT NOT NULL, source_report_id TEXT NOT NULL, c_now_label TEXT NOT NULL, observed_at TEXT NOT NULL,
  PRIMARY KEY (local_dataset_id, source_report_id));
CREATE TABLE server_completed (    -- 중앙 manifest: 이 dataset 에서 이미 completed 로 저장된 신고(source_report_key 앞 24hex, S-04)
  dataset_key TEXT NOT NULL, key_prefix TEXT NOT NULL, fetched_at TEXT NOT NULL, PRIMARY KEY (dataset_key, key_prefix));

CREATE TABLE upload_runs (run_id TEXT PRIMARY KEY, trigger TEXT NOT NULL, schedule_key TEXT,
  contributor_fingerprint TEXT, started_at TEXT NOT NULL, finished_at TEXT,
  result TEXT CHECK (result IN ('running','no_change','success','partial','auth_required','consent_required',
                                'connection_required','offline','failed','deferred')),
  counts_json TEXT NOT NULL DEFAULT '{}', request_ids TEXT NOT NULL DEFAULT '[]', error_code TEXT);

CREATE TABLE schedule_runs (
  project_namespace TEXT NOT NULL, contributor_fingerprint TEXT NOT NULL, local_dataset_id TEXT NOT NULL,
  writer_epoch INTEGER NOT NULL, schedule_key TEXT NOT NULL, scheduled_date_kst TEXT NOT NULL, due_at_utc TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('due','running','succeeded','deferred','failed')),
  attempts INTEGER NOT NULL DEFAULT 0, last_attempt_at TEXT, finished_at TEXT, deferred_reason TEXT,
  lease_owner TEXT, lease_until TEXT, run_id TEXT,
  PRIMARY KEY (project_namespace, contributor_fingerprint, local_dataset_id, writer_epoch, schedule_key));

CREATE TABLE leases (name TEXT PRIMARY KEY, owner TEXT NOT NULL, until TEXT NOT NULL);  -- upload / rebuild / scheduler

CREATE TABLE rebuild_jobs (
  run_id TEXT PRIMARY KEY, required_version TEXT NOT NULL, local_dataset_id TEXT NOT NULL,
  source_account_namespace TEXT NOT NULL, state TEXT NOT NULL, phase TEXT, confirmed_at TEXT,
  started_at TEXT, updated_at TEXT NOT NULL, completed_at TEXT, list_complete INTEGER NOT NULL DEFAULT 0,
  counts_json TEXT NOT NULL DEFAULT '{}', backup_ref TEXT, backup_check TEXT, last_error TEXT,
  source_generation INTEGER, gaps_accepted_at TEXT);
CREATE UNIQUE INDEX rebuild_one_active ON rebuild_jobs(required_version, local_dataset_id, source_account_namespace)
  WHERE state NOT IN ('completed','completed_with_gaps','abandoned');
CREATE TABLE rebuild_items (run_id TEXT NOT NULL, source_report_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('pending','fetched','failed_retryable','failed_permanent')),
  attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT, event_id TEXT,
  last_list_label TEXT,            -- 영구 실패 당시 목록 C_NOW 라벨(나중에 바뀌면 다시 조회, S-12)
  PRIMARY KEY (run_id, source_report_id));
```

규칙
- capture: detail_status UPSERT + (이벤트면) journal INSERT·outbox INSERT(context active 일 때)·meta.next_revision 증가 + report_latest UPSERT(rebuild 중이면 report_latest_staging 에 유효 최신 포인터 — 새 이벤트면 그 id, 이벤트가 없으면 기존 최신 id, **둘 다 없으면 쓰지 않음**) 를 **한 트랜잭션**으로 commit 한 뒤 개인 DB 저장. capture 가 실패하면 그 신고의 개인 저장을 하지 않는다. 저장 결과로 최신 journal 행의 `personal_save_state` 갱신.
- 시작 시 정리: `personal_save_state='pending'` 이고 10분 지난 행은 개인 DB 의 원본 상세 행이 그 payload 의 status_raw 와 같으면 saved, 아니면 failed 로 맞춘다(표시용; 전송 가능 여부와 무관).
- `source_revision` 은 `meta.next_revision` 하나로 파일 전체 단조 증가(데이터셋 회전으로 초기화하지 않음). 중앙 status·manifest 의 `last_accepted_revision` 보다 작으면 그 값+1 로 올린다.
- capture 재시도 의도 기록(S-03): community.db 자체가 실패할 수 있으므로 **별도 파일** `<data>/community_capture_retry.json`(PC) / 앱 폴더 같은 이름(모바일) — `[{source_report_id, reason, failed_at, attempts}]`, 원자적 쓰기(임시 파일→fsync→rename).
  순서: 상세를 받으면 **capture 전에** 그 ID 를 의도로 기록 → capture → 개인 저장 → 성공이면 의도 제거. 의도 기록 자체가 실패하면 그 건을 저장하지 않고 수집을 즉시 멈춘다(개인 상태가 전진하지 않았으므로 다음 실행의 선정 규칙이 같은 이유로 다시 고른다). 증분 선정에 항상 포함.
  남는 한계(S-03-I): 선정 규칙상 다시 고르지 않는 신고(이미 종결 Y·같은 목록 라벨)를 **사용자가 수동 단건 재수집**하다가 의도 파일 쓰기까지 실패하면 그 수동 요청은 저장 없이 실패로 끝난다(화면에 실패 표시, 사용자가 다시 요청). 초기화 item 은 `rebuild_items` 로 재시도되므로 해당 없음.
- 삭제 뒤(`contributions-delete` 성공): 모든 outbox 대기 행 `blocked:deleted_by_user`, 삭제 시각 이전 journal 행은 `blocked_reason='deleted_by_user'` 로 표시해 reshare·location_supplement 후보에서 영구 제외. 한 수집 실행에서 capture 가 **연속 3회** 실패하면 수집을 `community_store_unavailable` 오류로 멈추고(공식 사이트 반복 호출 방지) 화면에 복구 안내.
- manifest 신선도(S-04): `meta.manifest_scope` = `<dataset_key>:<writer_epoch>` 가 현재 연결과 같을 때만 수집(실시간·초기화)을 시작한다. 다르면 먼저 manifest 전 페이지를 받아 `server_completed` 를 교체(한 트랜잭션)한 뒤 기록. 실패하면 수집을 시작하지 않고 `manifest_unavailable` 로 표시(fail-closed).
- 전송 대상 = outbox 행 중 journal 의 (project_namespace, contributor_fingerprint, connection_id, consent_grant_id) 가 현재 `context` 와 같은 것. 다르면 `blocked:context_mismatch`.
- 삭제: outbox 는 durable ACK 때 삭제. journal 은 신고별 최신 행 + 미ACK 전부 보존, 나머지 ACK 행은 90일 뒤 정리. 파일 200MB 초과 시 경고(자동 삭제 안 함).
- `rotate_dataset(reason)`: 개인 DB 교체(복원·가져오기·모드 전환)·공식 계정 변경 **직전에** 호출(보수적 선회전, S-20) → 새 local_dataset_id, 이전 id 를 dataset_history 에. 교체가 실패해도 되돌리지 않는다(초기화를 한 번 더 요구할 뿐 데이터 손실·오귀속 없음). 이전 journal/outbox 삭제 안 함.
- `server_completed` 는 writer 등록·rebind·takeover 직후와 초기화 시작 때 `community-ingest/manifest` 로 새로 받아 dataset 단위로 교체한다. 해당 신고의 correction 이 ACK 되면 그 행을 지운다.
- `project_namespace` 가 바뀌면 이전 namespace 행은 `blocked:namespace_changed`(E05).
