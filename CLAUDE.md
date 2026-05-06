# 나만의 안전신문고 — 프로젝트 컨텍스트

안전신문고 민원 관리 시스템. FastAPI 웹서버 구조.
모바일 앱은 별도 레포: `/home/better0101/projects/safetyreport-mobile` (git: Fentanest/safetyreport-mobile)

## 작업 규칙
- 작업 완료 후: 구조 변경은 CLAUDE.md, 작업 이력은 CHANGELOG.md에 기록 + 코드 git 커밋
- 구조/운영 메모는 CLAUDE.md, 작업/버그/세션 이력은 CHANGELOG.md로 분리 관리
- `CLAUDE.md`에는 프로젝트 구조, 작동 방식, 운영상 주의점만 남긴다.
- `README.md`, `CLAUDE.md`, `CHANGELOG.md`만 주요 문서로 git 추적한다.
- 그 외 계획/메모용 `md` 파일은 로컬 보관용으로 두고 추적하지 않는다.

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
│   │   ├── api_client.py        # direct login 세션 + Selenium 브라우저 fallback 공용화
│   │   ├── title_pipeline.py    # API/legacy 공용 목록 row 정규화
│   │   ├── detail_pipeline.py   # API/legacy 공용 상세 row 정규화 + 만족도 보강
│   │   ├── direct_login.py       # curl_cffi + RSA + OAuth 기반 직접 로그인
│   │   ├── driv.py               # Selenium Chrome driver 생성 (desktop/remote/hub)
│   │   ├── login.py              # Selenium 로그인 UI 처리
│   │   ├── crawltitle_api.py     # API 목록 크롤러
│   │   ├── crawldetail_api.py    # API 상세 크롤러
│   │   ├── crawltitle.py         # 레거시 Selenium 목록 크롤러
│   │   └── crawldetail.py        # 레거시 Selenium 상세 크롤러
│   ├── database/
│   │   ├── engine.py             # 공용 SQLAlchemy engine 생성/재사용
│   │   ├── database.py           # SQLAlchemy 테이블 정의 + DB 쿼리 + merge/save 보조
│   │   └── models.py             # 테이블 모델/metadata 보조 정의
│   └── utils/
│       ├── logger.py            # LoggerFactory (web/crawl/star 로거 분리)
│       ├── export.py            # Excel/Google Sheets 내보내기
│       ├── path_utils.py        # resource_path / frozen 판별 / UTF-8 보정
│       ├── templating.py        # 중앙 집중식 Jinja2Templates
│       ├── message_formatter.py # 텔레그램 메시지 포맷
│       ├── notifier.py          # 텔레그램 알림 발송
│       ├── retry.py             # 공용 재시도 횟수/백오프 설정 헬퍼
│       ├── scheduler.py         # 자동 스케줄러
│       ├── security.py          # config 암복호화 / 세션 키 관리
│       └── updater.py           # GitHub Releases 자동 업데이트
├── services/
│   ├── crawl_control.py         # 크롤링 시작/중지/재개, 큐 파일, 로그 헤더/회전 공용화
│   ├── crawl_log_service.py     # current_crawl.log 경로/회전 공용 헬퍼 (순환 import 방지)
│   ├── crawl_state_store.py     # crawl_done / crawl_done_ext / crawl_changes JSON 상태 저장소
│   ├── data_service.py          # 기존 import 호환 facade (query/stats/state 재노출)
│   ├── db_backup.py             # DB checkpoint(WAL/SHM 정리) + 서버/모바일 DB 자동 감지/변환
│   ├── export_service.py        # 엑셀/구글시트 export 흐름 조립
│   ├── file_service.py          # 웹/API 파일 브라우저 공용 로직
│   ├── crawl_manager.py         # 크롤링 프로세스 싱글톤 (충돌 방지)
│   ├── duplicate_group_service.py # raw_content 기반 중복군/대표건 관리 + canonical projection
│   ├── media_proxy_service.py   # 원격 첨부 동영상/미디어 스트리밍 프록시
│   ├── parser.py                # HTML/JSON 파싱 (과태료, 처리상태 등)
│   ├── rating_service.py        # 모바일/웹 별점 batch 시작 + 현재 별점 로그 준비
│   ├── report_query_service.py  # 목록/검색/감시목록/중복차량 조회
│   ├── report_stats_service.py  # 대시보드/기관 통계/연도 목록
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
│   │   ├── duplicate_route.py   # /duplicates/manage 중복 신고 관리 UI
│   │   ├── file_browser_route.py # /file-browser/** 파일 브라우저
│   │   ├── media_route.py       # /media/proxy 원격 미디어 프록시
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
- 모바일 API는 `web/routers/api_route.py`, 웹 UI는 `web/routers/*.py` + `web/templates/*.html` 조합으로 구성되며, 실제 공통 작업은 `services/` 계층으로 최대한 이동했다.
- 크롤링은 크게 세 갈래다.
  - `legacy`: Selenium 로그인 + Selenium HTML 파싱
  - `api`: `direct_login` + `curl_cffi` API 호출
  - `api fallback`: direct login 실패 시 Selenium 로그인 후 브라우저 컨텍스트 `$.get` API 호출
