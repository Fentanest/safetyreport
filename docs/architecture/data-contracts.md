# 데이터·외부 계약

다루는 것: 설정 키, DB 테이블/컬럼, 서비스 반환 키, category 전파, 모바일 API, WebSocket, 완료 마커 파일, 크롬 확장 API.

2026-09-24 에 기존 루트 `CLAUDE.md`(725줄)에서 옮겼다. 아래 '이관 원문' 은 문구를 바꾸지 않고 옮긴 것이며, 원본 전체는 [legacy-claude-reference.md](legacy-claude-reference.md)에 있다.

## 코드 대조 정정 (기준 17df6cb)

| 원문 절 | 현재 코드 | 조치 |
|---|---|---|
| 설정 표 crawl_type | 코드는 `[Crawler] crawl_type` 이 `api` 면 api, 그 외 값은 모두 `legacy` 로 읽는다(`settings/settings.py:68-69`). 표의 `api / web` 는 부정확. | 정정 |
| 설정 표 auto_export_sheet | 코드 기본값은 `False`(`settings/settings.py:66`). 표의 `True` 는 부정확. | 정정 |
| config.ini 섹션+키 | `[MAP] kakao_rest_api_key` 가 목록에 없다(`settings/settings.py:62`, 지도 지오코딩용). | 추가 |
| get_dashboard_stats() / get_agency_stats() 키 | 현재 코드는 목록 외에 `withdrawGraphCount`, `dedupe_mode`(대시보드), `avg_days`, `total_fine_amount`, `unconfirmed(_pct)`, `avg_rating`, `rating_count`, `by_law`, `available_laws`, `has_empty_law`, `dedupe_mode`(기관 통계)를 반환한다(`services/report_stats_service.py:440-470, 603-705`). | 추가 — 기존 키는 유지 |
| 모바일 API 표 | `/summary 의 취하 필드 규칙` 목록이 표 중간에 끼어 뒤쪽 행(`/app/config` 이하)이 표로 렌더되지 않는다. 내용은 유효. | 형식만 문제 |

## 2026-09-24 통계 계약 갱신 (모바일 세션 `feature/stats-overview-api` 병합분)

이 절은 해당 브랜치가 옛 루트 `CLAUDE.md` 에 추가한 내용을 옮긴 것이다(원문 그대로). 위 "이관 원문"의 `get_agency_stats()` 키 목록보다 이 절이 최신이다.
결정 번호 S-xx 는 모바일 레포 `docs/design/statistics-spec.md` 의 결정표를 가리킨다.

**get_agency_stats() → traffic/parking/other 각각:**
```
by_agency / by_person / police_by_agency / police_by_person / other_by_agency / other_by_person
  └ agency  person  total  avg_days  total_fine_amount  fine_amount_unknown
    fines  fines_pct  warnings  warnings_pct  rejects  rejects_pct  unconfirmed  unconfirmed_pct
    in_progress  in_progress_pct  avg_rating  rating_count
by_law (법규별, 같은 필드 + law)
```
행 규칙(2026-09-24, 모바일 S-10 — `_build_stats_tables`, 모바일 `LocalDbService.buildStatsCategory` 와 같은 정의):
- 표에 넣을지는 처리상태가 아니라 값으로 정한다. 기관표 = 처리기관(trim)이 있는 신고, 담당자표 = 처리기관과 담당자가 모두 있는 신고(`''`·`미지정` 제외). 처리기관이 없으면 어느 표에도 넣지 않는다('알수없음' 행 없음).
- 배정된 처리중 신고도 들어간다. `in_progress` = 완료(수용·일부수용·불수용·기타·답변완료)도 취하도 아닌 상태, `unconfirmed` 에서 뺀다. 대시보드용 `_disposition_counts` 는 그대로다.
- `avg_days` 는 완료 신고만(이송 답변일이 붙은 처리중·취하 제외). `/stats/overview` 의 `avg_days` 도 같다.
- 표시용 반올림은 `_round_half_up`(x.x5 올림, 모바일 Dart `toStringAsFixed` 와 동일). Python `round()` 를 쓰지 않는다.

### 모바일 API 응답 필드 (/api/v1)
공통 래퍼: `status` `data` `count`

**crawl/done:** `done` `timestamp` `changed_count`
**crawl/config:** `crawl_type` `crawl_mode` `max_empty_pages`
**stats:** `traffic` / `parking` / `other` / `available_years` / `traffic_total_fine`
**stats/overview** (2026-09-24, 모바일 통계 요약용): `all` / `traffic` / `parking` / `other` 각각
`total completed accept partial reject supplement processing withdraw avg_days avg_days_count reversed_date_count undated_report_count monthly_reported[{month,count}] monthly_answered[{month,count}]`
+ `available_years` `year_basis`(='답변일') `exclude_withdraw` `dedupe_mode`.
`get_agency_stats` 와 같은 행(로딩·대표건·행 필터·취하 제외·법규)을 쓴다 — 공통 헬퍼 `_load_stats_frames` / `_apply_stats_row_filters` / `_apply_stats_law_filter`.
평균 처리일은 기관 평균을 합치지 않고 원자료에서 직접 계산(완료 상태 + 두 날짜 유효 + 차이 ≥ 0), 표본 수 함께 제공. 모바일 Standalone `LocalDbService.summarizeOverviewRows` 와 같은 정의 — 한쪽을 바꾸면 양쪽 테스트를 함께 고친다.

