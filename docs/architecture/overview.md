# 시스템 구조 개요

다루는 것: 디렉토리 구조, 전체 동작 요약, 실행 모드·경로·마이그레이션, 로그, 빌드(PyInstaller/ARM64).

2026-09-24 에 기존 루트 `CLAUDE.md`(725줄)에서 옮겼다. 아래 '이관 원문' 은 문구를 바꾸지 않고 옮긴 것이며, 원본 전체는 [legacy-claude-reference.md](legacy-claude-reference.md)에 있다.

## 코드 대조 정정 (기준 17df6cb)

| 원문 절 | 현재 코드 | 조치 |
|---|---|---|
| 디렉토리 구조 | `core/utils/runtime_mode.py`, `scripts/dev/`, `tools/`, `tests/` 가 트리에 없다. `services/db_editor_service.py`, `web/routers/filters.py` 도 빠져 있다(본문에는 db_editor_service 언급 있음). | 트리 보강 필요 — 아래 '2026-09-24 추가 구조' 절 참고 |
| 로그 시스템 | WebSocket 로그 경로는 `/crawl/ws/logs` 외에 별점 로그 `/rating/ws/rating_logs` 도 있다(`web/routers/rating_route.py`). | 추가 |
| 현재 주요 구조 요약 / 전체 | 테스트용 실행 경로(SAFETYREPORT_DATA_DIR / SAFETYREPORT_FIXTURE_MODE)는 2026-09-24 에 추가됐다. | docs/development/runtime-and-packaging.md |
| 현재 서버 시작·사용 흐름의 schema4/API 버전 설명 | `2eb833d`의 서버 schema는 5, 모바일 교환 schema는 16이다. 유효 API 키 뒤에도 protocol3 검사가 있으며 인증된 최소 server/version probe만 예외다. legacy DB는 백업 후 초기화하며 아래 이관 원문의 자동 업그레이드는 과거 설명이다. | 현행 `data-contracts.md` 및 `contracts/selfhost-compat/` 우선 |

## 2026-09-24 추가 구조

- `core/utils/runtime_mode.py` — 개발/테스트 seam. `SAFETYREPORT_DATA_DIR`(데이터 루트 교체), `SAFETYREPORT_FIXTURE_MODE=1`(외부 부작용 차단). 둘 다 없으면 운영 동작 그대로.
- `scripts/dev/fixture_server.py` — 합성 데이터 fixture 서버, `scripts/dev/web_contract_inventory.py` — 라우트/DOM 계약 정적 인벤토리.
- `tools/web-tests/` — Playwright Test(개발 전용 Node), `tools/docker/compose.devtest.yml` — 격리 개발 Docker.
- `services/db_editor_service.py`(데이터 수정 스키마/조회/저장), `web/routers/filters.py`(dedupe/카테고리 쿼리 정규화) — 기존 파일이지만 트리에 없었다.
- 실행·테스트 방법은 [../development/runtime-and-packaging.md](../development/runtime-and-packaging.md).

## 현재 서버 시작·사용 흐름 (2026-09-27 dev)

이 절이 아래 이관 원문의 구버전 시작·DB 설명보다 우선한다.

1. `main.py` 시작 단계에서 `core/database/database.py`가 이전 형식 DB를 발견하면 `data/backups/legacy_v*.db`로 온전하게 백업한 뒤 신고 자료를 비운다. 관리자 계정·API 키·감시 목록·주소 좌표 캐시는 남긴다. 새 설치와 현재 스키마 DB는 이 초기화를 거치지 않는다. 실패하면 서버 시작을 멈춘다([data-contracts.md](data-contracts.md)).
2. 관리자 로그인 뒤 `services/community_gate.py`와 `main.py` 미들웨어가 카카오 로그인·중앙의 현재 신고 내용 공유 동의를 확인한다. 통과 전에는 신고 화면과 크롤 시작을 막고 온보딩으로 보낸다. 동의 철회나 정책 변경 시 재확인한다([community-gate.md](community-gate.md)).
3. 이전 자료를 비웠거나 중앙 공유 자료를 다시 채워야 하면 초기화 수집 안내를 거친다. 실제 수집은 `services/crawl_manager.py`가 `start.py`를 별도 프로세스로 실행한다([community-rebuild.md](community-rebuild.md), [crawling-and-processing.md](crawling-and-processing.md)).
4. Android Client는 `web/routers/api_route.py`의 인증된 `/api/v1/server/version`에서 PC v3 이상을 확인한 뒤 연결한다. 앱 v2 이상 필요 안내는 `web/templates/devices.html`의 사용자 문구이며, 서버 API 전체가 앱 버전을 강제로 검증한다는 뜻은 아니다. 양쪽 변경 시 앱의 저장 설정 재진입·WebSocket 연결도 함께 확인한다.

