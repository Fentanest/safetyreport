# 나만의 안전신문고 — 프로젝트 컨텍스트

안전신문고 민원 관리 시스템. FastAPI 웹서버 구조.
모바일 앱은 별도 레포: `/home/better0101/projects/safetyreport-mobile` (git: Fentanest/safetyreport-mobile)

## 작업 규칙
- 작업 완료 후: 구조 변경은 CLAUDE.md, 작업 이력은 CHANGELOG.md에 기록 + 코드 git 커밋
- 구조/운영 메모는 CLAUDE.md, 작업/버그/세션 이력은 CHANGELOG.md로 분리 관리
- `CLAUDE.md`, `CHANGELOG.md`는 git 추적 대상이고, `REFACTOR.md`는 로컬 원문 보관용이다.

---

## 디렉토리 구조

```
/
├── .github/workflows/
│   ├── build.yml                  # Windows/Linux/macOS(x64+arm64) 빌드 + Release 생성
│   ├── build-windows-manual.yml   # Windows 수동 테스트 빌드 (artifact only)
│   ├── build-linux-manual.yml     # Linux 수동 테스트 빌드 (artifact only)
│   ├── build-macos-x64-manual.yml # macOS x64 수동 테스트 빌드 (artifact only)
│   └── build-macos-arm64-manual.yml # macOS arm64 수동 테스트 빌드 (artifact only)
├── core/
│   ├── crawler/
│   │   ├── direct_login.py       # curl_cffi + RSA + OAuth 기반 직접 로그인
│   │   ├── driv.py               # Selenium Chrome driver 생성 (desktop/remote/hub)
│   │   ├── login.py              # Selenium 로그인 UI 처리
│   │   ├── crawltitle_api.py     # API 목록 크롤러
│   │   ├── crawldetail_api.py    # API 상세 크롤러
│   │   ├── crawltitle.py         # 레거시 Selenium 목록 크롤러
│   │   └── crawldetail.py        # 레거시 Selenium 상세 크롤러
│   ├── database/
│   │   ├── database.py           # SQLAlchemy 테이블 정의 + DB 쿼리
│   │   └── models.py             # 테이블 모델/metadata 보조 정의
│   └── utils/
│       ├── logger.py            # LoggerFactory (web/crawl/star 로거 분리)
│       ├── export.py            # Excel/Google Sheets 내보내기
│       ├── path_utils.py        # resource_path / frozen 판별 / UTF-8 보정
│       ├── templating.py        # 중앙 집중식 Jinja2Templates
│       ├── message_formatter.py # 텔레그램 메시지 포맷
│       ├── notifier.py          # 텔레그램 알림 발송
│       ├── scheduler.py         # 자동 스케줄러
│       ├── security.py          # config 암복호화 / 세션 키 관리
│       └── updater.py           # GitHub Releases 자동 업데이트
├── services/
│   ├── data_service.py          # DB 조회/집계 (대시보드, 필터, crawl_done 등)
│   ├── db_backup.py             # DB checkpoint(WAL/SHM 정리) + 서버/모바일 DB 자동 감지/변환
│   ├── crawl_manager.py         # 크롤링 프로세스 싱글톤 (충돌 방지)
│   ├── parser.py                # HTML/JSON 파싱 (과태료, 처리상태 등)
│   ├── satisfaction_fetcher.py  # 만족도조사 점수+사유 조회 (HTTP + Selenium)
│   ├── star_rating_service.py   # 별점 배치 처리
│   ├── sunwi_fetcher.py         # 안전신문고 통계 API 수집/대분류-소분류 Top5 CSV 가공 유틸
│   ├── sunwi_service.py         # 행정구역별 안전신문고 Top5 수집/캐시/CSV 저장
│   └── ws_manager.py            # WebSocket 클라이언트 연결 관리 싱글톤
├── web/
│   ├── routers/
│   │   ├── api_route.py         # /api/v1/** 모바일 API
│   │   ├── auth_route.py        # /login, /logout 인증
│   │   ├── backup_route.py      # /backup/** DB 백업/복원
│   │   ├── crawl.py             # /crawl/** 웹 크롤링 제어
│   │   ├── dashboard.py         # / 대시보드
│   │   ├── data.py              # /data/** 데이터 조회
│   │   ├── db_editor_route.py   # DB 수정/수동 편집 UI
│   │   ├── devices_route.py     # /devices/** 기기 연동 (API 키 + WS 연결 현황)
│   │   ├── file_browser_route.py # /files/** 파일 브라우저
│   │   ├── rating_route.py      # /rating/** 별점 관리
│   │   ├── settings_route.py    # /settings/** 설정 페이지
│   │   ├── stats.py             # /stats/** 통계
│   │   ├── watchlist_route.py   # /watchlist/** 감시목록
│   │   └── ws_route.py          # /ws/events WebSocket 엔드포인트
│   ├── static/                  # favicon, logo 등 정적 파일
│   └── templates/               # Jinja2 HTML 템플릿
├── scripts/
│   ├── build/
│   │   ├── build_exe.py         # PyInstaller 빌드 스크립트
│   │   └── check_macos_universal2.py # Mach-O 아키텍처 진단 스크립트
│   └── debug/
│       ├── extractor.py         # API+Selenium 비교 테스터 (DB 갱신 없음)
│       ├── merge.py             # 수동 merge 유틸
│       └── save.py              # 수동 저장 유틸
├── settings/settings.py         # AppSettings 싱글톤 (config.ini 기반)
├── main.py                      # FastAPI 서버 진입점 (lifespan, 라우터 등록)
└── start.py                     # 크롤링 실행 스크립트 (서브프로세스)
```

