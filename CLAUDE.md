# 나만의 안전신문고 — 프로젝트 컨텍스트

안전신문고 민원 관리 시스템. FastAPI 웹서버 + Flutter 모바일 앱 구조.

---

## 디렉토리 구조

```
/
├── core/
│   ├── crawler/
│   │   ├── crawltitle_api.py   # API 기반 목록 크롤러 (기본)
│   │   ├── crawldetail_api.py  # API 기반 상세 크롤러 (기본)
│   │   ├── crawltitle.py       # 레거시 Selenium 목록 크롤러
│   │   └── crawldetail.py      # 레거시 Selenium 상세 크롤러
│   ├── database/database.py    # SQLAlchemy 테이블 정의 + DB 쿼리
│   └── utils/
│       ├── logger.py           # LoggerFactory (crawl/star 로거 분리)
│       ├── export.py           # Excel/Google Sheets 내보내기
│       ├── path_utils.py       # resource_path (frozen/dev 분기)
│       └── templating.py       # 중앙 집중식 Jinja2Templates
├── services/
│   ├── data_service.py         # DB 조회/집계 (대시보드, 필터, crawl_done 등)
│   ├── crawl_manager.py        # 크롤링 프로세스 싱글톤 (충돌 방지)
│   ├── parser.py               # HTML/JSON 파싱 (과태료, 처리상태 등)
│   ├── star_rating_service.py  # 별점 배치 처리
│   └── ws_manager.py           # WebSocket 클라이언트 연결 관리 싱글톤
├── web/
│   ├── routers/
│   │   ├── api_route.py        # /api/v1/** 모바일 API
│   │   ├── crawl.py            # /crawl/** 웹 크롤링 제어
│   │   ├── ws_route.py         # /ws/events WebSocket 엔드포인트
│   │   ├── data.py             # /data/** 데이터 조회
│   │   ├── settings_route.py   # /settings/** 설정 페이지
│   │   └── rating_route.py     # /rating/** 별점 관리
│   └── templates/              # Jinja2 HTML 템플릿
├── settings/settings.py        # AppSettings 싱글톤 (config.ini 기반)
├── main.py                     # FastAPI 서버 진입점 (lifespan, 라우터 등록)
├── start.py                    # 크롤링 실행 스크립트 (서브프로세스)
└── mobile/
    ├── lib/
    │   ├── main.dart                   # 앱 진입점, IndexedStack 6탭
    │   ├── models/report.dart          # Report, DashboardStats
    │   ├── providers/report_provider.dart
    │   ├── providers/notification_history_provider.dart
    │   ├── services/api_service.dart   # HTTP API 호출
    │   └── screens/
    │       ├── dashboard_screen.dart
    │       ├── statistics_screen.dart  # 카드 탭 → FilteredListScreen
    │       ├── filtered_list_screen.dart
    │       ├── crawl_screen.dart       # 크롤링 제어 + 실시간 로그
    │       ├── notifications_screen.dart
    │       └── settings_screen.dart
    └── android/app/src/main/kotlin/com/fentanest/mysafetyreport/
        ├── MainActivity.kt      # MethodChannel (권한, WsService, showNotification)
        ├── WsService.kt         # 백그라운드 WebSocket Foreground Service
        └── NotificationService.kt  # 카카오/안전신문고 알림 리스너
```

---

## DB 테이블

| 테이블 | 설명 |
|--------|------|
| `mysafety` | 신고 목록 (ID, 신고번호, 신고명, 신고일, 상태 등) |
| `mysafetydetail_traffic` | 교통위반 상세 |
| `mysafetydetail_other` | 기타 신고 상세 |
| `mysafetymerge_traffic` | 최종 병합 (traffic) |
| `mysafetymerge_other` | 최종 병합 (other) |
| `api_keys` | 모바일 API 인증 키 |
| `admin_users` | 웹 관리자 계정 |
| `watchlist` | 감시 목록 (신고번호) |

---

## 크롤링 파이프라인 (start.py)