## 2026-09-24 사진 촬영 시각 컬럼 (주정차 과태료 추정용)

- detail/merge 3개 테이블에 `사진_첫촬영`(TEXT `YYYY-MM-DD HH:MM:SS`), `사진_끝촬영`(TEXT), `사진_촬영수`(INTEGER) 추가. `upgrade_schema()` 가 자동 ALTER.
- 의미: NULL = 아직 시도 안 함(또는 네트워크 오류로 다음 크롤링에 재시도), `사진_촬영수 = 0` = 받았지만 EXIF 촬영 시각 없음.
- 채우는 곳: `database.detail_to_sql` → `_resolve_photo_capture`(주정차 신고만, 트랜잭션 밖에서 사진 앞 128KB 스트리밍) → `services/photo_capture_time.py`.
  값은 재크롤링에서 이어받고, 변경 감지(`synced_at`, 변경 알림)에 포함하지 않는다.
- 서버↔모바일 변환: `db_backup.restore_from_mobile_db` 가 세 컬럼을 옮긴다. 모바일 `reports` 테이블과 서버 DB 가져오기 쪽도 같은 세 컬럼을 가져야 한다(PROJECT_RULES §3-1).
- API: `/api/v1/reports/*` 는 테이블 전체 컬럼을 내보내므로 세 필드가 추가된다. 대시보드 `recent_answers`/`watchlist` 레코드에도 포함.
- 추정 과태료(`services/fine_estimate.py`)는 이 컬럼과 번호판·신고 메뉴로 계산하며 DB 에 저장하지 않는다(statistics-spec §4-2).

## 2026-09-24 서버↔모바일 DB 왕복 검사 (PROJECT_RULES §3-1)

- `scripts/dev/db_roundtrip_check.py --mobile-repo <모바일 작업트리>` — 실제 변환 코드를 이어서 돌린다.
  A: 서버 S0 → 모바일 `importFromServerDb`(Dart, `test/tool/db_roundtrip_harness_test.dart`) → M1 → 서버 `restore_from_mobile_db` → S2, S0 == S2.
  B: M1 → S2 → 모바일 M3, M1 == M3. NULL/''·정수/실수 타입까지 원시 값으로 비교, 차이 1건이라도 있으면 종료코드 1.
- 서버 쪽은 `SAFETYREPORT_DATA_DIR` 임시 폴더 + fixture 모드 서브프로세스, 모바일 쪽은 sqflite FFI. 운영 DB·외부 요청 없음.
- S0 은 fixture 24건에 크롤러와 같은 값(주소정규화 3컬럼, `synced_at` 백필)과 까다로운 값(사진 시각·`사진_촬영수=0`, NULL 벌점, 빈 별점사유, 줄바꿈·따옴표, 실수 좌표, 지오코딩 캐시 ok/not_found)을 넣는다.
- 허용 차이는 하나: `mysafety_sync_meta.map_backfill_state`(서버 지도 백필 런타임 상태, 복원 직후 서버가 새로 쓰고 모바일은 의도적으로 버림).
- 알려진 정규화(실제 크롤러 출력에는 나타나지 않아 현재 데이터 손실은 없음, 운영 사본 NULL 0건 확인): 모바일 가져오기와 서버 복원이
  지오코딩 캐시 `error_message` NULL→'', 주소정규화·행정구역·지오코딩상태 NULL→''/재계산, `synced_at` NULL→가져온 시각으로 바꾼다. 저장 로직 리팩터링 때 NULL 보존으로 정리한다.
- 변환 코드(서버 `db_backup.py`, 모바일 `local_db_service.dart` import/export)나 공통 컬럼을 바꾸면 이 검사를 돌린다.

## 2026-09-24 저장 계층 재설계 R1 — 교환·복원·스키마 버전

- **저장 계약** `contracts/storage-contract.json`(모바일 레포 같은 경로에 바이트 동일). 모델·표가 어긋나면 `tests/test_storage_contract.py` 가 실패한다.
- **스키마 버전**: 서버 `PRAGMA user_version` = `core/database/database.py SCHEMA_VERSION`(현재 2). 더 새 버전 DB 는 upgrade 전에 거부. 모바일은 `LocalDbService.dbVersion`(현재 12).
- **감시목록**: 원천은 `mysafety_watchlist` 하나. title·merge 의 `감시목록` 열은 `database.refresh_watch_flags` 가 계산(merge_final·감시목록 변경 때). 서버 sync_meta 에는 'watchlist' 키를 두지 않는다(모바일은 sync_meta 'watchlist' 가 원천이라 교환 때 변환).
- **복원**(`services/db_backup.py` → `core/storage/exchange.py`): 사본에 적용 → 무결성 검사 → 백업(`data/backups/`) → 원자적 교체. 크롤링·지도 좌표 변환 중에는 409.
  모바일 DB 로 복원해도 관리자·API 키·변경 기록은 유지되고 지오코딩 캐시는 합쳐진다. 구버전 앱 DB 에 수정값·중복 판단 표가 없으면 서버 것을 유지.