모바일 앱 구조는 `safetyreport-mobile` 레포의 CLAUDE.md 참조.

---

## 현재 주요 구조 요약

- FastAPI 웹앱은 `main.py`에서 기동하고, 실제 크롤링은 `start.py`를 **별도 서브프로세스**로 실행한다.
- 설정 저장/조회는 `settings/settings.py`의 `AppSettings` 싱글톤을 중심으로 돌고, 실데이터는 `data/config.ini`, `data/data.db`, `data/auth/*`에 쌓인다.
- 모바일 API는 `web/routers/api_route.py`, 웹 UI는 `web/routers/*.py` + `web/templates/*.html` 조합으로 구성된다.
- 크롤링은 크게 세 갈래다.
  - `legacy`: Selenium 로그인 + Selenium HTML 파싱
  - `api`: `direct_login` + `curl_cffi` API 호출
  - `api fallback`: direct login 실패 시 Selenium 로그인 후 브라우저 컨텍스트 `$.get` API 호출
- `legacy` 상세 파서는 처리결과가 여러 개일 때 마지막 `처리결과` 테이블을 최신 답변으로 사용한다.
- `legacy` 목록 파서는 페이지 전환 중 `stale element reference`가 나면 같은 페이지를 다시 읽도록 재시도한다.
- 비회원 모드는 direct login을 타지 않고, Chrome 창에서 사용자가 로그인한 뒤 `재개` 신호를 기다리는 수동 흐름이다.
- `sunwi_service`는 로그인 없이 안전신문고 통계 API를 별도로 호출하고, 서버 시작 후 즉시 1회 + 이후 3시간마다 대분류/소분류 기준 행정구역 Top5를 갱신한다.
- 빌드 계층은 `scripts/build/build_exe.py`와 `.github/workflows/*.yml`이 담당한다.
  - `build.yml`: 정식 릴리즈 (macOS x64 + arm64 포함)
  - `build-windows-manual.yml`: 태그 체크 없이 수동으로 Windows 아티팩트 생성
  - `build-linux-manual.yml`: 태그 체크 없이 수동으로 Linux 아티팩트 생성
  - `build-macos-x64-manual.yml`: 태그 체크 없이 수동으로 macOS x64 아티팩트 생성
  - `build-macos-arm64-manual.yml`: 태그 체크 없이 수동으로 macOS arm64 아티팩트 생성