`VERSION`은 PC 프로그램 버전이고 SQLite의 `SCHEMA_VERSION`(현재 5, contracts/storage-contract.json 의 schema_version.server)과 별개다. 모바일 앱 버전·빌드 번호도 별개이며, 현재 dev 앱은 `2.0.0+31`이다.

## 이관 원문

<!-- legacy CLAUDE.md 15-110 -->
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
│   │   ├── title_pipeline.py    # 목록 row 정규화
│   │   ├── detail_pipeline.py   # 상세 row 정규화 + 만족도 보강
│   │   ├── direct_login.py       # curl_cffi + RSA + OAuth 기반 직접 로그인
│   │   ├── driv.py               # Selenium Chrome driver 생성 (desktop/remote/hub)
│   │   ├── login.py              # Selenium 로그인 UI 처리
│   │   ├── crawltitle_api.py     # API 목록 크롤러
│   │   └── crawldetail_api.py    # API 상세 크롤러 (레거시 Selenium 크롤러는 2026-09-25 삭제)
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
│   ├── db_backup.py             # DB checkpoint(WAL/SHM 정리) + 서버/모바일 DB 자동 감지/변환 + sync_meta/중복군 메타 보존
│   ├── export_service.py        # 엑셀/구글시트 export 흐름 조립
│   ├── file_service.py          # 웹/API 파일 브라우저 공용 로직
│   ├── crawl_manager.py         # 크롤링 프로세스 싱글톤 (충돌 방지)
│   ├── geocode_service.py       # 공식 응답 좌표를 기존 DB 열에 담는 순수 헬퍼
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


<!-- legacy CLAUDE.md 111-198 -->
## 현재 주요 구조 요약

- FastAPI 웹앱은 `main.py`에서 기동하고, 실제 크롤링은 `start.py`를 **별도 서브프로세스**로 실행한다.
- 설정 저장/조회는 `settings/settings.py`의 `AppSettings` 싱글톤을 중심으로 돌고, 실데이터는 `data/config.ini`, `data/data.db`, `data/auth/*`에 쌓인다.
- 모바일 API는 `web/routers/api_route.py`, 웹 UI는 `web/routers/*.py` + `web/templates/*.html` 조합으로 구성되며, 실제 공통 작업은 `services/` 계층으로 최대한 이동했다.
- 크롤링은 API 방식 하나다(2026-09-25 레거시 Selenium HTML 크롤링·최소 크롤링·`crawl_type` 설정 제거).
  - `api`: `direct_login` + `curl_cffi` API 호출
  - `api fallback`: direct login 실패 시 Selenium 로그인 후 브라우저 컨텍스트 `$.get` 으로 같은 API 호출(비상 경로 — Chrome 설정·Selenium 의존성은 이것 때문에 남아 있다)
- 상세/목록 파싱 결과는 `title_pipeline.py`, `detail_pipeline.py`로 공통 row 스키마에 맞춘다.
- `start.py`의 큐 지정 크롤링은 전체 목록 갱신은 건너뛰지만,
  큐 신고번호가 DB에 아직 없으면 목록 페이지를 다시 순회해 ID를 찾아낸 뒤 상세 크롤링으로 이어가야 한다.
  특히 API direct-login 모드는 Selenium driver가 없어도 이 재탐색이 동작해야 한다.