- **API 값**: `/api/v1/reports/{traffic,parking,other}` 는 NULL 을 null, 정수 열(별점·synced_at·보완횟수·사진_촬영수)을 정수로 보낸다. 웹 화면 조회는 기존처럼 ''.
- **변경 알림 payload**(`crawl_changes.json`): NULL 은 '' (표시용).
- 새 표: `mysafety_report_override`(사용자 수정값), `mysafety_duplicate_decision`(중복 판단), `mysafety_change_log`·`mysafety_change_cursor`(변경 기록) — 쓰기는 R2·R5 부터.

## 2026-09-24 저장 계층 재설계 R2 — 서버 저장 규칙

- 크롤링 저장은 `core/storage/reports_repo.save_crawled`(기존 `database.detail_to_sql` 은 튜플 → `CrawledDetail` 어댑터). 신고 1건 = 트랜잭션 1개, 네트워크는 트랜잭션 전.
- **merge(화면용 표) = title + detail(사이트 원본) + `mysafety_report_override`(사용자 수정값) + 감시목록(계산) + 6개월 지난 첨부 "6개월 초과"**. 전체(`merge_final`)와 1건(`refresh_merge_rows`)이 같은 규칙.
  detail 은 사이트 원본만 담는다 — 편집기(`db_editor_service.update_record`)는 수정값 표에 쓰고 detail 을 건드리지 않는다.
- 교환은 원본으로: 모바일 가져오기는 title+detail 을 읽고(merge 아님) 수정값은 표로 따로 옮긴다.
- 변경 판정(synced_at·변경 알림): 상세 사이트 열 + category + entry_value + 본문, NULL 과 '' 는 같음. 지오코딩·사진 열은 제외(모바일 `_syncedAtTrackedKeys` 와 같음).
- 중복 판단: `mysafety_duplicate_decision` 이 원천(개별·일괄 저장이 기록). 그룹 재생성은 판단 표 → 기존 그룹 → 자동 기본값 순.
- 별점 일괄 제출·이미 참여 확인은 점수도 기록(`sync_rating_status(score=, cause=)`).
- `upgrade_schema(engine, maintenance=False)`: 크롤링 서브프로세스용 가벼운 확인(표·열·버전). 스키마 버전 4(인덱스).

## 2026-09-24 저장 계층 재설계 R6 — 시각 형식 (S-32)

형식이 여러 개인 것은 받는 쪽이 이미 그 형식으로 읽고 있어서다. 저장된 값을 바꾸지 않고, 새 필드는 아래 규칙을 따른다.

| 종류 | 형식 | 필드 |
|---|---|---|
| 기계용 기록 시각(새 필드 기본값) | 정수 Unix epoch **밀리초** | `synced_at`, `mysafety_raw_content.saved_at`, 중복 그룹·멤버·판단의 `created_at`/`updated_at`, `mysafety_report_override.updated_at`, `mysafety_change_log.created_at`, `mysafety_change_cursor.updated_at` (모바일 같은 이름 열도 같음) |
| 마지막 동기화 | ISO8601 로컬 시각, 시간대 없음 | `mysafety_sync_meta.last_sync` — 서버는 초까지, 모바일(`toIso8601String`)은 마이크로초까지. 둘 다 `datetime.fromisoformat`·`DateTime.parse` 로 읽힌다 |
| 사이트 원문 날짜 | `YYYY-MM-DD` 문자열, 시각은 `HH:MM` | `신고일`·`답변일`·`발생일자`·`발생시각`, 사진 촬영 시각 열 — 사이트가 준 값 그대로 |
| 사람이 보는 표시용 | `YYYY-MM-DD HH:MM:SS` 문자열 | `crawl_done*.json` 의 `timestamp`, `api_keys.created_at`, 기기 연결 목록 — 서버 전용·화면 표시 |
| WebSocket 메시지 | `datetime.isoformat()`(마이크로초 포함) | `/ws/events` 의 `timestamp` |

`synced_at` 의 뜻: "이 신고의 상세가 마지막으로 실제로 바뀐 시각". 2026-05-06 이전 행은 그 시각을 알 수 없어 `답변일`(없으면 `신고일`) 그날 끝으로 추정해 채웠다 —
정렬(최근 답변)용으로는 같은 뜻으로 쓰고, "언제 크롤링했나"로 쓰지 않는다. 크롤링 시각은 `last_sync`.

## 2026-09-24 저장 계층 재설계 R6 — 라우터 실행 (S-29)