---

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
normalize_police / exclude_withdraw / auto_export_excel / auto_export_sheet
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
              exclude_withdraw / retry_interval / max_retry_attemps / max_empty_pages
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

### data_service.py 반환 딕셔너리 키

**get_dashboard_stats():**
```
last_crawl_time  total
acceptCount  partialCount  rejectCount  processingCount  completedCount  withdrawCount
tFineCount  tPenaltyCount  tRejectCount  tUnconfirmedCount
accept_pct  partial_pct  reject_pct  processing_pct  withdraw_pct
tfine_pct  tpenalty_pct  treject_pct  tunconfirmed_pct
recent_answers  watchlist  exclude_withdraw
```

**get_traffic/parking/other/all_records():**
```
ID  신고번호  신고명  신고일  답변일  처리기관  담당자  처리상태  결과
범칙금_과태료  벌점  차량번호  위반법규  위반장소  발생일자  발생시각
신고내용  처리내용  첨부사진  첨부파일  지도  감시목록
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

---

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
| `api_keys` | 모바일 API 인증 키 |
| `admin_users` | 웹 관리자 계정 |
| `mysafety_watchlist` | 감시 목록 (신고번호) |

---

## 크롤링 파이프라인 (start.py)

```
main()
  → 로그인 전략 결정
      → nonmember      → legacy 강제 + Selenium 수동 로그인 대기
      → crawl_type=api → direct_login 시도
          → 성공       → driver 없이 API 호출
          → 실패       → Selenium 로그인 후 브라우저 API fallback
      → crawl_type=legacy → Selenium 회원 로그인 강제
  → _run_crawling_process()
      → API 경로
          → crawltitle_api.crawl_titles(browser_fallback 여부 반영)
          → crawldetail_api.crawl_details(browser_fallback 여부 반영)
      → 레거시 경로
          → crawltitle.crawl_titles()
          → crawldetail.crawl_details()
      → database.title_to_sql()
      → (큐 모드) extract_ids_from_queue()
          → missing_rnums 있으면 최대 100페이지 단건 크롤링으로 탐색
          → 발견 즉시 detaillist 추가, 모두 찾으면 조기 종료
      → database.deatil_to_sql()
  → _process_and_save_results()
      → database.merge_final()
      → save_crawl_done()    # crawl_done.json 저장 → 모바일 폴링용
      → save_crawl_changes() # 변경 목록 저장
      → (auto_export_excel) save_to_excel()
      → (auto_export_sheet) save_to_google_sheet()