```
main()
  → driv.create_driver() → login()
  → _run_crawling_process()
      → crawltitle_api.crawl_titles()  → database.title_to_sql()
      → (큐 모드) extract_ids_from_queue()
          → missing_rnums 있으면 최대 100페이지 단건 크롤링으로 탐색
          → 발견 즉시 detaillist 추가, 모두 찾으면 조기 종료
      → crawldetail_api.crawl_details() → database.deatil_to_sql()
  → _process_and_save_results()
      → database.merge_final()
      → save_crawl_done()    # crawl_done.json 저장 → 모바일 폴링용
      → save_crawl_changes() # 변경 목록 저장
      → (auto_export_excel) save_to_excel()
      → (auto_export_sheet) save_to_google_sheet()
```

- `start.py`는 FastAPI와 **별도 서브프로세스**로 실행 → ws_manager 싱글톤에 직접 접근 불가
- 크롤링 완료 알림은 FastAPI의 `wait_and_rotate` 배경 스레드가 완료 마커 확인 후 브로드캐스트

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

### 경로 1: WsService.kt (백그라운드)
앱 종료 상태에서도 동작:
1. WsService가 `/ws/events` 연결 유지
2. `crawl_finished` 수신 → `showPushNotif()` → Android 시스템 알림 (ws_push 채널)
3. 알림 기록 → `FlutterSharedPreferences` → 앱 재시작 시 알림 탭 표시

### 경로 2: 앱 포그라운드 폴링 (보완)
WsService가 연결 끊긴 동안 완료된 경우:
1. 앱 포그라운드 복귀 → `notifications_screen._fetchServerResults()`
2. `/api/v1/crawl/done` 폴링 → 완료 마커 있으면
3. `/api/v1/crawl/results` 조회 → 알림 탭 추가
4. `showNotification` MethodChannel → Android 시스템 알림 표시 (중복 방지 X, WsService 미수신분 보완)

### crawl_done.json
- `save_crawl_done(changed_count)` — 크롤링 완료 시 저장
- `get_and_clear_crawl_done()` — 읽고 즉시 삭제 (한 번만 읽힘)
- 위치: `data/crawl_done.json`

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

---

## 모바일 하단 탭 구조

| 인덱스 | 탭 | 화면 |
|--------|----|----|
| 0 | 대시보드 | `DashboardScreen` |
| 1 | 신고리스트 | `ReportListScreen` |
| 2 | 검색 | `SearchScreen` |
| 3 | 통계 | `StatisticsScreen` |
| 4 | 알림 | `NotificationsScreen` |
| 5 | 크롤링 | `CrawlScreen` |

---

## Android 서비스 구성

### WsService (Foreground Service)
- OkHttp WebSocket, `pingInterval = 25s` (프로토콜 레벨)
- 재연결 백오프: 3s → 6s → 12s → 24s → 60s
- Foreground 알림: `ws_service` 채널 (`IMPORTANCE_LOW`, 소리 없음)
  - 재연결 중: "서버 연결 대기 중..." (이전: "연결 끊김. Ns 후 재연결...")
  - 연결됨: "서버 연결됨 ✓"
- `crawl_started`, `crawl_finished` → `ws_push` 채널 (`IMPORTANCE_DEFAULT`) 알림
- `START_STICKY` — OS 강제 종료 후 자동 재시작

### MainActivity MethodChannel (`com.fentanest.mysafetyreport/permissions`)
| 메서드 | 기능 |
|--------|------|
| `isNotificationListenerEnabled` | 알림 리스너 활성화 여부 |
| `openNotificationListenerSettings` | 시스템 알림 접근 설정 화면 |
| `startWsService` | WsService 시작 |
| `stopWsService` | WsService 중지 |
| `isWsServiceRunning` | WsService 실행 여부 |
| `showNotification` | 로컬 Android 알림 표시 (폴링 완료 시 보완용) |

### NotificationService (알림 리스너)
카카오톡/안전신문고 알림 인터셉트 → 신고번호 정규식 추출 → `/api/v1/crawl/enqueue`

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

### 세션 인증 미들웨어
- `_PUBLIC_PREFIXES`: `/login`, `/setup`, `/static/`, `/api/v1/`, `/ws/` 등 세션 우회
- `/api/v1/` → `X-API-Key` 별도 인증
- AJAX/fetch 요청은 302 대신 401 JSON 반환

