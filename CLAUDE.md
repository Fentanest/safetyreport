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
│   │   ├── devices_route.py    # /devices/** 기기 연동 (API 키 관리 + WS 연결 현황)
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
| 2 | 통계 | `StatisticsScreen` |
| 3 | 알림 | `NotificationsScreen` |
| 4 | 파일 | `FileBrowserScreen` |
| 5 | 크롤링 | `CrawlScreen` |

---

## Android 서비스 구성

### WsService (Foreground Service)
- OkHttp WebSocket, `pingInterval = 25s` (프로토콜 레벨)
- 재연결 백오프: 3s → 6s → 12s → 24s → 60s
- Foreground 알림: `ws_service` 채널 (`IMPORTANCE_LOW`, 소리 없음)
  - 재연결 중: "서버 연결 대기 중..." (이전: "연결 끊김. Ns 후 재연결...")
  - 연결됨: "서버 연결됨 ✓"
  - 탭 시 앱 열림 (`getLaunchIntentForPackage` + FLAG_ACTIVITY_SINGLE_TOP)
- `crawl_started`, `crawl_finished` → `ws_push_v2` 채널 (`IMPORTANCE_HIGH`) 알림
- `crawl_changes` → `ws_push_v2` 채널 개별 알림 + `FlutterSharedPreferences`에 `flutter.pending_crawl_changes` 저장
  - 앱 포어그라운드 복귀 시 `main.dart`가 읽어 알림 탭으로 이동 + 카드 뷰 바텀시트 표시
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

### 웹 사이드바 설정 섹션
- **앱 설정 (/settings)**: 1. 시스템 및 서버 설정 / 2. 크롤링 설정 / 3. 외부 연동 키 설정
  - 1. 시스템: 로그인 계정 → 별점 휴대폰 번호 → 데이터 필터 → 세션/프록시
  - 2. 크롤링: 크롬 구동 방식 → 크롤링 후 자동 저장 → 고급 설정 → 자동 스케줄러
  - 3. 외부 연동: 텔레그램 → 구글 시트 URL → 구글 JSON 인증 파일 업로드 (fetch 방식)
- **관리자 계정 변경 (/settings/admin)**: 아이디/비밀번호 변경 전용 페이지
- **기기 연동 (/devices)**: API 키 생성/삭제 + 현재 WebSocket 연결 기기 목록

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
Flutter 측에서 `prefs.reload()` 호출 필요 (→ `NotificationHistoryProvider.load()`, `main.dart _checkPendingChanges()` 적용됨).

WsService가 Kotlin 쪽에서 쓰는 SharedPreferences 키:
- `flutter.notifications_history` — 크롤링 시작/완료 알림 기록 (JSON array)
- `flutter.pending_crawl_changes` — crawl_changes 이벤트 수신 시 변경 목록 임시 저장 (앱 복귀 후 카드 뷰 표시 후 삭제)

### 파서 첨부파일 URL (services/parser.py)
`FILE_URL`이 상대경로(`/fileDown/singo/...`)로 오는 경우 `https://www.safetyreport.go.kr` 프리픽스 자동 추가.
`STTEMNT_IMAGE_URL`도 동일하게 처리. (텔레그램 첨부 URL 깨짐 버그 수정)

### 첨부사진/파일 URL 구분자
DB에는 `\n` 으로 구분 저장됨. 웹 `data_table.html`의 `renderAttach()`는 `d.split('\n')`으로 파싱.
모바일 `report_detail_sheet.dart`의 `_splitUrls()`도 `split('\n')` 사용. (`,`로 split하면 URL 전체가 1개로 처리됨)

### 모바일 알림 탭 구조 (notifications_screen.dart)
`DefaultTabController(length: 2)` 로 두 탭 분리:
- **크롤링 현황**: `extraData == null`인 항목 (크롤링 시작/완료 알림)
- **신고 결과**: `extraData != null`인 항목 (개별 신고 변경 결과) → 탭 시 `ReportDetailSheet` 표시

### 모바일 통계 탭 구조 (statistics_screen.dart)
`TabController(length: 12)` — 교통위반 6탭 + 기타위반 6탭.
각 그룹: 기관별 / 담당자별 / 경찰 기관 / 경찰 담당자 / 비경찰 기관 / 비경찰 담당자.
행 클릭 필터: `r.agency == agency` (정확히 일치, `contains` 아님).