- API/legacy 상세/목록 파싱 결과는 `title_pipeline.py`, `detail_pipeline.py`로 공통 row 스키마에 맞춘다.
- 조회 계층은 `report_query_service.py`, 통계 계층은 `report_stats_service.py`, 크롤링 상태 파일 계층은 `crawl_state_store.py`로 분리되었고, `data_service.py`는 기존 import 경로 호환용 facade만 남겼다.
- 라우터는 가능한 얇게 유지하고, 크롤링 제어/파일 브라우저/별점 시작은 각각 `crawl_control.py`, `file_service.py`, `rating_service.py`를 통해 공통 처리한다.
- 데이터 수정 화면과 API는 `db_editor_service.py`가 스키마/조회/저장을 맡고, 웹 목록은 `신고번호 DESC` 표형 리스트를 기본 UI로 사용한다.
- 중복 신고 관리는 `duplicate_group_service.py`가 맡는다.
  - `mysafety_raw_content.raw_content`가 자동 중복 감지의 source of truth다.
  - 같은 payload hash를 가진 신고를 중복군으로 묶고, 중복 상태와 대표건 선정 모드를 별도로 관리한다.
  - 중복 상태는 `review_required`, `confirmed_duplicate`, `not_duplicate` 세 가지다.
  - 대표건 모드는 `auto` 또는 `manual` 이다.
  - `auto` 모드에서는 refresh 때마다 우선순위(`과태료 > 경고/범칙금 > 처리상태 > 답변일 > synced_at > 신고번호`)로 대표건을 다시 고른다.
  - `manual` 모드에서는 사용자가 저장한 대표건을 유지한다.
  - 단, 저장 요청이 `auto` 모드더라도 사용자가 auto 추천값이 아닌 다른 child를 직접 대표건으로 선택해 저장하면, 서버는 그 의도를 우선해 자동으로 `manual`로 승격시킨다.
  - 조회/통계는 `raw` 또는 `canonical` projection으로 대표건 기준 집계를 선택할 수 있다.
  - `not_duplicate` 상태 그룹은 중복 신고 관리 메뉴에서는 유지하되, canonical projection에서는 일반 원본 신고처럼 모두 반영한다.
  - child 표는 `신고번호 DESC` 순이며, `ID`는 안전신문고 링크, `신고번호`는 내부 상세 모달 링크다.
  - child 표의 `신고메뉴` 컬럼은 `entry_value`를 보여준다.
  - 웹 중복 신고 관리의 일괄 처리 바는 `상태 변경`과 `대표건 선정`을 동시에 일괄 적용할 수 있다.
- `crawl_state_store.py`의 `crawl_done_ext.json`은 크롬 익스텐션 전용 완료 알림 payload 저장소다.
  - 일반 신고 변경은 `notification_kind=report`
  - 중복 신고 변경은 `notification_kind=duplicate`
  - 중복 항목에는 `duplicate_change_type`, `status_label`, `representative_mode_label`, `member_count`, `representative_report_number`, `body`가 함께 들어간다.