- 웹·모바일 API 라우터는 await 가 없으면 `def`(FastAPI 스레드풀에서 실행)로 둔다. 본문을 읽느라 `async def` 인 라우터는 DB·파일 작업을 `run_in_threadpool` 로 넘긴다.
  판다스·SQLite 작업을 이벤트 루프에서 돌리면 복원·편집·중복 판단 저장 동안 WebSocket·미디어 스트림까지 멈춘다.
- 스레드에서 돌기 때문에 크롤링 시작·큐 적재·중지는 `crawl_control._launch_lock` 으로 한 줄로 세운다(실행 중 확인 → 로그 회전 → 프로세스 시작이 끊기지 않게).
- 스레드풀 토큰은 미디어 스트림(`iter_stream`)과 같이 쓴다(anyio 기본 40).

## 2026-09-25 중복군 응답 필드 추가 (W3)
- `duplicate_group_service.get_duplicate_groups()`(웹 관리 화면, `/api/v1/duplicates/groups`) 그룹마다 `user_decided`(판단 표에 행이 있음), `decided_at`(epoch ms), `decided_at_text`("YYYY-MM-DD HH:MM") 추가. 기존 필드는 그대로.
- 판단 표는 그룹별 **마지막** 사용자 판단만 보관한다. 여러 번의 변경 이력은 저장하지 않는다(필요하면 서버·앱 계약에 이력 표를 새로 넣어야 함).

## 이관 원문

<!-- legacy CLAUDE.md 199-307 -->
## 주요 변수명 / 필드명 정리

### 설정값 (settings.py self.xxx)
```
datapath / config_path / resultfile / resultpath / logfile / logpath / db_path
table_title / table_detail_traffic / table_detail_parking / table_detail_other
table_merge_traffic / table_merge_parking / table_merge_other
loginurl / myreporturl / mysafereporturl / titletable / remotepath
chrome_mode / remote_debug_port / headless
username / password / phone_number
telegram_token / chat_id / telegram_enabled
google_api_auth_file / google_sheet_key / google_sheet_enabled
scheduler_enabled / scheduler_mode / scheduler_interval_hours / scheduler_cron_times / scheduler_interval_start
normalize_police / exclude_withdraw / use_representative_records / auto_export_excel / auto_export_sheet
crawl_mode / crawl_type / max_empty_pages / retry_interval / max_retry_attemps
session_max_age / log_level / TZ / trusted_proxies
```

### config.ini 섹션+키
```
[SELENIUM]    remotepath / chrome_mode / remote_debug_port / headless
[LOGIN]       username / password
[TELEGRAM]    telegram_token / chat_id
[SCHEDULER]   enabled / mode / interval_hours / cron_times / interval_start
[RATING]      phone_number
[SETTINGS]    normalize_police / auto_export_excel / auto_export_sheet / crawl_mode
              exclude_withdraw / use_representative_records
              retry_interval / max_retry_attemps / max_empty_pages
              session_max_age / log_level / TZ / trusted_proxies
[Crawler]     crawl_type
[GOOGLESHEET] sheet_key
```

### DB 컬럼명
**mysafety:** `ID` `상태` `신고번호` `신고명` `신고일` `만족도조사여부`

**mysafetydetail_*/mysafetymerge_* 공통:**
```
ID  처리상태  차량번호  위반법규  범칙금_과태료  벌점
처리기관  담당자  답변일  발생일자  발생시각  위반장소
종결여부  신고내용  처리내용  지도  첨부사진  첨부파일
```
merge에는 title 컬럼(`상태` `신고번호` `신고명` `신고일` `만족도조사여부` `감시목록`)도 포함

title 컬럼에 `별점`(Integer 1~5, NULL 가능) `별점사유`(TEXT) 추가됨. merge에도 동일 컬럼 포함.

**mysafety_watchlist:** `신고번호` / **admin_users:** `username` `password_hash` `salt` / **api_keys:** `key` `name` `created_at`

### 크롤러 파싱 키 (API 응답 원시값)
```
C_A_CONTENTS  C_A_BODY  C_APP_GUBUN_NM  RN_ADRES
C_A_ADD2  C_A_ADDR_HEAD  C_A_ADDR_TAIL  C_NOW
STTEMNT_IMAGE_URL  ARR_C_FILES  answers
```
**answers[*]:** `C_MANAGER_TYPE_NM` `C_R_PROC_STAT_NM` `C_MANAGE_ORG_NAME` `C_MANAGE_MAN` `C_R_MOD_ID` `C_DATE` `C_R_MOD_DATE` `C_MANAGE_CONTENTS` `C_R_BODY`

**ARR_C_FILES[*]:** `FILE_URL` `ATCH_FILE_ID` `FILE_TY` `ORGINL_FILE_NM` `FILE_EXTSN` `EXT`

### 조회/통계 서비스 반환 딕셔너리 키

`services/data_service.py` 는 현재 `report_query_service.py`, `report_stats_service.py`,
`crawl_state_store.py` 를 재노출하는 호환 facade다. 아래 키는 실제 서비스 구현이 유지해야 하는 외부 계약이다.