```

- `start.py`는 FastAPI와 **별도 서브프로세스**로 실행 → ws_manager 싱글톤에 직접 접근 불가
- 크롤링 완료 알림은 FastAPI의 `wait_and_rotate` 배경 스레드가 완료 마커 확인 후 브로드캐스트
- `login.py`는 Selenium 회원 로그인 담당, `direct_login.py`는 API용 직접 로그인 담당으로 역할을 분리한다.

---

## 설정 (config.ini / settings.py)

| 키 | 섹션 | 설명 | 기본값 |
|----|------|------|--------|
| `crawl_type` | `Crawler` | `api` / `web` | `api` |
| `crawl_mode` | `SETTINGS` | `full` / `min` (reset은 저장 안 함 → full로 저장) | `full` |
| `max_empty_pages` | `SETTINGS` | 빈 페이지 허용 횟수 | `3` |
| `normalize_police` | `SETTINGS` | 경찰 기관명 정규화 | `True` |
| `exclude_withdraw` | `SETTINGS` | 취하 데이터 숨기기 | `True` |
| `auto_export_excel` | `SETTINGS` | 크롤링 후 엑셀 자동 저장 | `True` |
| `auto_export_sheet` | `SETTINGS` | 크롤링 후 구글 시트 자동 업로드 | `True` |

---

## 로그 시스템

- `data/logs/current_crawl.log` — 현재 크롤링 로그 (라이브)
- `data/logs/crawl_{ts}.log` — 완료된 크롤링 백업
- `data/logs/current_rating.log` — 현재 별점 작업 로그
- `data/logs/star_{ts}.log` — 완료된 별점 로그 백업
- `LoggerFactory.star_log` — `set_star_log_file(path)` 로 파일 핸들러 동적 교체
- 웹/모바일 모두 WebSocket(`/crawl/ws/logs`)으로 실시간 스트리밍 가능

---

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

## 모바일 알림 파이프라인

모바일 앱 내부 구현(WsService.kt, 알림 채널 등)은 `safetyreport-mobile` 레포의 CLAUDE.md 참조.

**서버 측 완료 마커 파일 구조:**

### crawl_done.json
- `save_crawl_done(changed_count)` — 크롤링 완료 시 저장
- `get_and_clear_crawl_done()` — 읽고 즉시 삭제 (한 번만 읽힘)
- 위치: `data/crawl_done.json`

### crawl_done_ext.json (크롬 확장 전용)
- `save_crawl_done_ext(changed_count, changes)` — 크롤링 완료 시 저장, changes에 신고번호/신고명 포함
- `get_and_clear_crawl_done_ext()` — 읽고 즉시 삭제
- 위치: `data/crawl_done_ext.json`
- **주의**: `crawl_done.json`(모바일용)과 별도 파일 — 크롬 확장이 소비해도 모바일 흐름 무영향
- 완료 핸들러 3곳에서 모두 저장: `crawl.py:wait_and_rotate_log`, `crawl.py:_wait`, `api_route.py:_run_after_crawl`

### crawl_changes.json
- `save_crawl_changes(changed_item_ids)` — `[{"id": ..., "change_type": "신규"|"변경", "신고내용": ..., "처리내용": ...}]` 형태로 저장
- `peek_crawl_changes()` — 읽기만 (삭제 안 함) → WS 브로드캐스트용
- `get_and_clear_crawl_changes()` — 읽고 즉시 삭제 → 모바일 API 폴링용
- 위치: `data/crawl_changes.json`

### 연속 알림 큐 처리 (crawl_manager.py)
- 크롤링 중 추가 enqueue/start 요청 → `_pending_queue`에 적재
- 크롤링 완료 후 `pop_pending()` → 큐에 쌓인 신고번호 전부 묶어 자동 재실행
- `append_to_pending(report_number)`, `pop_pending()`, `pending_count()` 메서드 제공

---

## 파싱 규칙 (services/parser.py)

### 과태료 자동 파싱
- 신고 유형이 "버스전용차로 위반", "쓰레기, 폐기물", "불법주정차신고" 이고 `처리상태 == "수용"` → `범칙금_과태료 = "과태료"`
- **예외**: `process_status == "취하"` 이면 과태료 설정 안 함. 취하 확정 시 `penalty_amount`, `penalty_points` 초기화.

### 불수용 키워드 강제 교정
`['부득이하게', '종결합니다', '처벌이 어려운 점', '처분이 불가']` 포함 시 → 불수용 + 범칙금 초기화

### 경고 키워드
`['교통질서 안내장', '훈방권', '12대 중과실', ...]` — 범칙금 없을 때 '경고' 설정

---

## 웹 대시보드 카드 → 상세 URL

| 카드 | URL |
|------|-----|
| 총 신고 | `/data/all` |
| 처리중 | `/data/all?status=처리중` |
| 답변완료 | `/data/all?status=완료` |
| 취하 | `/data/all?status=취하` |
| 수용 | `/data/all?status=수용` |
| 일부수용 | `/data/all?status=일부수용` |
| 불수용 | `/data/all?status=불수용` |
| 주정차위반 | `/data/parking` |
| 과태료 | `/data/traffic?fine=과태료` |
| 경고/범칙금 | `/data/traffic?fine=경고` |
| 교통 불수용 | `/data/traffic?status=불수용` |
| 미확인 | `/data/traffic?fine=미확인` |

---

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
| POST | `/crawl/enqueue` | 신고번호 큐 등록 (알림 리스너 연동) |
| GET | `/crawl/status` | 크롤링 실행 여부 |
| GET | `/crawl/done` | 완료 마커 조회 (읽으면 삭제) |
| GET | `/crawl/results` | 변경 신고 목록 조회 (읽으면 삭제) |
| GET | `/crawl/config` | crawl_type, crawl_mode, max_empty_pages |
| POST | `/crawl/start` | 모바일에서 크롤링 시작 |
| POST | `/crawl/kill` | 크롤링 강제 중지 |
| POST | `/crawl/resume` | 비회원 로그인 완료 신호 |
| GET | `/app/config` | 앱 설정 (exclude_withdraw, normalize_police 등) |
| POST | `/settings` | 필터 설정 저장 (normalize_police, exclude_withdraw) |
| GET | `/files?path=` | 서버 파일 브라우저 (logs/results 한정) |
| GET | `/files/download?path=&api_key=` | 파일 다운로드 (헤더 또는 쿼리 파라미터 인증) |
| GET | `/server/version` | 서버 버전 + GitHub 최신 버전 (모바일·크롬 확장 공통) |
| GET | `/crawl/done/ext` | 크롤링 완료 마커 조회 (크롬 확장 전용, 확인 후 자동 삭제) |
| GET | `/vehicle/{vehicle_number}` | 차량번호 부분 일치 검색 (전체 카테고리, 크롬 확장용) |

---

## 주요 아키텍처 결정 사항

### 멀티-모드 디스패처
`main.py`가 `--mode` 인자에 따라 Web Server / Bot / Crawler / Notifier로 분기.
PyInstaller 단일 바이너리 배포 시 서브프로세스가 `sys.executable` 재호출.

### 리소스 경로 (`path_utils.py`)
- Frozen: `sys._MEIPASS` 또는 `os.path.dirname(sys.executable)` 기준
- Dev: 프로젝트 루트 기준

### DB 자동 마이그레이션
`upgrade_schema()` — 앱 시작 시 누락 컬럼 자동 `ALTER TABLE`.

### API 기반 크롤러 (crawltitle_api)
`page_size=200` 으로 수천 건을 수 초 내 스캔. Selenium DOM 파싱 대비 ~10배 속도 향상.

### 별점 2단계 API
1. GET으로 이미 참여 완료 여부 확인 → 완료건 스킵
2. POST로 별점 제출
API 차단 대비 Selenium 백업 코드 주석 보존.

### Watchlist 독립 테이블
메인 테이블 스키마 변경 없이 감시 기능 분리. Join으로 효율적 추적.

### 웹 사이드바 설정 섹션
- **앱 설정 (/settings)**: 1. 시스템 및 서버 설정 / 2. 크롤링 설정 / 3. 외부 연동 키 설정
  - 1. 시스템: 로그인 계정 → 별점 휴대폰 번호 → 데이터 필터 → 세션/프록시
  - 2. 크롤링: 크롬 구동 방식 → 크롤링 후 자동 저장 → 고급 설정 → 자동 스케줄러
  - 3. 외부 연동: 텔레그램 → 구글 시트 URL → 구글 JSON 인증 파일 업로드 (fetch 방식)
- **관리자 계정 변경 (/settings/admin)**: 아이디/비밀번호 변경 전용 페이지
- **기기 연동 (/devices)**: API 키 생성/삭제 + 현재 WebSocket 연결 기기 목록
- **데이터 수정 (/db-editor)**: mysafetymerge 테이블 조회·수정 → mysafety + mysafetydetail 역동기화 (교통/주정차/기타 탭)

### 세션 인증 미들웨어
- `_PUBLIC_PATHS`: `/login`, `/setup`, `/logout`, `/health` 세션 우회
- `_PUBLIC_PREFIXES`: `/static/`, `/api/v1/`, `/ws/` 등 세션 우회
- `/api/v1/` → `X-API-Key` 별도 인증
- AJAX/fetch 요청은 302 대신 401 JSON 반환
- `/health` → 인증 없이 `{"status": "ok"}` 반환 (cloudflared health check 전용)

### 리버스 프록시 지원 (trusted_proxies)
`config.ini` `[SETTINGS] trusted_proxies` 에 쉼표 구분 IP 목록 저장.
`main.py` 모듈 레벨에서 `uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware` 조건부 적용.
설정 변경 후 **서버 재시작 필요** (미들웨어는 앱 시작 시 1회 등록).

### 주정차위반 카테고리 (crawldetail_api.py, crawldetail.py)
크롤링 시 `entry_value` 기준으로 3분류:
- `"자동차·교통위반"` in entry_value → `traffic`
- `"불법주정차신고"` in entry_value → `parking`
- 그 외 → `other`

entry_value는 "본 신고는 안전신문고 앱의 **(entry_value)** 메뉴로 접수된 신고입니다" 패턴에서 추출.
주정차위반 예시: `불법주정차신고-기타 불법주정차`

주정차위반 파싱 규칙:
- 수용 시 과태료: parser.py의 `"불법주정차신고"` in entry_value 조건 그대로 적용
- 취하 시: penalty 초기화 (기타위반과 동일)
- `"미확인"` penalty 로직은 `"자동차·교통위반"` 조건에만 해당 → 주정차에 미적용 (정상)

### 파서 첨부파일 URL (services/parser.py)
`FILE_URL`이 상대경로(`/fileDown/singo/...`)로 오는 경우 `https://www.safetyreport.go.kr` 프리픽스 자동 추가.
`STTEMNT_IMAGE_URL`도 동일하게 처리. (텔레그램 첨부 URL 깨짐 버그 수정)