- 웹 첨부 동영상은 `media_proxy_service.py` + `/media/proxy`를 우선 사용한다.
  - 원격 파일을 서버가 range 헤더와 함께 스트리밍 프록시하고,
  - 프록시 실패 시 브라우저가 원본 URL로 fallback 한다.
  - `<video preload="none">`으로 두어 모달 오픈만으로 모든 동영상이 동시에 선로딩되지 않게 유지한다.
- 크롤링 로그 회전은 `crawl_log_service.py`로 분리했다.
  - `crawl_control.py`와 `crawl_manager.py`가 같은 회전 함수를 공유하지만 서로를 import하지 않게 유지해야 한다.
- 서버 시작 시 `database.upgrade_schema()`가 실행되며, 기존 DB에 새 컬럼이 생긴 경우 단순 `ALTER TABLE`만 하지 않고 필요한 후속 마이그레이션까지 같이 처리한다.
  - 2026-05-06 이후에는 `mysafetydetail_*`.`synced_at` 공백을 `답변일` 우선, 없으면 `신고일` 기준으로 자동 백필하고 `mysafetymerge_*`를 다시 만든다.
  - `/backup/upload`로 서버 형식 DB를 덮어쓴 경우에도 같은 업그레이드/백필을 즉시 수행하므로, 앱 재시작 전까지 구스키마가 남아 있지 않게 한다.
  - 같은 흐름에서 payload exact 중복군도 재생성되어 merge 결과와 대표건 집계층을 함께 갱신한다.
- `legacy` 상세 파서는 처리결과가 여러 개일 때 마지막 `처리결과` 테이블을 최신 답변으로 사용한다.
- `legacy` 목록 파서는 페이지 전환 중 `stale element reference`가 나면 같은 페이지를 다시 읽도록 재시도한다.
- 비회원 모드는 direct login을 타지 않고, Chrome 창에서 사용자가 로그인한 뒤 `재개` 신호를 기다리는 수동 흐름이다.
- 만족도 보강은 API/legacy 각각 다른 조회 경로를 유지한다.
  - API 상세: 점수 API 우선, 필요 시 만족도 팝업 HTML로 사유 보강
  - legacy 상세: 만족도 팝업 HTML 직접 조회
  - 공통 원칙: 조회 실패는 미참여로 간주하지 않고, 확정 미참여일 때만 `참여 완료 -> 참여 가능` 재분류
- `sunwi_service`는 로그인 없이 안전신문고 통계 API를 별도로 호출하고, 서버 시작 후 즉시 1회 + 이후 3시간마다 대분류/소분류 기준 행정구역 Top5를 갱신한다.
- `web/templates/base.html`의 신고 상세 모달과 `web/templates/data_table.html`의 첨부 렌더는 이제 문자열 외 값도 받아들인다.
  - `지도`, `첨부사진`, `첨부파일`이 배열/객체/비정상 타입으로 들어와도 정규화 후 렌더하며, 상세 모달 렌더 중 일부 예외가 나도 페이지 전체가 죽지 않도록 fallback 모달을 띄운다.
- 전체 신고 조회/통계/대시보드/API의 기본 dedupe 기준은 `SETTINGS.use_representative_records`가 정한다.
  - `True`: 대표건 기준(`canonical`)
  - `False`: 원본 기준(`raw`)
  - 웹 페이지에는 별도 토글을 두지 않고 설정 페이지에서만 바꾼다.
  - 내부적으로 `dedupe=canonical|raw` 쿼리 파라미터가 명시되면 그 값은 여전히 우선 적용된다.
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
- 중복군 자동 감지는 `mysafety_raw_content.raw_content`가 있는 row만 대상으로 한다.
  - 같은 payload hash라도 `차량번호`, `category`, `entry_value`가 충돌하면 기본 상태는 `review_required` + `apply_globally=0`이다.
  - 기본 자동 감지 결과에서 충돌이 없으면 `confirmed_duplicate`, 충돌이 있으면 `review_required`로 시작한다.
  - `not_duplicate`는 “화면에서 숨김”이 아니라 “중복군 메타는 유지하되 canonical projection에서 원본 child를 모두 살려둠”을 뜻한다.
  - `representative_mode='auto'`인 그룹은 refresh 때마다 대표건이 다시 계산될 수 있고, `manual`은 저장된 대표건을 유지한다.
  - `raw_content`가 없는 과거 데이터는 자동 확정 대상이 아니라 후속 검토 후보 경로로 다룬다.

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
      → database.detail_to_sql()   # 기존 deatil_to_sql alias 유지
  → _process_and_save_results()
      → database.merge_final()
      → crawl_state_store.save_crawl_done()    # crawl_done.json 저장 → 모바일 폴링용
      → crawl_state_store.save_crawl_changes() # 변경 목록 저장
      → export_service.export_results()        # auto_export_* 설정 반영