**get_dashboard_stats():**
```
last_crawl_time  total
acceptCount  partialCount  rejectCount  processingCount  supplementCount  completedCount  withdrawCount  withdrawRawCount
tFineCount  tPenaltyCount  tRejectCount  tUnconfirmedCount
accept_pct  partial_pct  reject_pct  processing_pct  supplement_pct  withdraw_pct
tfine_pct  tpenalty_pct  treject_pct  tunconfirmed_pct
recent_answers  watchlist  exclude_withdraw
```

**get_traffic/parking/other/all_records():**
```
ID  신고번호  신고명  신고일  답변일  처리기관  담당자  처리상태  결과
범칙금_과태료  벌점  차량번호  위반법규  위반장소  발생일자  발생시각
신고내용  처리내용  첨부사진  첨부파일  지도  감시목록  category
```
**get_duplicate_records():** 위 + `total_count` `valid_count`

**get_agency_stats() → traffic/parking/other 각각:**
```
by_agency / by_person / police_by_agency / police_by_person / other_by_agency / other_by_person
  └ agency  person  total  fines  fines_pct  warnings  warnings_pct  rejects  rejects_pct
```

### 모바일 API 응답 필드 (/api/v1)
공통 래퍼: `status` `data` `count`

**crawl/done:** `done` `timestamp` `changed_count`
**crawl/config:** `crawl_type` `crawl_mode` `max_empty_pages`
**stats:** `traffic` / `parking` / `other` / `available_years` / `traffic_total_fine`

Flutter Report 모델 필드(fromJson 매핑) 및 모바일 상세 구조는 `safetyreport-mobile` 레포의 CLAUDE.md 참조.

### category 전파 규칙

- `category` (`traffic` / `parking` / `other`) 는 단순 표시용이 아니라
  웹 상세 모달 링크와 모바일 상세 시트 링크가 원래 카테고리 탭으로 돌아가기 위한
  구조적 계약이다.
- `get_dashboard_stats().recent_answers`, `watchlist`, `get_duplicate_records()`,
  `get_all_watchlist()` 뿐 아니라 `get_all_records()`, `search_by_vehicle()`,
  `search_by_address()`, `get_unrated_records()` 도 각 행에 `category` 를 유지해야 한다.
- 카테고리별 목록 함수는 `_get_records_from_table(..., category=...)` 를 통해
  기본 라벨을 붙이고, 합본/검색 함수는 병합 과정에서 이를 지우지 않아야 한다.

---


<!-- legacy CLAUDE.md 308-371 -->
## DB 테이블

| 테이블 | 설명 |
|--------|------|
| `mysafety` | 신고 목록 (ID, 신고번호, 신고명, 신고일, 상태 등) |
| `mysafetydetail_traffic` | 교통위반 상세 |
| `mysafetydetail_parking` | 주정차위반 상세 |
| `mysafetydetail_other` | 기타 신고 상세 |
| `mysafetymerge_traffic` | 최종 병합 (traffic) |
| `mysafetymerge_parking` | 최종 병합 (parking) |
| `mysafetymerge_other` | 최종 병합 (other) |
| `mysafety_entry_value` | 신고별 entry_value 저장 (ID, entry_value) — 카테고리 재분류 기반 |
| `mysafety_raw_content` | 신고별 원본 payload 저장 (ID, raw_content, raw_type, saved_at) |
| `mysafety_sync_meta` | key/value 메타. `last_sync`(서버 마지막 크롤링 시각, ISO8601), `watchlist`(감시목록 mirror), 기타 확장 키 |
| `mysafety_duplicate_group` | payload exact 중복군 메타데이터 (대표건, 대표건 모드, 중복 상태, 전역 반영 여부) |
| `mysafety_duplicate_member` | 중복군 멤버 목록 (report_id, category, 대표건 여부) |
| `api_keys` | 모바일 API 인증 키 |
| `admin_users` | 웹 관리자 계정 |
| `mysafety_watchlist` | 감시 목록 (신고번호) |

- `mysafetydetail_*`, `mysafetymerge_*` 는 2026-05-06부터 `synced_at INTEGER` 컬럼을 가진다.
  값은 Unix epoch milliseconds 이며, "이 detail/merge 레코드가 마지막으로 실제 반영된 시각"을 뜻한다.
- `synced_at` 는 신규 insert 또는 실제 detail 변경 시에만 갱신된다.
  내용이 같은 단건 재크롤은 기존 값을 유지해야 최근 답변 정렬이 재조회 순서로 오염되지 않는다.
- 구버전 서버 DB를 열면 `upgrade_schema()`가 `synced_at` 컬럼을 추가한 뒤 기존 row도 자동 백필한다.
  - 백필 규칙: `답변일`이 있으면 그 날짜를, 없으면 title의 `신고일`을 기준으로 epoch ms 생성
  - 날짜 문자열에 시각이 없으면 그날의 마지막 시각(23:59:59.999)으로 채워 같은 날짜끼리는 `신고번호 DESC`가 tie-break로 작동하게 한다.