### 첨부사진/파일 URL 구분자
DB에는 `\n` 으로 구분 저장됨. 웹 `data_table.html`의 `renderAttach()`는 `d.split('\n')`으로 파싱.
모바일 측 파싱은 `safetyreport-mobile` 레포 참조.

### 웹 통계 탭 구조 (stats.html)
2행 버튼 UI. 1행: 교통위반 / 주정차위반 / 기타위반. 2행: 기관별 / 담당자별 / 경찰 기관 / 경찰 담당자 / 비경찰 기관 / 비경찰 담당자.
Bootstrap tab 제거 → 커스텀 show/hide (`stats-pane` 클래스). 선택 상태 sessionStorage에 저장.
`get_agency_stats()` 반환값: `{"traffic": {...}, "parking": {...}, "other": {...}}`.
각 카테고리별 탭 클릭 시 해당 `/data/{category}?agency=...&agencyExact=true` 로 이동.

### 웹 통계 agencyExact 파라미터 (stats.py, data.py)
`agencyExact=True` 시 `df['처리기관'] == agency` 정확히 일치 필터 적용.
통계 행 클릭 링크에는 `&agencyExact=true` 자동 포함. 직접 검색 시는 기본값 `false` (contains).

### 웹 상세검색 문법 (data_table.html)
- 상세검색 상단에 `&` = AND, `,` = OR 안내 문구 표시.
- `차량번호`, `신고번호`, `신고명`, `위반법규`, `담당자`, `위반장소`, `처리기관`, `범칙금_과태료`, `별점사유`, `신고내용`, `처리내용`은 DataTables `ext.search` 커스텀 필터에서 같은 문법으로 처리.
- `처리상태`는 `_tableData`(DB에서 내려온 현재 레코드)에서 distinct 값을 추출해 다중선택 드롭다운으로 렌더링.
- `별점`은 `없음`, `1~5점` 다중선택 드롭다운으로 렌더링.
- 두 드롭다운 모두 선택된 항목 우측에 초록 `v`를 표시.