```

- `start.py`는 FastAPI와 **별도 서브프로세스**로 실행 → ws_manager 싱글톤에 직접 접근 불가
- 크롤링 완료 후 로그 회전/완료 마커 기록/WS 브로드캐스트는 `crawl_manager.run_after_crawl()` 경로로 수렴
- `login.py`는 Selenium 회원 로그인 담당, `direct_login.py`는 API용 직접 로그인 담당으로 역할을 분리한다.
- 최근 3일 답변 목록은 `답변일` 필터 후 `synced_at DESC`, 동순위 `신고번호 DESC` 로 정렬한다.
  `synced_at` 가 없는 과거 데이터는 `답변일 DESC`, `신고번호 DESC` fallback 이 필요하다.

---

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
- `services.crawl_state_store.save_crawl_changes(engine, changed_item_ids)` — `[{"ID": ..., "change_type": "신규"|"변경", "신고번호": ..., "신고명": ...}]` 형태로 저장
- `services.crawl_state_store.peek_crawl_changes()` — 읽기만 (삭제 안 함) → WS 브로드캐스트용
- `services.crawl_state_store.get_and_clear_crawl_changes()` — 읽고 즉시 삭제 → 모바일 API 폴링용
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
| POST | `/rating/start` | 모바일 Client 별점 배치 시작 (API 키 인증) |
| POST | `/crawl/enqueue` | 신고번호 큐 등록 (알림 리스너 연동) |
| GET | `/crawl/status` | 크롤링 실행 여부 |
| GET | `/crawl/done` | 완료 마커 조회 (읽으면 삭제) |
| GET | `/crawl/results` | 변경 신고 목록 조회 (읽으면 삭제) |
| GET | `/crawl/config` | crawl_type, crawl_mode, max_empty_pages |
| POST | `/crawl/start` | 모바일에서 크롤링 시작 |
| POST | `/crawl/kill` | 크롤링 강제 중지 |
| POST | `/crawl/resume` | 비회원 로그인 완료 신호 |
| GET | `/app/config` | 앱 설정 (`exclude_withdraw`, `normalize_police`, `use_representative_records` 등) |
| POST | `/settings` | 필터 설정 저장 (`normalize_police`, `exclude_withdraw`, `use_representative_records`) |
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
- `만족도 조사 여부`는 `참여 완료`, `참여 가능` 단일선택 `<select>` 드롭다운으로 렌더링. 모바일 `SearchFilterSheet`에도 동일하게 추가.

### 웹 통계 상세검색 문법 (stats.html, services/report_stats_service.py)
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

## 크롬 확장 연동 (`web/routers/api_route.py`, `services/report_query_service.py`)

크롬 확장(`safetyreport-chromeextension`)에서 차량번호 검색 지원.

```
GET /api/v1/vehicle/{vehicle_number}
```
- 인증: `X-API-Key` 헤더
- 동작: 교통/주정차/기타 전체 merge 테이블에서 차량번호 부분 일치 검색, 신고번호 역순 정렬
- `search_by_vehicle(engine, vehicle_number)` — 현재 구현은 `report_query_service.py`, `data_service.py`는 호환 재노출만 담당

---

## 변경 이력
- 작업/버그/세션 기록은 `CHANGELOG.md`에 정리한다.
- 2026-04-30에 기존 작업 이력 섹션을 CLAUDE.md에서 분리했다.