- `raw_content` 는 목록/통계 테이블에 싣지 않고 `mysafety_raw_content` 별도 테이블에 보관한다.
  - 서버 상세 크롤링은 신고 본문 원문을 `raw_type='report_body'` 로 함께 저장한다. 이 row 가 있어야 reset 후 재크롤링에서도 payload exact 중복군이 다시 생긴다.
- 서버 "마지막 크롤링 시각"은 `mysafety_sync_meta.last_sync` 에 ISO8601 문자열로 저장된다.
  - `start.py` 의 `_process_and_save_results` 가 merge 직후 `mysafety_sync_meta` 에 `key='last_sync'`, `value=datetime.now().isoformat(timespec='seconds')` 로 직접 upsert 한다.
  - 모바일 `sync_engine.dart._saveSyncTime()` 와 같은 키/형식이라 서버↔모바일 DB import 시 round-trip 으로 의미가 보존된다.
  - 대시보드는 `report_stats_service.get_dashboard_stats` 가 `mysafety_sync_meta.last_sync` 를 직접 SELECT 해서 `datetime.fromisoformat(...).strftime('%Y-%m-%d %H:%M:%S')` 로 표시한다.
  - 값이 없으면 `기록 없음`. (이전의 `current_crawl.log` mtime fallback 은 제거됨)
- `--reset` 크롤링은 신고 ID 에 매여 있는 사이드카 테이블도 같이 비운다.
  - drop 대상: `mysafety*`, `mysafetydetail_*`, `mysafetymerge_*`, `mysafety_entry_value`, `mysafety_raw_content`, `mysafety_duplicate_member`, `mysafety_duplicate_group`
  - `mysafety_sync_meta` 는 통째로 drop 하지 않고 `key='watchlist'` 만 보존한 채 `last_sync` 등 나머지 키를 삭제한다.
  - `admin_users`, `api_keys`, `mysafety_watchlist` 는 보존.
- 서버-모바일 DB 변환 시 아래 항목은 round-trip 보존 대상으로 취급한다.
  - 신고 row: title/detail/merge 필드, `entry_value`, `synced_at`
  - payload 메타: `raw_content`, `raw_type`, `saved_at`
  - 모바일 메타: `mysafety_sync_meta` (`last_sync`, `watchlist`, 기타 key/value)
  - 중복군 메타: `status`, `representative_mode`, `representative_id`, `apply_globally`, `note`
  - 중복 멤버 메타: `priority_score`, `raw_match`, `field_match`, `created_at`, `updated_at`
  - 보완요청 요약: `보완횟수`, `보완_미응답`, `보완_요청_내용`, `보완_신고자_의견` (detail/merge 컬럼으로 서버/모바일 동일 키)
- `db_backup.restore_from_mobile_db()` 는 모바일 레거시 duplicate hash가 들어와도 `raw_content` 기준 SHA-256 canonical group_id로 재매핑한 뒤 저장한다.
- 보완요청은 신고 행에 **마지막 round 1세트 + 누적 횟수** 만 보존한다. 다회차 이력 전체는 저장하지 않는다.
  - detail/merge 의 신규 컬럼 4개로 표현: `보완횟수` (누적 round 수), `보완_미응답` (`Y/N`), `보완_요청_내용` (요청자/연락처/요청·완료 일시 prefix + 본문), `보완_신고자_의견`.
  - 보완요청 내용 prefix 는 `services/parser.py:_build_supplement_summary()` 가 `"보완 요청자: <name> (<phone>) · 요청 일시: ... · 완료 일시: ..."` 형식으로 조립한다. 마지막 답변자와 최종 판정자가 다를 수 있으므로 누가 이 보완을 요청했는지 본문 안에 함께 표시.
  - 레거시 모드는 `services/supplement_parser.py:parse_supplement_rounds_from_html()` 로 `splmntDivBody` round 리스트를 만든 뒤 마지막 round 만 요약에 사용한다. round 카운트는 round 리스트 길이, API 모드는 `SPLMNT_DMND_NO` 사용.
  - 마지막 완료 round 의 신고자 의견 텍스트는 기존처럼 신고 메인 행의 차량번호/발생일자·시각/위반장소를 덮어쓴다 (`supplement_parser.latest_completed_overrides()`).
  - 신고 본 row 의 `처리상태/종결여부` 는 보완요청이 열려 있으면 `보완요청 / N`, 종결 상태(취하/답변완료 등)에서는 `보완_미응답='N'` 으로 닫는다.
  - `get_pending_detail_ids()` 가 detail 의 `보완_미응답='Y'` row 를 항상 재크롤링 대상에 포함 → 다음 크롤링에서 답변/추가 round 변화가 자동 반영된다.
  - API 모드 JSON 만으로 보존되는 정보는 마지막 round 의 요청자·요청일시·요청 내용 + 신고자 의견 + 종결 여부 + 누적 횟수에 한정된다. 과거 round 의 요청자 이름까지 보고 싶다면 같은 ID 를 레거시 모드로 다시 크롤링하면 된다.
  - crawl_changes payload 의 `change_reason` 은 신고 자체가 보완요청 상태이거나 `보완_미응답='Y'` 일 때 `supplement`, 그 외는 `report`. payload 에 `supplement_count`, `supplement_open`, `보완_요청_내용`, `보완_신고자_의견` 가 함께 들어간다.