### 웹 통계 상세검색 문법 (stats.html, services/data_service.py)
- `처리기관`, `신고명`, `위반장소`는 `_parse_and_or_groups()` / `_matches_and_or_text()` / `_apply_text_query()` 헬퍼로 같은 `&` / `,` 문법 처리.
- `agencyExact`는 단일어 입력일 때만 exact match를 사용하고, `&` 또는 `,`가 포함되면 AND/OR 부분검색 규칙이 우선한다.

### 웹 첨부파일 인라인 미디어 (data_table.html)
`renderAttach()` 버튼에 `data-type="photo"|"file"` 추가.
클릭 시 Bootstrap 모달(`#attachModal`)에서 이미지/동영상 인라인 표시, 기타는 다운로드 버튼.
`<img>` → 인라인 표시, `<video controls>` → 인라인 재생, 기타 → 다운로드 버튼만.

### ARM64 빌드 (Dockerfile.build, driv.py)
- `FROM --platform=linux/arm64` 상수 금지 → CLI `--platform linux/arm64` 전달
- `upx-ucl` ARM64 미지원 → `apt-get install -y upx-ucl 2>/dev/null || true`
- `FROM scratch AS exporter` 패턴으로 단일 명령 바이너리 추출
- `driv.py`: `platform.machine()` 이 `aarch64`/`arm64` 이면 `/usr/bin/chromedriver` 사용