- 조회 계층은 `report_query_service.py`, 통계 계층은 `report_stats_service.py`, 크롤링 상태 파일 계층은 `crawl_state_store.py`로 분리되었고, `data_service.py`는 기존 import 경로 호환용 facade만 남겼다.
- 라우터는 가능한 얇게 유지하고, 크롤링 제어/파일 브라우저/별점 시작은 각각 `crawl_control.py`, `file_service.py`, `rating_service.py`를 통해 공통 처리한다.
- 웹 `data_table.html` 기반 페이지(전체/교통/주정차/기타/중복차량)는
  선택된 신고번호를 `POST /crawl/enqueue-selected`로 보내 다건 큐 크롤링을 걸 수 있다.
  이 경로도 모바일 enqueue와 같은 `crawl_control.py`를 타므로,
  이미 실행 중이면 pending queue에 붙고 아니면 큐 지정 크롤링을 즉시 시작한다.
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
- 웹 첨부 동영상은 `media_proxy_service.py` + `/media/proxy`를 통해 캐시된 로컬 파일에서 Range 부분 응답으로 재생한다.
  - 원본(`safetyreport.go.kr` 첨부 다운로드)은 Range를 지원하지 않고(`200 OK` 응답 + `Content-Disposition: attachment` + `Content-Type: application/download`) 직결 시 seek 바가 동작하지 않는다.
  - 프록시는 upstream 파일을 `data/media_cache/<sha256(url)>` 로 받아두되, **캐시 완성을 기다리지 않고 받는 즉시 흘려보낸다(tail-follow)**.
    - `open_stream(url)`이 upstream `Content-Length`만 확보되면 진행 중인 `.tmp`를 소스로 반환하고, `iter_stream()`이 다운로더가 써 내려가는 만큼 따라 읽는다.
    - 리더가 "지금 어디까지 읽어도 되는지"는 파일 크기가 아니라 `_progress[url]["downloaded"]` 카운터로 판단한다. 다운로더가 `flush()` 뒤에만 이 값을 올리므로 미완성 버퍼를 읽지 않는다.
    - 리더는 `.tmp`를 먼저 열고, 그 사이 완료돼 rename 됐으면 최종 파일로 폴백한다. POSIX에서 이미 열린 fd는 rename 후에도 같은 inode를 가리키므로 rename을 가로질러 읽어도 안전하다.
    - `Content-Encoding`이 identity가 아니거나 `Content-Length`가 없으면(chunked 등) Range 계산이 깨지므로 그때만 레거시 경로(완료까지 대기)로 폴백한다.
    - 아직 안 받은 오프셋으로의 Range 요청은 그 지점까지 대기한다. 정체가 `_STALL_TIMEOUT_SECONDS`(120초)를 넘으면 에러로 끊는다.
  - 캐시 준비는 백그라운드 워커 4개로 병렬 수행해 여러 동영상이 동시에 warm-up 될 수 있게 한다. `POST /media/prepare`는 이 워커를 미리 깨우는 용도이고, `GET /media/status`는 진행률(`bytes`/`total`) 조회용이다. 둘 다 재생 시작의 전제 조건은 아니다.
  - 캐시가 이미 완성된 URL은 종전대로 파일에서 Range 헤더에 맞춰 `206 Partial Content`/`200`을 만들어 응답한다.
  - 동시 요청 race는 URL별 `threading.Lock`으로 직렬화하고, atomic write(`.tmp` → rename)로 부분 쓰기 노출을 막는다.
  - `iter_stream()`은 동기 제너레이터라 Starlette가 threadpool에서 돌린다. 대기 중인 스트림 하나가 threadpool 토큰 하나를 잡으므로, 동시 재생 수가 크게 늘면 anyio 기본 한도(40)를 염두에 둘 것.
  - `Content-Type`은 URL 경로 기반 `mimetypes.guess_type`으로 결정해 upstream의 `application/download`를 덮어쓴다.
  - `/media/`는 로그인 리다이렉트 예외 경로이며, 프록시 대상 호스트는 `*.safetyreport.go.kr`로 제한한다.
  - 캐시 만료: 서버 시작 시 7일 이상 된 캐시 파일을 자동 정리(`cleanup_cache()`).
  - 모달 오픈 시 `prepare`만 fire-and-forget 으로 던지고 `<video preload="metadata">` src를 **즉시** 붙인다. 캐시 완료 폴링은 하지 않는다 (tail-follow 라 기다릴 이유가 없다). 실패는 `<video>`의 `error` 이벤트로 "동영상 준비 실패" 표시.
  - 모달 `hidden.bs.modal` 이벤트에서 내부 `<video>`를 `pause()` → `removeAttribute('src')` → `load()` 순으로 정리해 진행 중 다운로드와 백그라운드 오디오를 abort 한다. (백드롭/X/ESC 닫기 모두 동일 경로)
  - 적용 대상: `base.html`의 `#reportDetailModal`, `data_table.html`의 `#attachModal` 두 곳 모두 동일 패턴.
- 크롤링 로그 회전은 `crawl_log_service.py`로 분리했다.
  - `crawl_control.py`와 `crawl_manager.py`가 같은 회전 함수를 공유하지만 서로를 import하지 않게 유지해야 한다.