### 웹 통계 agencyExact 파라미터 (stats.py, data.py)
`agencyExact=True` 시 `df['처리기관'] == agency` 정확히 일치 필터 적용.
통계 행 클릭 링크에는 `&agencyExact=true` 자동 포함. 직접 검색 시는 기본값 `false` (contains).

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
| WsService 중지 버튼 앱 크래시 | `stopSelf()` 전 `stopForeground()` 미호출 + reconnectThread sleep InterruptedException 미처리 | `stopForeground(true)` 추가, sleep에 try-catch InterruptedException 추가 |
| 기기 연결 현황 ID가 UUID로 표시 | `_connection_meta`에 기기명 미저장, ws_route에서 조회 안 함 | `get_api_key_name()` DB 조회 후 `ws_manager.connect(device_name=...)` 전달 |
| WS 클라이언트 IP가 프록시 IP로 표시 | 리버스 프록시 뒤에서 `websocket.client.host` = 프록시 IP | ws_route에서 `X-Forwarded-For` 헤더 우선 처리 |
| 텔레그램 첨부파일 상대경로 URL | API 응답 `FILE_URL`이 `/fileDown/singo/...`로 오는 경우 있음 | `parser.py`에서 `file_url.startswith('/')` 체크 후 도메인 추가 |
| 텔레그램 지도사진 중복 전송 | `img_links`에 이미 있는 `MAPIMG_URL`을 `insert(0, ...)`으로 또 추가 | `map_image in img_links`이면 먼저 `remove()` 후 `insert(0, ...)` |
| 알림 탭 신고건 탭해도 무반응 | `NotificationItem`에 `extraData` 없어 상세 표시 불가 | `extraData: Map<String,dynamic>?` 추가, `ReportDetailSheet` 연결 |
| NotificationService 아이콘 | `ic_dialog_info`(안드로이드 기본) 사용 중 | `R.drawable.ic_stat_logo` 적용 |
| 신고번호 정규식 패턴 불일치 | `202[0-9]{7,11}` → 새 형식 `SPP-2603-1434237` 미인식 | `SPP-\\d{4}-\\d{6,8}` 로 변경 |
| 연속 enqueue 요청 드롭 | 크롤링 중 추가 enqueue → "busy" 반환 후 폐기 | `_pending_queue` 추가 → 크롤 완료 후 자동 재실행 |
| crawl_changes WS 브로드캐스트 누락 | `save_crawl_changes()` 저장 후 WS 이벤트 미전송 | `peek_crawl_changes()` → `broadcast_from_thread("crawl_changes", ...)` 추가 |
| 통계 기관 필터 포함 매칭 | `str.contains`로 '서울특별시' 클릭 시 '서울특별시경찰청' 등 모두 포함됨 | `agencyExact` 파라미터 추가 → 통계 행 클릭 시 정확히 일치(`==`) 필터 적용 |
| 통계 검색 500 에러 | `str.contains(regex=True)`로 특수문자 입력 시 regex 오류 | 모든 `str.contains` 호출에 `regex=False` 적용 |
| `calc_stats` 조기 반환 KeyError | 조기 반환 시 2키 dict 반환 → 라우터가 6키 기대 | 조기 반환도 6키 빈 dict 반환으로 통일 |
| 모바일 첨부 URL 분리 오류 | `_splitUrls()`가 `,`로 split → `\n` 구분 URL 전체가 1개로 처리됨 | `split('\n')`으로 변경 |
| 모바일 첨부 URL %0A 미분리 | DB에 `%0A`(URL 인코딩 개행)로 저장된 경우 분리 안 됨 | `split(RegExp(r'\n\|%0A\|%0a'))`로 확장 |
| 감시목록 카드 상세 미표시 | `/summary` watchlist에 `신고내용`·`처리내용`·`첨부사진`·`첨부파일` 등 누락 | `watchlist_items` 딕셔너리를 `recent_answers`와 동일한 필드셋으로 통일 |
| ARM64 chromedriver Exec format error | webdriver_manager가 x86_64 chromedriver 다운로드 → ARM64에서 실행 불가 | `platform.machine()` 감지 후 `/usr/bin/chromedriver` 사용 |