---

## 빌드

### Android APK
`safetyreport-mobile` 레포의 CLAUDE.md 참조.

### PyInstaller (서버 바이너리)
`scripts/build/build_exe.py` — Windows/Linux 단일 바이너리. 미사용 stdlib 제외, UPX 압축.

---

## 디버그 extractor (`scripts/debug/extractor.py`)

### 사용법
```bash
python scripts/debug/extractor.py SPP-2604-1234567   # 신고번호
python scripts/debug/extractor.py 59216726 40871819  # 내부 ID 다중
```

### 기능
- 신고번호(SPP-xxx) → DB 조회로 내부 ID 자동 변환, 다중 ID 순차 처리
- **API 방식** + **Selenium 방식** 둘 다 크롤링하여 결과 비교
- 출력 파일 5종 (`data/logs/`):
  - `{id}_api_raw.json` — API 원시 응답
  - `{id}_api_parsed.txt` — API 파싱 결과
  - `{id}_legacy_raw.html` — Selenium 전체 페이지 소스
  - `{id}_legacy_parsed.txt` — Selenium 파싱 결과
  - `{id}_diff.txt` — 두 방식 파싱 차이 자동 비교
- **DB 갱신 없음** — 순수 테스터

### 주요 설계: `_create_debug_driver()`
- **Docker** (`/.dockerenv`): 이미지 내장 Chromium + 시스템 chromedriver 직접 사용 (Hub 미사용)
  - 같은 컨테이너에서 Hub 통신 시 네트워크 스파이크 → Cloudflare 502 유발하므로 Hub 우회
- **비Docker**: chrome_mode 설정(hub/remote/desktop) 그대로 따름
- `remote` 모드(비Docker 한정): `driver.quit()` 호출 안 함 → 공유 Chrome 유지

---

## 크롬 확장 연동 (`web/routers/api_route.py`, `services/data_service.py`)

크롬 확장(`safetyreport-chromeextension`)에서 차량번호 검색 지원.

```
GET /api/v1/vehicle/{vehicle_number}
```
- 인증: `X-API-Key` 헤더
- 동작: 교통/주정차/기타 전체 merge 테이블에서 차량번호 부분 일치 검색, 신고번호 역순 정렬
- `search_by_vehicle(engine, vehicle_number)` — `data_service.py`에 추가

---

## 변경 이력
- 작업/버그/세션 기록은 `CHANGELOG.md`에 정리한다.
- 2026-04-30에 기존 작업 이력 섹션을 CLAUDE.md에서 분리했다.