- 서버 시작 시 `database.upgrade_schema()`가 실행되며, 기존 DB에 새 컬럼이 생긴 경우 단순 `ALTER TABLE`만 하지 않고 필요한 후속 마이그레이션까지 같이 처리한다.
  - 2026-05-06 이후에는 `mysafetydetail_*`.`synced_at` 공백을 `답변일` 우선, 없으면 `신고일` 기준으로 자동 백필하고 `mysafetymerge_*`를 다시 만든다.
  - `/backup/upload`로 서버 형식 DB를 덮어쓴 경우에도 같은 업그레이드/백필을 즉시 수행하므로, 앱 재시작 전까지 구스키마가 남아 있지 않게 한다.
  - 업로드 절차(EO R-12, 2026-10-05): 웹 `/backup/upload` 와 API `/api/v1/settings/db/upload` 는 같은 임시 저장(`web/db_upload.staged_db_upload` — .db 이름 확인,
    1MiB 단위로 이벤트 루프 밖에서 쓰기, 끝나면 임시 파일과 `-wal`·`-shm`·`-journal` 부속 파일까지 삭제)과 HTTP 와 무관한 복원
    `services.db_backup.restore_uploaded_db`(형식·무결성·종류 판별 → 서버/모바일 복원, 결과 kind·imported·backup)을 쓴다. 거절은 둘 다 409 같은 본문,
    성공 응답은 웹만 `message` 를 더한다. 이전에는 형식 검사가 만든 `-wal`·`-shm` 이 임시 폴더에 남았다.
  - 같은 흐름에서 payload exact 중복군도 재생성되어 merge 결과와 대표건 집계층을 함께 갱신한다.
- 신고 위치는 안전신문고 상세의 `C_A_W`(위도)·`C_A_E`(경도)에서 읽고, 완료된 보완 주소·좌표가 있으면 그 값으로 갱신한다.
  - `services/geocode_service.py`는 기존 `위도`·`경도`·`지오코딩상태` 열에 맞춘 순수 변환만 한다. 카카오 REST 주소 변환·백필 작업은 없다.
  - 재크롤링 때 좌표가 없어지면 이전 좌표를 지운다. 사용자 편집 주소는 공식 좌표를 바꾸지 않는다.
- 비회원(수동) 로그인 모드는 2026-09-25 제거했다. 크롤링은 늘 회원 로그인이고, `/api/v1/crawl/resume` 은 구앱 호환으로 410 만 돌려준다.
- 만족도 보강: 점수 API 우선, 필요 시 만족도 팝업 HTML로 사유 보강(브라우저 비상 경로면 브라우저 세션으로 같은 조회)
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


<!-- legacy CLAUDE.md 428-438 -->
## 로그 시스템

- `data/logs/current_crawl.log` — 현재 크롤링 로그 (라이브)
- `data/logs/crawl_{ts}.log` — 완료된 크롤링 백업
- `data/logs/current_rating.log` — 현재 별점 작업 로그
- `data/logs/star_{ts}.log` — 완료된 별점 로그 백업
- `LoggerFactory.star_log` — `set_star_log_file(path)` 로 파일 핸들러 동적 교체
- 웹/모바일 모두 WebSocket(`/crawl/ws/logs`)으로 실시간 스트리밍 가능

### 실패 대체 정책 (EO R-17, 2026-10-05)
조회·판정이 실패했을 때 빈 값이나 기본값으로 바꾸는 곳이 많다. 기본값 자체(best-effort·fail-closed)는 기능마다 정한 그대로 두고, 진단 방식만 통일한다.
- **기록하는 대체**: 화면 값·수집 판단·권한 판정에 쓰이는 조회가 실패하면 `core/utils/fallback.note_fallback(동작명, exc)` 로 남긴다.
  예: 대시보드 감시목록, 평균 처리일, community 상태 라벨·재시도 ID, 손상된 상태 JSON, 설정 복호화, 데이터 주인·업로드 허용·삭제 표시 확인, 크롤 후 rebuild·변경 목록 게시, 스케줄러 사전 확인.
- **기록 형식**: `[fallback] <동작명>: <예외 종류>` 만 남긴다. 예외 메시지·인자·SQL 값은 경로·쿠키·계정이 섞일 수 있어 남기지 않는다.
  같은 (동작명, 예외 종류)는 60초에 한 번만 남겨, polling 화면이 로그를 채우지 않게 한다.
