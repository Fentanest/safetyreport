# 커뮤니티 공유 업로드 — PC 데이터 경로 (T4)

`contracts/community-ingest/` 계약의 PC 쪽 데이터 경로 구현 기록이다.
게이트·온보딩·초기화 job·`main.py`·스케줄러 본체는 T3 소유이며, 여기서는 인터페이스로만 연결한다.

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
| `services/community_uploader.py` | `request_upload/wake/start_background/stop_background/upload_status/reshare_candidates/request_reshare/refresh_server_completed/on_contributions_deleted` |
| `services/community_schedule.py` | KST 순수 함수(`due_key/next_due_at/should_run`) + `register_community_jobs/run_midnight/catch_up_on_start` |
| `web/routers/community_upload_route.py` | `router`(관리자 `/community/upload/*`), `api_router`(API 키 `/api/v1/community/upload/*`) |
| `core/storage/reports_repo.py` | `_save_one` 안 capture 호출 1곳 + `personal_save_state` 표시, 연속 3회 실패 시 중단 |
| `web/templates/report_map.html` | 지도 위 "커뮤니티 공유" 패널 |

## 운영 주의점

- `community.db` 는 개인 DB(`data.db`)와 별도 파일이다. 백업·다운로드·편집기·변환·초기화
  (`--reset`)가 이 파일을 건드리지 않는다. 비밀(토큰·연결 비밀)은 여기에 두지 않는다.
- 수집 서브프로세스(`start.py`)와 메인 서버가 함께 열므로 WAL + `busy_timeout` 30초,
  쓰기는 짧은 `BEGIN IMMEDIATE` 트랜잭션으로만 한다.
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
- durable ACK(`accepted/duplicate/no_change/stale_ignored/quarantined`)만 outbox 를
  지운다. `conflict` → dead_letter(원본 불변), `rejected` → blocked 보존,
  400/413/422 → dead_letter, 429 → Retry-After 대기, 5xx·timeout·busy → 지수 백오프
  1초→최대 1시간 ±20%. 401 은 토큰 갱신 1회 후 재시도한다.
- 403(consent_*/connection_*/writer_superseded/contributor_suspended/session_revoked)은
  해당 행 blocked + 게이트 무효화(T3 `community_gate.invalidate`).
- ACK `projection_status` 5종을 journal 에 저장하고 패널에 표시한다:
  published=지도 반영됨, removed=지도에서 빠짐(정정),
  held=중앙 저장 완료·지도 반영 대기, not_public=중앙 저장(지도 비표시),
  not_applicable=변경 없음.
- 삭제(`contributions-delete` 성공 뒤 T3 라우트가 `on_contributions_deleted()` 호출):
  outbox 대기 전부 `blocked:deleted_by_user`, 삭제 시각 이전 journal
  `blocked_reason='deleted_by_user'`(reshare·location_supplement 후보 영구 제외),
  `server_completed` 비움.
- 자정 업로드: KST 00:00 due, 키 범위
  (project_namespace, contributor_fingerprint, local_dataset_id, writer_epoch,
  schedule_key). 최신 키 1회만 보충하며 이전 날짜 누락은 따로 실행하지 않는다.
  `success/no_change` 만 succeeded 로 기록한다(partial·실패·인증 필요는 아님).
- 파일 200MB 초과 시 경고만 하고 자동 삭제하지 않는다(`outbox_size_warning`).
- `personal_save_state='pending'` 이고 10분 지난 행의 시작 시 정리는 미구현이다
  (표시용 reconcilation — T3 또는 후속 작업에서 `local-store.md` 규칙대로 추가한다).
- `report_map.html` 패널의 POST 에 쓰는 CSRF 토큰은 `stats.py` 지도 뷰가 내려준다
  (T4 범위 밖 2줄 추가 — T3 확인 필요, `.agent-runs/T4/REQUESTS.md` 참조).

## 코드 대조 정정

| 날짜 | 내용 |
|---|---|
| 2026-09-26 | `contracts/community-ingest/` 사본이 `.gitignore` 로 2개 파일만 추적되던 것을 857185d 로 21개 전부 추적. 이 문서의 규칙 서술은 코드·계약과 일치함을 벡터 테스트로 확인. |