- 중복군 자동 감지는 `mysafety_raw_content.raw_content`가 있는 row만 대상으로 한다.
  - 같은 payload hash라도 `차량번호`, `category`, `entry_value`가 충돌하면 기본 상태는 `review_required` + `apply_globally=0`이다.
  - 기본 자동 감지 결과에서 충돌이 없으면 `confirmed_duplicate`, 충돌이 있으면 `review_required`로 시작한다.
  - `not_duplicate`는 “화면에서 숨김”이 아니라 “중복군 메타는 유지하되 canonical projection에서 원본 child를 모두 살려둠”을 뜻한다.
  - `representative_mode='auto'`인 그룹은 refresh 때마다 대표건이 다시 계산될 수 있고, `manual`은 저장된 대표건을 유지한다.
  - `raw_content`가 없는 과거 데이터는 자동 확정 대상이 아니라 후속 검토 후보 경로로 다룬다.

---


<!-- legacy CLAUDE.md 413-427 -->
## 설정 (config.ini / settings.py)

| 키 | 섹션 | 설명 | 기본값 |
|----|------|------|--------|
| `crawl_type` | `Crawler` | `api` / `web` | `api` |
| `crawl_mode` | `SETTINGS` | `full` / `min` (reset은 저장 안 함 → full로 저장) | `full` |
| `max_empty_pages` | `SETTINGS` | 빈 페이지 허용 횟수 | `3` |
| `normalize_police` | `SETTINGS` | 경찰 기관명 정규화 | `True` |
| `exclude_withdraw` | `SETTINGS` | 취하 데이터 숨기기 | `True` |
| `use_representative_records` | `SETTINGS` | 대표건 기준 canonical 집계를 전역 기본값으로 사용 | `True` |
| `auto_export_excel` | `SETTINGS` | 크롤링 후 엑셀 자동 저장 | `True` |
| `auto_export_sheet` | `SETTINGS` | 크롤링 후 구글 시트 자동 업로드 | `True` |

---


<!-- legacy CLAUDE.md 439-468 -->
## WebSocket 이벤트 (`/ws/events`)

WsService.kt가 `ws://<host>/ws/events?api_key=<key>` 로 영구 연결.

| 이벤트 | 방향 | 설명 |
|--------|------|------|
| `connected` | 서버→앱 | 연결 확인 |
| `ping` (30s) | 서버→앱 | 연결 유지 |
| `pong` | 앱→서버 | ping 응답 |
| `crawl_started` | 서버→앱 | 크롤링 시작 알림 |
| `crawl_finished` | 서버→앱 | 크롤링 완료 + 변경 건수 |
| `crawl_changes` | 서버→앱 | 개별 신고 변경 상세 |

### 핵심 주의: 백그라운드 스레드 브로드캐스트
배경 스레드에서 반드시 `ws_manager.broadcast_from_thread(event_type, data)` 사용.
`new_event_loop()` + `run_until_complete()` 방식은 FastAPI 메인 루프의 WebSocket 객체에 접근 불가 → 이벤트 전달 실패.

`main.py` lifespan startup에서 `ws_manager.set_main_loop(asyncio.get_event_loop())` 호출 필수.

### 메시지 형식
```json
{
  "type": "crawl_finished",
  "timestamp": "2026-04-01T23:00:00",
  "data": {"changed_count": 5}
}
```

---


<!-- legacy CLAUDE.md 469-501 -->
## 모바일 알림 파이프라인

모바일 앱 내부 구현(WsService.kt, 알림 채널 등)은 `safetyreport-mobile` 레포의 CLAUDE.md 참조.

**서버 측 완료 마커 파일 구조:**

### crawl_done.json
- `services.crawl_state_store.save_crawl_done(changed_count)` — 크롤링 완료 시 저장
- `services.crawl_state_store.get_and_clear_crawl_done()` — 읽고 즉시 삭제 (한 번만 읽힘)
- 위치: `data/crawl_done.json`

### crawl_done_ext.json (크롬 확장 전용)
- `services.crawl_state_store.save_crawl_done_ext(changed_count, changes)` — 크롤링 완료 시 저장, changes에 신고번호/신고명 포함
- `services.crawl_state_store.get_and_clear_crawl_done_ext()` — 읽고 즉시 삭제
- 위치: `data/crawl_done_ext.json`
- **주의**: `crawl_done.json`(모바일용)과 별도 파일 — 크롬 확장이 소비해도 모바일 흐름 무영향
- 저장/회전/브로드캐스트 후처리는 `services/crawl_manager.py` 의 `run_after_crawl()` 내부에서 공통 처리

