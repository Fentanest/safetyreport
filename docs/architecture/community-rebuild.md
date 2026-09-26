# 1회 초기화 크롤링 (T3b)

`contracts/community-ingest/rebuild.md` 상태기계의 PC 구현. 계획 `plan-final.md` §7.

## 범위 키

`(REQUIRED_VERSION, local_dataset_id, source_account_namespace)`.
`REQUIRED_VERSION = "source-rebuild-2026-09-26.1"`,
`source_account_namespace = sha256("safetyreport-dataset|v1|" + 공식 로그인 ID 소문자·trim)`
(설정 `[LOGIN] username` = `settings.username`, 없으면 `prerequisites_required`).
완료 판정은 이 키의 `completed`/`completed_with_gaps` 행 유무. 새 설치도 같은 job 하나로 첫 전체 수집을 겸한다.

## 상태 보관

`community.db` 의 `rebuild_jobs`·`rebuild_items` (`services/community_store.py` 스키마, T0b).
개인 DB(`data.db`)는 제자리 갱신만 한다. 시작 전 sqlite backup API 로
`<data>/backups/pre-rebuild-<run_id>.db` + `PRAGMA integrity_check` 를 남기고,
실패하면 개인 DB 무변경으로 `failed`.

## 흐름 (`services/community_rebuild.py`)

`start(confirmed_by)` → `preparing_backup` → manifest 전 페이지 갱신(T4
`community_uploader.refresh_server_completed()`, 실패면 `prerequisites_required:manifest_unavailable`)
→ `running` → `crawl_control.start_rebuild(run_id)`.
이미 활성 run 이 있으면 unique index + lease 로 그 run 을 반환한다(확인 연타·다중 브라우저).

`start.py --rebuild <run_id>` (환경변수 `SAFETYREPORT_REBUILD_RUN_ID` 도 설정, T4 capture 용):

- 목록: `crawltitle_api.crawl_titles(..., progress)` 가 전 페이지 성공(`list_ok`)일 때만
  그 ID 전부를 `rebuild_items(pending)` 로 등록하고 `list_complete=1`.
  한 페이지라도 실패·로그인 실패면 job `failed` (0건 성공 금지).
  정상 인증의 빈 목록(총 0건)은 `list_complete=1`·items 0 → `completed`.
- 상세: items 중 `pending`·`failed_retryable` 만(checkpoint, 재개 시 `fetched` 건너뜀).
  `crawldetail_api.crawl_details(..., status_sink)` 분류
  (`classify_fetch_error`: 네트워크·5xx → retryable(최대 5회, 이후 permanent),
  403/404/410 → permanent + 당시 목록 라벨을 `last_list_label` 에 보존,
  401/419 → run 전체 `paused(auth)`).
  `CaptureStoreUnavailable`(T4)이 올라오면 중단하고 `failed(community_store_unavailable)`.
- 끝나면 기존 크롤 완료 마커 흐름 그대로.

`crawl_manager.run_after_crawl` 의 rebuild 훅이 `--rebuild <run_id>` 명령을 보고
`on_crawl_finished(run_id)` 를 부른다: `list_complete=1 ∧ pending·retryable 0` 이면
`validating` → 영구 실패 없으면 `committing` → `completed`,
영구 실패가 있으면 사용자가 `accept_gaps` 뒤 `completed_with_gaps`.
`committing` 은 community.db 한 트랜잭션에서 `report_latest_staging(run_id)` 를
`report_latest(local_dataset_id)` 에 upsert 병합(삭제 없음, 무변경 carry-forward) +
`source_generation` 증가. 재시작 시 `running` + 만료 lease → 같은 run 재개
(`resume_on_startup`, T3a 가 `main.py` 에서 부른다).

## 증분 선정 (`core/database/database.py`)

`get_pending_detail_ids(force=False)` = 기존 SQL 후보 ∪ 벡터 `list_refetch.json` 규칙
(`should_refetch_list_item`, 13건 벡터 테스트):
목록 C_NOW 라벨(`title.상태`) vs community.db `detail_status` 라벨 비교
(None 은 `!=` 직접 비교 금지), `detail_status` 없음(마지막 rebuild 의
`failed_permanent` 이고 목록 라벨이 `last_list_label` 과 같으면 제외),
capture 재시도 ID(T4 `capture_retry_ids()`, 없으면 빈 집합 — 파일 직접 읽기 금지),
`보완_미응답='Y'`. `closed`·`supplement_open` 은 detail 표 원본만
(사용자 override·처리상태 canonical 무시).

## 스케줄러 분리 (`core/utils/scheduler.py`)

`update_jobs()` 는 크롤 job(`crawl_job_interval`, `cron_HH_MM`)만 제거·재생성하고,
끝에서 T4 `community_schedule.register_community_jobs(scheduler)` 를 호출한다
(`ImportError` 면 로그만). 크롤 스케줄러가 disabled 여도 커뮤니티 job 은 유지(S-08).
`run_crawler` 는 게이트·초기화 상태를 확인해 필요 시 건너뛴다.
(`next_run_time` 로그는 미기동 스케줄러에서도 안 죽게 `getattr` 처리.)

## 복원 전 선회전 (`core/storage/exchange.py`)

`restore()` 가 `_swap_in` 직전에
`CommunityStore.open().rotate_dataset(f"restore_{kind}")` 를 호출한다(보수적 선회전,
교체 실패해도 되돌리지 않음, S-20).

## 로컬 API (`web/routers/community_rebuild_route.py`)

- 관리자: `/settings/community/rebuild` (`GET` 상태, `POST /start·/resume·/pause·/accept-gaps`,
  CSRF·JSON·Origin 확인). `main.py` 등록은 T3a.
- 모바일 Client: `/api/v1/community/rebuild` (`GET`, `POST /start·/resume`,
  관리 허용 API 키 + `X-Community-User-Token` 을 T3a
  `community_gate.verify_client_user_token` 으로 확인 — 모듈·함수가 없으면 검사 생략).
- 응답 `no-store`, 토큰·비밀 없음.

## 코드 대조 정정

| 문서 | 코드 | 상태 |
|---|---|---|
| `rebuild.md` preparing_backup 실패 | `start()` → `failed`, 개인 DB 무변경 | 일치 (G07) |
| `interfaces.md` `rotate_dataset` 호출자 | `exchange.restore()` 가 `_swap_in` 직전 호출 | 일치 |
| `schedule.md` 커뮤니티 job id | T4 `register_community_jobs` 제공 전 — 호출 자리·멱등 재확인만 구현, fake 로 검증 | T4 대기 |
| 게이트 `require_fresh`·`verify_client_user_token`, `refresh_server_completed` | T3a·T4 제공 전 — 함수 안 import + 부재 시 통과, fake 로 검증 | T3a·T4 대기 |

## 로컬 검증 (2026-09-26)

`SAFETYREPORT_DATA_DIR=$(mktemp -d) /home/better0101/projects/safetyreport/.venv/bin/python
-m unittest discover -s tests -p "test_*.py"` → 243 passed (skip 3, 기존과 동일).
신규 `test_community_rebuild` 29 · `test_community_selection` 5 ·
`test_community_scheduler_split` 6.