### 리버스 프록시 지원 (trusted_proxies)
`config.ini` `[SETTINGS] trusted_proxies` 에 쉼표 구분 IP 목록 저장.
`main.py` 모듈 레벨에서 `uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware` 조건부 적용.
설정 변경 후 **서버 재시작 필요** (미들웨어는 앱 시작 시 1회 등록).

### 모바일 Android 알림 채널 주의
채널 ID별 importance는 **최초 생성 시에만** 설정 가능. 기존 채널 importance 변경 불가.
importance를 올려야 할 때는 채널 ID를 바꿔야 함 (예: `ws_push` → `ws_push_v2`).
현재 채널: `ws_push_v2` (WsService), `app_push_v2` (MainActivity) — 모두 `IMPORTANCE_HIGH`.

### Flutter SharedPreferences 크로스-프로세스 캐시 주의
Android native(Kotlin)에서 `FlutterSharedPreferences` 직접 쓰기 시 Flutter 캐시에 즉시 반영 안 됨.
Flutter 측에서 `prefs.reload()` 호출 필요 (→ `NotificationHistoryProvider.load()` 적용됨).

---

## 빌드

### Docker APK 빌드 (NDK 포함)
```bash
docker run --rm \
  -v ${PWD}:/build \
  -v ~/.pub-cache:/root/.pub-cache \
  -v ~/.android-sdk:/root/Android/Sdk \
  --workdir /build \
  ghcr.io/cirruslabs/flutter:stable \
  bash -c "yes | \$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager --sdk_root=/root/Android/Sdk 'ndk;28.2.13676358' 2>&1 | tail -3 && flutter build apk --release"
```
컨테이너 내장 sdkmanager로 마운트된 경로에 NDK 설치.

### PyInstaller
`scripts/build/build_exe.py` — Windows/Linux 단일 바이너리. 미사용 stdlib 제외, UPX 압축.

---

## 주요 버그 이력

| 버그 | 원인 | 수정 |
|------|------|------|
| WS 이벤트 미전달 | 배경 스레드에서 `new_event_loop()` 사용 → 메인 루프 WebSocket 접근 불가 | `broadcast_from_thread()` + `run_coroutine_threadsafe()` |
| 취하 민원 과태료 오파싱 | 취하 확정 전 처리상태 기준으로 과태료 설정 | 취하 확정 시 penalty 초기화 |
| statistics_screen InkWell 괄호 누락 | `Card(child: InkWell(child: Padding(...)))` 구조에서 `)` 누락 | 괄호 추가 |
| `/api/v1/` auth 차단 | `auth_middleware`가 세션 없는 API 요청을 302 리다이렉트 | `_PUBLIC_PREFIXES`에 `/api/v1/` 추가 |
| 두 개의 별점 로그 파일 | `star_*.log` (Python logger) + `current_rating.log` (수동 write) 이중 기록 | `set_star_log_file()` 로 통합 |
| 모바일 `/api/v1/reports/traffic` 500 | `df.to_dict()` 결과에 pandas `NaN`(`float('nan')`) 포함 → JSON 직렬화 실패. 웹(Jinja2)은 무시하지만 JSON 응답은 실패 | `_get_records_from_table`, `get_duplicate_records`에서 `df.fillna('')` 후 `to_dict()` |
| 다중 선택 크롤링 1건만 전송 | `SelectionActionBar._crawl()`이 건별 `enqueue` 호출 → 첫 번째 이후는 "busy" 반환(HTTP 200)되어 Flutter가 성공으로 간주 | `startCrawlQueue(numbers)` 추가 → `crawl/start` + `queue_list`로 일괄 전송 |
| 알림 팝업(heads-up) 미표시 | `IMPORTANCE_DEFAULT`는 heads-up 팝업 불가. 채널 생성 후 importance 변경 불가 | 채널 ID `ws_push` → `ws_push_v2`, `app_push` → `app_push_v2`, `IMPORTANCE_HIGH` + `enableVibration` |
| 알림 탭 WsService 기록 미반영 | Flutter `SharedPreferences` 싱글톤 캐시가 WsService의 Android 직접 쓰기를 반영 못함 | `NotificationHistoryProvider.load()`에 `prefs.reload()` 추가 |