### crawl_changes.json
- `services.crawl_state_store.save_crawl_changes(engine, changed_item_ids)` — 일반 신고는 `notification_kind=report`, `synced_at` 을 포함한 payload로 저장
- 일반 신고 변경 목록은 저장 전에 `synced_at DESC`, fallback `답변일 DESC`, `신고번호 DESC` 로 재정렬한다.
- 각 report payload 에는 `change_reason ('supplement' | 'report')`, `supplement_open`, `supplement_round_no`, `supplement_round_count` 도 함께 들어간다. `supplement` 는 신고가 보완요청 상태이거나 보완 history 에 열린 round 가 있을 때.
- `services.crawl_state_store.peek_crawl_changes()` — 읽기만 (삭제 안 함) → WS 브로드캐스트용
- 모바일 API 폴링(`/api/v1/crawl/results`)은 이 파일이 아니라 `core/storage/change_log.py`(변경 기록 + 기기별 읽은 위치, R5)를 읽는다. 파일을 읽고 지우던 `get_and_clear_crawl_changes()` 는 R6 에서 삭제.
  기록은 60일·5천 건까지, 180일 넘게 안 온 기기의 읽은 위치는 새 묶음을 쌓을 때 지운다.
- 위치: `data/crawl_changes.json`

### 연속 알림 큐 처리 (crawl_manager.py)
- 크롤링 중 추가 enqueue/start 요청 → `_pending_queue`에 적재
- 크롤링 완료 후 `pop_pending()` → 큐에 쌓인 신고번호 전부 묶어 자동 재실행
- `append_to_pending(report_number)`, `pop_pending()`, `pending_count()` 메서드 제공

---


<!-- legacy CLAUDE.md 536-570 -->
## 모바일 API 엔드포인트 (/api/v1)

인증: `X-API-Key` 헤더, `auth_middleware` 우회 (`_PUBLIC_PREFIXES`에 `/api/v1/` 등록).

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/summary` | 대시보드 요약 |
| GET | `/reports/traffic` | 교통위반 신고 목록 |
| GET | `/reports/parking` | 주정차위반 신고 목록 |
| GET | `/reports/other` | 기타위반 신고 목록 |
| GET | `/stats` | 기관별/담당자별 통계 |
| GET/POST | `/watchlist` | 감시 목록 조회/수정 |
| POST | `/rating/start` | 모바일 Client 별점 배치 시작 (API 키 인증) |
| POST | `/crawl/enqueue` | 신고번호 큐 등록 (알림 리스너 연동) |
| GET | `/crawl/status` | 크롤링 실행 여부 |
| GET | `/crawl/done` | 완료 마커 조회 (읽으면 삭제) |
| GET | `/crawl/results` | 변경 신고 목록 조회 (읽으면 삭제) |
| GET | `/crawl/config` | crawl_type, crawl_mode, max_empty_pages |
| POST | `/crawl/start` | 모바일에서 크롤링 시작 |
| POST | `/crawl/kill` | 크롤링 강제 중지 |
| POST | `/crawl/resume` | 비회원 로그인 완료 신호 |

- `/summary` 의 취하 필드 규칙
  - `exclude_withdraw=True` 이면 그래프/모바일 카드 기준 `withdrawCount=0`, `withdraw_pct=0`
  - 실제 원본 취하 건수는 `withdrawRawCount` 로 별도 전달
| GET | `/app/config` | 앱 설정 (`exclude_withdraw`, `normalize_police`, `use_representative_records` 등) |
| POST | `/settings` | 필터 설정 저장 (`normalize_police`, `exclude_withdraw`, `use_representative_records`) |
| GET | `/files?path=` | 서버 파일 브라우저 (logs/results 한정) |
| GET | `/files/download?path=&api_key=` | 파일 다운로드 (헤더 또는 쿼리 파라미터 인증) |
| GET | `/server/version` | 서버 버전 + GitHub 최신 버전 (모바일·크롬 확장 공통) |
| GET | `/crawl/done/ext` | 크롤링 완료 마커 조회 (크롬 확장 전용, 확인 후 자동 삭제) |
| GET | `/vehicle/{vehicle_number}` | 차량번호 부분 일치 검색 (전체 카테고리, 크롬 확장용) |

---


<!-- legacy CLAUDE.md 710-722 -->
## 크롬 확장 연동 (`web/routers/api_route.py`, `services/report_query_service.py`)

크롬 확장(`safetyreport-chromeextension`)에서 차량번호 검색 지원.

```
GET /api/v1/vehicle/{vehicle_number}
```
- 인증: `X-API-Key` 헤더
- 동작: 교통/주정차/기타 전체 merge 테이블에서 차량번호 부분 일치 검색, 신고번호 역순 정렬
- `search_by_vehicle(engine, vehicle_number)` — 현재 구현은 `report_query_service.py`, `data_service.py`는 호환 재노출만 담당

---