- **기록하지 않는 대체**: 정리·종료·알림(cleanup), 형식 판정(parse), 다음 실행에서 다시 시도(retry), 부가 정보 보강(optional),
  오류를 값으로 돌려줌(explicit)만 허용한다. 목록과 분류는 `tests/test_fallback_policy.py` 의 `SILENT_ALLOWED` 가 정본이고,
  목록에 없는 새 `except Exception: pass/return/continue` 는 시험이 막는다.
- **요청 경계**: 인증 미들웨어가 잡은 예외(공개 경로 포함)는 `log_request_exception(path, exc)` 로 경로·예외 종류·traceback 프레임만 남기고 500 을 돌려준다.
  공개 경로 예외는 이전에는 기록 없이 500 이었다.

---


<!-- legacy CLAUDE.md 571-583 -->
## 주요 아키텍처 결정 사항

### 멀티-모드 디스패처
`main.py`가 `--mode` 인자에 따라 Web Server / Bot / Crawler / Notifier로 분기.
PyInstaller 단일 바이너리 배포 시 서브프로세스가 `sys.executable` 재호출.

### 앱 조립 (EO R-04, 2026-10-05)
- `main.py` 맨 앞에서 PyInstaller 하위 모드(`--mode crawl/bot/notify/save_excel`)를 나눈다 — 크롤·알림 하위 프로세스가 라우터·서비스 전체를 불러오지 않는다.
- `create_app()` 이 프로세스 준비(`_init_runtime`: 코어 로거·data 하위 폴더) → 앱·정적 파일 → 신뢰 프록시 → 시그널 처리기 → 라우터 → HTTP 미들웨어 → 세션 미들웨어(세션 키·로그 WS 키) 순으로 조립하고 `app.state.engine` 을 둔다.
  미들웨어 순서(바깥부터): 세션 → 관리자 인증 → 시간 측정 → 커뮤니티 게이트 → 버전·CSRF.
- `main.app` 은 모듈 `__getattr__`(PEP 562)로 처음 읽을 때 한 번 만든다(`get_app()`). `uvicorn main:app`·Docker·fixture·시험은 그대로 쓰고, `import main` 만으로는 앱·로그·폴더·시그널이 생기지 않는다. frozen 실행은 `get_app()` 을 넘긴다.
- 경계: 설정(`settings.settings`)과 DB 경로는 프로세스 전역이라 한 프로세스에 데이터 루트가 다른 앱 둘은 만들 수 없다. 라우터 9개는 여전히 모듈 전역으로 같은 프로세스 엔진(`get_engine()` 단일 객체)을 들고 있다.
  설정 모듈은 import 때 data 폴더를 만든다(이번에 바꾸지 않음).

### 리소스 경로 (`path_utils.py`)
- Frozen: `sys._MEIPASS` 또는 `os.path.dirname(sys.executable)` 기준
- Dev: 프로젝트 루트 기준

### DB 모듈 구성 (EO R-06, 2026-10-05)
`core/database/database.py` 는 스키마·업그레이드·이전 DB 초기화·저장(title/detail/merge)·메타를 맡고, 아래로 나눈 이름을 그대로 다시 내보낸다(`database.X` 호출은 그대로).
- `core/database/accounts_repo.py`: 관리자 계정·API 키 CRUD(최초 관리자 생성 잠금 포함).
- `core/database/report_reads.py`: 병합 표 읽기(`load_results*`)·신고번호/ID 조회·차량번호/신고번호 검색.
- `services/collection_policy.py`: 상세 수집 대상 선정(신규·미종결·list-refetch-v1 재조회, community.db sidecar). `database.get_pending_detail_ids` 는 이것을 부른다.
- `core/database/disabled_upgrade.py`: 꺼 둔 이전 DB 업데이트 호출 조각 보관(실행 코드 없음, 다시 켤 자리 번호 ①~⑤).

### DB 자동 마이그레이션 (2026-09-27 초기화 크롤링 릴리스: 비활성)
`upgrade_schema()` — 새 DB 는 지금 스키마로 만들고, 이전 버전 DB 는 옮기지 않는다(열 추가·마이그레이션 코드는 주석으로 남김).
서버 시작 때 `reset_legacy_database()` 가 이전 버전 DB 를 `data/backups/legacy_v*.db` 로 백업한 뒤 비우고 초기화 크롤링으로 다시 채운다.
상세: `data-contracts.md` "2026-09-27 이전 버전 DB 처리".


<!-- legacy CLAUDE.md 665-682 -->
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
