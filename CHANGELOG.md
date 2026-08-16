# Changelog

작업, 버그 수정, 세션 기록용 문서.

- 구조/운영 컨텍스트는 `CLAUDE.md`에 유지
- 리팩토링 작업지시 원문은 `REFACTOR.md`에 유지
- 2026-04-30에 `CLAUDE.md`의 작업 이력 섹션과 최근 세션 메모를 이 파일로 이관

---

## 2026-08-16 (2.5.3)

### 동영상 프록시 tail-follow 전환 (첫 재생까지 전체 다운로드 대기 제거)

상태: 완료

배경:
- 같은 신고 동영상인데 모바일 앱은 즉시 재생되고 웹은 한참 기다린다는 지적
- 원인은 대역폭이 아니라 **재생 시작 조건**이었다
  - 모바일: ExoPlayer 가 원본 URL 직결 + progressive 재생. `moov` + 약 2.5초 버퍼만 차면 첫 프레임
    (upstream 의 `Content-Type: application/download` 는 컨테이너 스니핑으로 무시,
     `Content-Disposition: attachment` 는 브라우저 전용 개념이라 무관)
  - 웹: `/media/proxy` 가 `ensure_cached()` 로 **파일 100% 를 받은 뒤에야** 첫 바이트를 내보냄.
    거기에 프론트가 `GET /media/status` 를 500ms 간격으로 폴링해 `ready` 를 기다린 다음에야
    `<video src>` 를 붙였고, 같은 바이트가 네트워크를 두 번 탔다
- 즉 35MB 영상이면 모바일은 수백 KB 받고 시작, 웹은 35MB 를 다 받고 시작하는 구조였다

변경:
- `services/media_proxy_service.py`
  - `ensure_cached()` 가 다운로드 중에도 `_progress[url]` 로 `total`/`downloaded` 를 공개.
    chunk 를 `flush()` 한 뒤에만 카운터를 올려 리더가 미완성 버퍼를 읽지 않게 함
  - `open_stream(url)` 추가: 캐시 완료면 최종 파일, 아니면 upstream `Content-Length` 확보 즉시
    진행 중인 `.tmp` 를 소스로 반환 (전체 다운로드를 기다리지 않음)
  - `iter_stream(source, start, end)` 추가: 다운로더가 써 내려가는 만큼 따라 읽는 tail-follow 제너레이터.
    `.tmp` 를 먼저 열고 그 사이 rename 됐으면 최종 파일로 폴백 (POSIX 에서 열린 fd 는 rename 후에도 같은 inode)
  - `Content-Encoding` 이 identity 가 아니거나 `Content-Length` 가 없으면 Range 계산이 깨지므로
    그때만 레거시 경로(완료까지 대기)로 폴백
  - 아직 안 받은 오프셋으로의 Range 는 그 지점까지 대기, 정체 120초 초과 시 에러
  - `prime_cache()` 가 작업 제출 전에 진행 상태를 초기화 — 리더가 옛 상태를 보고 없는 `.tmp` 를 여는 race 차단
  - `get_cache_status()` 가 `bytes`/`total` 진행률을 함께 반환, `cleanup_cache()` 가 고아 진행 상태도 정리
- `web/routers/media_route.py`
  - `/media/proxy` 가 `ensure_cached()` 대신 `open_stream()` + `iter_stream()` 사용.
    Range 파싱/`206`/`Content-Range` 계산은 그대로
- `web/templates/base.html`
  - 준비 완료 폴링(`waitUntilProxyReady`) 제거. `prepare` 는 백그라운드 워머를 깨우는 fire-and-forget 으로만 던지고
    `<video src>` 를 즉시 연결
  - 실패는 `<video>` 의 `error` 이벤트로 "동영상 준비 실패" 표시, `loadeddata` 에서 준비 문구 해제
- `CLAUDE.md`
  - 동영상 재생 운영 메모를 tail-follow 모델로 갱신

검증:
- `python3 -m compileall services/media_proxy_service.py web/routers/media_route.py main.py` 통과
- 5MB 페이로드를 2초에 걸쳐 천천히 내려주는 로컬 upstream 으로 스모크:
  - `open_stream()` 0.10초 만에 반환 (`complete=False`, `total` 정상)
  - 첫 바이트 0.00초, 전체 수신 1.81초 → 다운로드를 실시간으로 따라감 (기존이면 약 2.0초 + 폴링 0.5초 뒤에야 첫 바이트)
  - 최종 바이트 수/SHA-256 원본과 일치 (rename 가로지르기 정상)
  - 완료 후 재요청 `complete=True`, 중간 Range 1000바이트 정확, 미수신 구간 Range 는 대기 후 정상 반환

비고:
- 캐시가 이미 있는 URL 의 동작은 종전과 동일 (즉시 206).
- `iter_stream()` 은 동기 제너레이터라 Starlette threadpool 에서 돌아간다.
  대기 중인 스트림 하나가 토큰 하나를 잡으므로 동시 재생 수가 크게 늘면 anyio 기본 한도(40)를 염두에 둘 것.

## 2026-07-01

### macOS x64 self-hosted 빌드 cryptography 소스 빌드 실패 수정

상태: 완료

배경:
- Python 3.14 + x86_64 조합에 맞는 `cryptography` 바이너리 wheel 이 없어 소스(Rust `openssl-sys`)
  빌드로 떨어지는데, self-hosted Intel Mac 러너(236)에 pkg-config/openssl 이 없어
  `Could not find openssl via pkg-config` / `The pkg-config command could not be found` 로 실패
- arm64 잡은 GitHub-hosted `macos-15` 라 툴체인/휠이 갖춰져 있어 문제 없었음

변경:
- `.github/workflows/build-macos-x64-manual.yml`, `.github/workflows/build.yml` (`build-macos-x64` 잡)
  - venv/pip install 직전에 x86_64 Homebrew(`/usr/local`)의 `pkgconf`/`openssl@3` 를 확보하고
    `OPENSSL_DIR`, `PKG_CONFIG_PATH`, `PATH` 를 후속 스텝에 전달하는 스텝 추가
  - x86_64 빌드이므로 arm64(`/opt/homebrew`)가 아니라 반드시 `/usr/local` openssl 을 사용

검증:
- `yaml.safe_load` 로 두 워크플로 파싱 성공 + 스텝 삽입 확인
- 임베드 셸 스크립트 `bash -n` 통과

## 2026-05-20

### 신고 지도 주소지 원형 분리 확대 기준 완화

상태: 완료

변경:
- `web/templates/report_map.html`
  - 주소지 단위 원형 마커가 더 낮은 확대 단계에서도 보이도록 클러스터 해제 기준을 `zoom 12`로 앞당김
  - 중간 확대 단계에서도 주소지 묶음이 더 빨리 풀리도록 줌별 `maxClusterRadius` 를 축소

검증:
- `git diff --check -- CHANGELOG.md web/templates/report_map.html`

### 신고 지도 주소 팝업 재오픈 회귀 수정

상태: 완료

변경:
- `web/templates/report_map.html`
  - 주소지 원형 마커 클릭 시 팝업을 매번 다시 `bindPopup()` 하지 않고, 주소별 `L.popup()` 객체를 재사용하도록 조정
  - 같은 마커를 한 번 닫은 뒤 다시 눌렀을 때 팝업이 즉시 닫히며 재오픈되지 않던 Leaflet 토글 충돌을 해소

검증:
- `git diff --check -- web/templates/report_map.html`

### 지오코딩 self-lock 제거 + queued 백필 자동 재개

상태: 완료

변경:
- `core/database/database.py`, `services/geocode_service.py`
  - 크롤링 상세 저장 중 지오코딩 캐시를 별도 SQLite write 연결로 다시 건드리며 `database is locked` 가 나던 흐름을 정리
  - detail 저장 트랜잭션 안에서 같은 연결로 주소 지오코딩 준비/캐시 upsert 를 수행하도록 순서를 조정
  - 지도 진행률 상태에 `queued` 를 추가하고, 크롤링 중에는 새 백필을 즉시 돌리지 않고 대기 상태로 유지
- `services/crawl_manager.py`, `main.py`
  - 크롤링 종료 직후와 서버 시작 시 queued/pending/error 지오코딩 백필을 자동 재개하도록 연결
- `services/db_backup.py`, `web/templates/report_map.html`
  - 모바일 DB 업로드/복원 시 stale `map_backfill_state` 를 버리고 서버 기준으로 다시 백필 상태를 계산
  - 웹 지도 진행률 카드가 `queued` 상태를 일반 완료가 아니라 대기 상태로 표시하도록 조정
- `tests/test_geocode_service_regression.py`, `tests/test_start_reset_regression.py`
  - self-lock 회귀, queued 상태 전이, 서버 시작 후 자동 재개 흐름을 회귀 테스트로 보강

검증:
- `./venv/bin/python -m unittest tests.test_geocode_service_regression tests.test_db_backup_regression tests.test_start_reset_regression -v`

## 2026-05-18

### 신고 지도 지점 색상 구분 + 주소별 신고 리스트 바로가기

상태: 완료

변경:
- `web/templates/report_map.html`
  - 지도 최종 지점 원형 마커 색상을 `과태료` 처분 비중에 따라 구분
  - 과태료 비중 `60% 이상`은 초록, `50% 이상`은 주황, `50% 미만`은 빨강으로 표시
  - 지점 마커 클릭 시 간단한 현황 팝업을 띄우고, 팝업 아래 `리스트 보기` 링크를 추가
  - `리스트 보기`는 현재 지도 카테고리를 유지한 채 주소 필터가 적용된 `data_table` 화면으로 연결

검증:
- `venv/bin/python -m unittest discover -s tests -v`
- `report_map.html` 템플릿 샘플 렌더링 확인

## 2026-05-17

### 신고 지도, 주소 지오코딩, 지도 통계 화면 추가

상태: 완료

변경:
- `services/geocode_service.py`
  - 카카오 REST API 기반 주소 정규화/좌표 변환 서비스 추가
  - `mysafety_sync_meta` 에 백필 진행 상태를 영속화하고, 지도 첫 진입 시 누락 좌표 백필이 시작되도록 조정
  - `config_required/running/error/completed` 상태와 진행률 퍼센트 제공
- `core/database/models.py`, `core/database/database.py`
  - detail/merge 테이블에 `주소정규화`, `행정구역`, `위도`, `경도`, `지오코딩상태` 컬럼 추가
  - 주소 단위 좌표 재사용용 `mysafety_geocode_cache` 테이블 추가
  - 신규/수정 신고 저장 시 기존 주소면 geo payload 재사용, 주소 변경 시 pending 상태로 재지정
- `web/routers/stats.py`, `web/routers/api_route.py`, `services/data_service.py`, `services/report_stats_service.py`
  - `/stats/map`, `/stats/map/progress`, `/api/v1/stats/map`, `/api/v1/stats/map/progress` 추가
  - dedupe 기본 정책을 공용 helper 로 통합하고 지도 집계는 대표건 설정을 자동 반영
- `web/templates/report_map.html`, `web/templates/base.html`, `web/templates/settings.html`, `web/routers/settings_route.py`
  - 사이드바 `신고 지도` 메뉴, 지도 화면, 진행률 카드, 카카오 REST API 키 설정 UI 추가
  - Leaflet 기반 클러스터 지도, 확대 단계별 집계 원, 행정구역/기관/처분 현황 tooltip, adaptive placement 반영

검증:
- `venv/bin/python -m compileall core services web start.py main.py`
- 백업 DB 복제본에 `upgrade_schema()` 적용 후 지도 집계 함수 스모크 테스트
- API 키 미설정 시 `config_required` 응답과 진행률 카드 경고 문구 확인

### 대시보드/지도 집계 기준 정리

상태: 완료

변경:
- `services/report_stats_service.py`, `web/routers/api_route.py`, `web/templates/index.html`
  - `취하 데이터 숨기기` 가 켜져 있어도 카드/요약에는 실제 취하 건수를 유지하고, 그래프 반영용 값만 `withdrawGraphCount` 로 분리
- `services/report_stats_service.py`, `web/templates/report_map.html`
  - 지도 집계와 tooltip 의 처리기관명 표시가 `경찰기관명 정규화` 옵션을 따르도록 정리
  - 클러스터/개별 원 tooltip 에 기관별 건수, 처리상태 비중, 처분 비중, 주요 행정구역 줄바꿈 표시 반영
- `start.py`
  - `--reset` 시 `mysafety_geocode_cache` 는 보존하고 크롤링 데이터/중복군만 초기화하도록 정리

검증:
- 임시 SQLite DB에서 `--reset` 후 geocode cache 보존 스모크 테스트
- 지도 tooltip 렌더링용 payload 생성과 줄바꿈/기관 목록 포맷 점검

### 리팩토링·안정화 후속 조치

상태: 완료

변경:
- `web/routers/filters.py`, `web/routers/stats.py`, `web/routers/api_route.py`, `web/routers/data.py`, `web/routers/dashboard.py`
  - dedupe/category normalize helper 공용화
- `services/geocode_service.py`
  - `fcntl` 이 없는 환경에서도 `O_EXCL` lock file fallback 으로 중복 백필 시작을 막도록 보강
  - stale lock 정리와 lease 기반 진행 상태 복구 보완
- `tests/test_geocode_service_regression.py`, `tests/test_start_reset_regression.py`
  - stale lease, cross-platform lock fallback, reset cache 보존 회귀 테스트 추가

검증:
- `venv/bin/python -m unittest discover -s tests -v`

### 지도 좌표 캐시 재사용 경고 + 처분 현황 `미확인` 분리

상태: 완료

변경:
- `services/geocode_service.py`, `web/templates/report_map.html`
  - 카카오 REST API 키가 한 번 등록돼 일부 주소가 이미 좌표화된 뒤 키가 제거되면, 저장된 좌표와 `mysafety_geocode_cache` 로 채울 수 있는 신고는 계속 지도에 표시
  - DB/캐시에 없는 새 주소만 더 이상 좌표 변환을 못 하는 경우 `config_warning` 상태와 전용 경고 문구를 표시
  - API 키가 다시 입력되면 같은 지도 화면에서 경고를 해제하고 일반 백필 흐름으로 복귀
- `services/report_stats_service.py`, `web/templates/stats.html`, `web/templates/report_map.html`
  - 기관 통계와 지도 tooltip 의 처분 현황에서 `불수용/기타` 묶음은 유지하고, 기존 `기타/미확인` 묶음은 `미확인` 단독 bucket 으로 분리
  - 지도 meta 에 `agency_count` 를 추가해 모바일 요약 카드도 같은 집계 기준을 재사용
- `tests/test_geocode_service_regression.py`
  - API 키가 없어도 저장 좌표/캐시 기반 표시가 유지되는 경우와, 새 주소만 남았을 때 `config_warning` 으로 전환되는 경우를 회귀 테스트로 추가

검증:
- `venv/bin/python -m unittest discover -s tests -v`
- `python3 -m compileall services/geocode_service.py services/report_stats_service.py web/templates/report_map.html web/templates/stats.html`

### 신고 지도 미변환 주소 목록 + 클러스터 숫자 기준 보정

상태: 완료

변경:
- `services/report_stats_service.py`, `services/data_service.py`, `web/routers/stats.py`, `web/routers/api_route.py`
  - 신고 지도와 같은 필터/중복 대표건 기준을 따르는 `미변환 주소 그룹` payload 추가
  - 웹 `/stats/map/missing`, 모바일 client 연동용 `/api/v1/stats/map/missing` API 추가
  - 주소별로 미변환 신고건을 그룹화하고, 각 그룹 안에 실제 신고 row payload 를 함께 내려주도록 정리
- `web/templates/report_map.html`
  - 신고 지도 우측 상단에 `미변환 주소` 버튼 추가
  - 주소 그룹 수/신고건 수를 보여주는 모달과 신고건 리스트 UI 추가
  - 파란 클러스터 원 숫자가 더 이상 주소 그룹 수가 아니라, 내부 주황색 원들의 `신고건수 합` 을 표시하도록 수정

검증:
- `python3 -m compileall services web/routers`
- `venv/bin/python -m unittest discover -s tests -v`

## 2026-05-13

### raw_content 저장 누락 복구 + 취하 숨김 summary/stats 정렬

상태: 완료

변경:
- `services/parser.py`, `core/crawler/detail_pipeline.py`, `core/database/database.py`
  - API/레거시 상세 파서가 신고 본문 원문을 `raw_content` / `raw_type='report_body'` 로 함께 넘기고, detail upsert 시 `mysafety_raw_content` 에도 같이 저장하도록 보강.
  - 같은 payload 재크롤이면 `saved_at` 를 유지하고, 실제 payload 가 바뀐 경우만 갱신되도록 정리.
  - 그 결과 서버 `--reset` 후 재크롤링에서도 `mysafety_raw_content` 기반 payload exact 중복군이 다시 생성된다.
- `services/report_stats_service.py`
  - `exclude_withdraw=True` 일 때 `/summary` 의 `withdrawCount` / `withdraw_pct` 를 0 으로 내려 모바일/웹 대시보드 그래프와 카드 기준을 일치화.
  - 대신 실제 원본 취하 개수는 `withdrawRawCount` 로 별도 노출.
  - 기관 통계(`get_agency_stats`)도 전역 `취하 데이터 숨기기` 설정을 따라 `취하` row 를 제외하도록 정리.

검증:
- `python3 -m compileall services/parser.py core/crawler/detail_pipeline.py core/database/database.py services/report_stats_service.py`
- `venv/bin/python` 임시 SQLite 시뮬레이션
  - 동일 `raw_content` 2건 저장 후 `mysafety_raw_content` 2행, 중복군 1개/멤버 2건 생성 확인
  - `exclude_withdraw=True` 에서 `/summary` 기준 `withdrawCount=0`, `withdrawRawCount=1`, 기관 통계 total=1 확인

### reset 크롤링 시 예전 보완 history 테이블 잔재 정리

상태: 완료

변경:
- `start.py`
  - `--reset` 크롤링 시 현재 운영 테이블(`title/detail/merge`, `entry_value`, `raw_content`, `duplicate_*`) 초기화와 별도로, 중간 실험 빌드에서 잠시 생성됐을 수 있는 `mysafety_supplement_history` 잔재 테이블도 함께 `DROP TABLE IF EXISTS` 하도록 보강.
  - 최종 설계는 보완요청 전용 별도 테이블 없이 detail/merge 보완 컬럼을 쓰므로, reset 후에도 스키마가 현재 운영 모델과 일치하도록 정리.

검증:
- `python3 -m compileall start.py`

### 상태 계층 재설계 구현

상태: 완료

변경:
- `services/parser.py`
  - API/레거시 상세 파서가 `상태(raw)` 와 `처리상태(canonical)` 를 분리해 저장하도록 정리.
  - API `C_NOW=0` 일반 미종결은 `상태=진행`, `처리상태=처리중` 으로 저장하고, 열린 보완은 `처리상태=보완요청` 으로 승격.
  - 닫힌 샘플 `57822934` 같은 케이스에서 raw `답변완료` + canonical `수용` 이 유지되도록 final 상태 분기 보강.
- `core/database/database.py`
  - `upgrade_schema()` 에 legacy row 정규화(backfill) 추가.
  - `get_pending_detail_ids()` 가 더 이상 `상태 != 처리상태` mismatch 에 의존하지 않고, `detail 없음 / 종결여부 != 'Y' / 보완_미응답='Y'` 중심으로 재크롤링 대상을 선정.
- `services/report_query_service.py`, `services/report_stats_service.py`, `web/templates/*.html`
  - 검색/통계/대시보드/감시목록이 canonical `처리상태` 기준으로 동작하도록 정리.
  - raw legacy 값 `진행/진행중/검토중` 은 UI에서 `처리중` 버킷으로만 취급.
- `web/templates/db_editor_form.html`
  - 처리상태 수동 편집 옵션을 canonical 상태 전체(`처리중/보완요청/수용/일부수용/불수용/기타/답변완료/취하/이송`)로 확장.
- `docs/status-layer-redesign-plan-2026-05.md`
  - 구현 메모와 샘플 검증 결과 반영.

검증:
- `python3 -m compileall services core web/routers start.py`
- `venv/bin/python` 으로 `upgrade_schema(sqlite:///:memory:)` smoke test 통과
- 샘플 JSON 파싱 확인
  - `59614484`: raw `진행`, canonical `보완요청`
  - `57822934`: raw `답변완료`, canonical `수용`
  - `40871819`: raw/canonical `취하`
  - `59216726`: raw `진행`, canonical `처리중`

## 2026-05-12 (문서/마무리)

### 보완요청 검색 항목과 대시보드 노출 문서 반영

상태: 완료

변경:
- `web/templates/data_table.html`
  - 신고내역 검색에서 처리상태 `보완요청` 을 선택할 수 있도록 상태 옵션 노출 보강
  - 기존 raw 상태값 `진행/진행중/처리중` 은 검색 UI에서 `처리중` 하나로만 노출하고, 실제 매칭은 세 값을 모두 포함하도록 정규화
  - `보완횟수` 검색 입력 추가 및 row filtering 연결
  - `과태료` 검색 라벨을 실제 컬럼 의미에 맞게 `범칙금/과태료` 로 정리
- `README.md`
  - 대시보드 `보완 요청` 카드와 신고내역 `보완요청`/`보완횟수` 검색 지원을 기능 설명에 반영

비고:
- 서버 워크트리의 `VERSION` 변경은 이번 커밋 범위에 포함하지 않는다.

## 2026-05-12 (최종 정리)

### 보완요청 마지막 round 구조화 + 공식 앱 딥링크 정리

상태: 완료

배경:
- 같은 날 오후 정리본에서는 보완 요청자 정보를 `보완_요청_내용` prefix 문자열에 섞어 보존했지만, 모바일/웹 모두에서 답변 담당자와 보완 요청자를 명확히 구분하려면 별도 필드가 더 적합했다.
- 동시에 웹 상세 모달의 "안전신문고 앱에서 보기" 버튼도 공식 앱 딥링크를 먼저 시도하고 웹으로 fallback 하는 형태로 마무리할 필요가 있었다.

변경:
- `core/database/models.py`
  - detail / merge 테이블에 `보완_요청자`, `보완_요청일시`, `보완_완료일시` 3개 컬럼 추가.
- `services/parser.py`
  - `_build_supplement_summary()` 가 마지막 round 를 `{count, is_open, requester, requested_at, completed_at, request_text, reporter_opinion}` 구조로 압축.
  - `보완_요청_내용` 은 이제 본문만 저장하고, 요청자/일시는 별도 필드로 분리.
  - 레거시 쪽 보완 round 판정은 `parse_supplement_rounds_from_html(page_soup)` 호출 + `any(is_open)` 검사로 단순화.
- `core/crawler/detail_pipeline.py`, `core/database/database.py`, `services/report_stats_service.py`, `services/crawl_state_store.py`, `services/db_backup.py`
  - 새 3개 필드가 DB 저장, merge, API/알림 payload, 모바일 DB import 경로까지 모두 이어지도록 반영.
- `services/supplement_parser.py`
  - 사용하지 않는 `source_type`, `has_open_round()`, 문서상 full-history 전제 설명을 제거하고 레거시 round 파서 본체만 남김.
- `web/templates/base.html`
  - 보완 카드에 `보완 요청자 / 요청 일시 / 완료 일시` 메타 행 추가.
  - `appsafetyreport://view?c_no=...&ext_path=M_MY_01_S0002.html&mem_yn=Y` 딥링크를 먼저 시도하고, 앱이 열리지 않으면 기존 웹 상세 URL로 fallback.
  - 버튼 문구를 `안전신문고 앱에서 보기` 로 통일.

비고:
- `보완횟수` 컬럼은 그대로 유지되고, data table 은 횟수만 보여준다.
- 보완요청 연락처는 최종 UI/모바일 요구사항 범위가 아니어서 구조화 필드로 올리지 않았다.

## 2026-05-12 (오후)

### 보완요청 저장 모델 단순화 — 마지막 round 1세트 + 누적 횟수만 보존

상태: 완료

배경:
- 같은 날 오전에 추가한 `mysafety_supplement_history` 다회차 보존 모델은 (a) API 모드에서 한 번 더 HTML 페이지를 크롤링해야 했고 (b) 모바일/웹 양쪽에 별도 endpoint·테이블·UI 가 필요해 비용 대비 정보 가치가 낮았다.
- 정책 변경: 신고 row 에 **마지막 round 1세트 + 누적 횟수** 만 보존한다. 마지막 답변자와 최종 판정자가 다를 수 있으므로 보완 요청 내용 본문 prefix 에 요청자/연락처/요청·완료 일시를 함께 텍스트로 묶어 표시.

변경:
- `core/database/models.py`
  - `mysafety_supplement_history` 테이블 정의 삭제
  - `detail_traffic/parking/other_*`, `mysafetymerge_*` 에 `보완횟수 (INTEGER)`, `보완_미응답 (TEXT 'Y'/'N')`, `보완_요청_내용 (TEXT)`, `보완_신고자_의견 (TEXT)` 4개 컬럼 추가 — `upgrade_schema()` 가 기존 DB 에 ALTER TABLE 자동 적용
- `core/database/database.py`
  - `supplement_history_to_sql`, `get_supplement_history_for_report`, `get_open_supplement_count`, `_normalize_supplement_round`, `_SUPPLEMENT_HISTORY_FIELDS` 모두 삭제
  - `_get_new_and_incomplete_ids()` 가 `detail.보완_미응답='Y'` 도 항상 재크롤링 대상에 포함하도록 일원화 (예전의 supplement_history 조회 + `처리상태='보완요청'` 이중 클로즈 제거)
  - `detail_to_sql()` 다시 5-tuple 입력으로 복귀, 보완 history 별도 upsert 호출 없음
  - `_merge_for_table()` SELECT 에 새 컬럼 4개 포함
- `services/parser.py`
  - `_build_supplement_summary(rounds)` 신규 — round 리스트 → `{count, is_open, request_text, reporter_opinion}` 단일 dict
  - 레거시 `parse_details()` 가 round 리스트 추출 후 마지막 round 만 summary 로 압축. 본문 prefix: `"보완 요청자: <name> (<phone>) · 요청 일시: ... · 완료 일시: ..."` 또는 미응답 시 `완료 일시: (미응답)` 마커
  - `parse_json_details()` 에서 `_build_last_supplement_round_from_json()` 으로 단일 round 추출. JSON 의 `SPLMNT_DMND_NO` 가 누적 횟수 source of truth
  - 종결 상태(취하/답변완료 등) 인 신고는 미응답 마지막 round 도 `is_open='N'` 로 강제 마감
  - `supplement_history` 키 emit 제거, 대신 `supplement_summary` 키 emit
- `core/crawler/detail_pipeline.py`
  - `DETAIL_COLUMNS` 에 보완요청 컬럼 4개 추가
  - `build_detail_dataframe()` 가 `details["supplement_summary"]` 를 읽어 row 에 합쳐 넣음
  - `build_detail_result()` 다시 5-tuple 반환 (supplement_history 제거)
- `core/crawler/crawldetail_api.py`
  - `_fetch_supplement_html_via_browser`, `_fetch_supplement_html_via_http`, `_maybe_enrich_supplement_history` 모두 삭제 — JSON 만으로 마지막 round 충분
- `services/crawl_state_store.py`
  - `save_crawl_changes()` 이 detail row 의 `보완횟수 / 보완_미응답 / 보완_요청_내용 / 보완_신고자_의견` 을 그대로 payload 에 옮긴다. `change_reason` 은 `보완_미응답='Y'` 또는 `처리상태='보완요청'` 이면 `supplement`
  - `save_crawl_done_ext()` 의 report change 항목에 `supplement_count` 전파
- `services/report_query_service.py`
  - `_load_supplement_round_counts`, `_inject_supplement_counts` 삭제 (이제 `보완횟수` 가 detail/merge 의 실제 컬럼)
- `services/report_stats_service.py`
  - `_REPORT_FIELDS` 에 보완 컬럼 4개 추가 → recent_answers/watchlist 카드도 자동 포함
  - 대시보드 별도 보완 카운트 후처리 코드 삭제
- `services/db_backup.py`
  - 모바일 → 서버 import 시 `report_supplement_history` round-trip 코드 삭제. 새 4 컬럼은 reports 테이블 import 경로에서 자연스레 따라옴
- `web/routers/api_route.py`
  - `GET /api/v1/supplements`, `GET /api/v1/supplements/{record_id}` 삭제
- `web/routers/data.py`
  - `GET /data/supplements/{record_id}` 삭제
- `web/templates/base.html`
  - 상세 모달의 `rdSupplements` 섹션을 마지막 round 단일 카드로 단순화. record.보완_요청_내용 / 보완_신고자_의견 / 보완횟수 / 보완_미응답 만 사용, 비동기 fetch 제거
- `start.py`
  - `--reset` drop 대상에서 `supplement_history_table` 제거

검증:
- `services.parser.parse_json_details(testresults/59614484)` → `progress_status='보완요청'`, `processing_finish='N'`, `supplement_summary={count:1, is_open:'Y', request_text=...}` (이민지 prefix 포함)
- `services.parser.parse_json_details(testresults/40871819)` → `progress_status='취하'`, `supplement_summary={count:2, is_open:'N', request_text=...}` (마지막 round = 이미숙)
- `parse_details()` 레거시 HTML 도 같은 4-key summary 산출. 다회차여도 마지막 round 만 보존 + count 정확.
- `database.detail_to_sql([...])` 시나리오: 신규 → '신규', 같은 입력 → 빈 changed_item_ids, 보완 요약 변경 → '변경'. detail row `처리상태='보완요청'`, `보완_미응답='Y'`, `보완횟수=1`, `보완_요청_내용` prefix 정상.
- `database.get_pending_detail_ids()` 가 `보완_미응답='Y'` row 를 항상 재크롤링 대상으로 포함.

비고:
- 마이그레이션: 이전 빌드의 `mysafety_supplement_history` 테이블은 그대로 남아 있어도 무해 (참조 없음). 다음 정기 정리 때 수동 DROP 권장.
- 모바일 standalone 빌드도 같은 4 컬럼만 보존하도록 동시에 정리됨 (mobile 레포 별도 커밋).

---

## 2026-05-12

### 보완요청 상태 및 다회차 보완 이력 보존

상태: 완료

변경:
- `core/database/models.py`
  - `mysafety_supplement_history` 테이블 추가 (PK: `ID + round_no`)
  - round별 보완 요청자/연락처/요청·완료 일시/요청 내용/신고자 보완 의견·차량번호·발생일자·시각·위반장소·첨부파일·지도·`is_open`·`source_type` 저장
- `core/database/database.py`
  - `supplement_history_to_sql()` row-level diff 후 upsert + 사라진 round 정리, `get_supplement_history_for_report()`, `get_open_supplement_count()` 추가
  - `_get_new_and_incomplete_ids()` 가 `is_open='Y'` 보완 history 와 `처리상태='보완요청'` detail row 도 항상 재크롤링 대상에 포함
  - `detail_to_sql()` 6-tuple 입력 지원: detail 변화가 없어도 history 변경이 있으면 `changed_item_ids` 에 추가해서 알림에 잡히게
- `services/supplement_parser.py` (신규)
  - `splmntDivBody` 의 table 들을 round 리스트로 추출. round 별 신고자 보완 의견에서 차량번호/발생일자/발생시각/위반장소 정규화
  - `latest_completed_overrides()` 가 마지막 완료 round 기준 detail field override 반환
- `services/parser.py`
  - 레거시 `parse_details()` 가 `supplement_parser` 로 round 전체를 추출해 `supplement_history` 로 반환
  - 보완요청이 열려 있으면 `processing_status='보완요청' / 종결여부='N'`, 종결 상태에서는 모든 round 의 `is_open` 을 `N` 로 닫는다
  - API `parse_json_details()` 가 `SPLMNT_DMND_NO + SPLMNT_FNSH_YN/CMPTN_YN + answers + C_NOW` 조합으로 `보완요청` 진행상황 판정. JSON 만으로 만들 수 있는 round 조각도 `supplement_history` 로 반환
- `core/crawler/detail_pipeline.py`
  - `build_detail_result()` 결과 tuple 에 `supplement_history` 추가 (6-tuple)
- `start.py`
  - `--reset` drop 대상에 `mysafety_supplement_history` 추가
- `services/crawl_state_store.py`
  - `save_crawl_changes()` 각 report payload 에 `change_reason ('supplement' | 'report')`, `supplement_open`, `supplement_round_no`, `supplement_round_count` 추가
  - `save_crawl_done_ext()` 의 report change 항목에 동일 메타 전파 (크롬 확장 알림에서 보완요청 구분 가능)
- `services/report_stats_service.py`
  - 대시보드 `supplementCount` / `supplement_pct` 산출 추가. valid_total 분모에 포함
- `web/templates/index.html`
  - `총 신고 → 보완 요청 → 처리 중 → 답변 완료 → 취하` 5-카드 레이아웃. 보완 카드 클릭 시 `/data/all?status=보완요청`
  - 처리상태 progress bar 에 보완 요청 구간 표시 (`supplementCount > 0` 일 때)
  - watchlist/recent_answers 배지 보완요청 색상 (`#fd7e14`) 처리
- `web/templates/data_table.html`
  - `preferredStatusOrder` 에 `보완요청` 포함, `renderBadge()` 에 보완요청 색상 분기 추가
- `web/routers/api_route.py`
  - `GET /api/v1/supplements` (전체 열린 round 목록)
  - `GET /api/v1/supplements/{record_id}` (단일 신고 round 이력)
- `services/db_backup.py`
  - 모바일 standalone `report_supplement_history` 테이블이 있으면 round-trip 으로 보존 (`OperationalError` 무시 — 구버전 모바일 DB 호환)
  - 복원 단계에서 supplement_history 도 통째로 비우고 다시 채움

검증:
- `python3 -c "from core.database import database; ..."` 모듈 import OK
- `services.supplement_parser.parse_supplement_rounds_from_html(...)` 가 testresults/40871819 (다회차 — 양훈석/이미숙), 59614484 (단일 open round — 이민지) 양쪽에서 round 수/요청자/완료일/override 정확히 산출
- `services.parser.parse_json_details(testresults/59614484_api_raw.json)` → `progress_status='보완요청'`, `processing_status='보완요청'`, `processing_finish='N'`, history 1 round (open=Y)
- `services.parser.parse_json_details(testresults/40871819_api_raw.json)` → `progress_status='취하'`, history 2 round (둘 다 is_open=N)
- `database.detail_to_sql([6-tuple], engine)` 시나리오: 신규 → '신규', 같은 입력 재실행 → 빈, history 만 변경 → '변경'
- `database.get_pending_detail_ids(engine)` 가 detail/title 가 모두 보완요청 상태로 일치해 있어도 supplement history 의 `is_open='Y'` 만으로 ID 를 다시 잡아오는 것을 확인

비고:
- 신고 본 row 는 현 상태의 projection 으로 유지하되, 보완 이력 전체는 별도 history 테이블에 저장 — 신고 메인 필드(차량번호/발생일자·시각/위반장소)는 기존처럼 마지막 완료 round 의 신고자 의견으로 override
- API 모드는 JSON 응답이 보통 "현재 round + 직전 완료 round" 일부만 노출하므로 다회차 이력은 레거시 모드 또는 동일 ID 재크롤링으로 보강된다 (HTML 전체 supplement 영역 hybrid fetch 는 후속 작업)
- crawl_changes payload 의 `change_reason` 은 신고 자체가 보완요청 상태이거나 보완 history 에 열린 round 가 있을 때 `supplement`, 그 외는 `report` — 모바일/크롬 확장에서 알림 라벨에 활용 가능

---

## 2026-05-08

### Client `crawl_changes` 순서 흔들림 보정 + `synced_at` payload 복구

상태: 완료

변경:
- `services/crawl_state_store.py`
  - `crawl_changes.json` 생성 시 일반 신고 변경 payload에 `notification_kind=report`, `synced_at` 포함
  - `get_merged_records_by_ids()` 결과를 그대로 쓰지 않고 `synced_at DESC`, fallback `답변일 DESC`, `신고번호 DESC` 로 재정렬

검증:
- `python3 -m py_compile services/crawl_state_store.py`

비고:
- 기존에는 모바일 Client가 WS `crawl_changes` 를 받아도 `synced_at` 없이 기록하게 되어 "최근 답변" / 알림 상세 순서를 서버 대시보드와 같은 기준으로 맞출 수 없었다.
- `get_merged_records_by_ids()` 는 내부적으로 `traffic → parking → other` 순으로 결과를 모으므로, 서버가 한 번 더 정렬하지 않으면 모바일 쪽에서 "알림이 온 순서와 비슷한 순서"가 쉽게 흔들릴 수 있었다.

### `last_sync` 처리에 둔 헬퍼/방어 로직 제거 (정공법으로 인라인)

상태: 완료

변경:
- `services/crawl_state_store.py`
  - `set_last_crawl_time` / `get_last_crawl_time` 두 줄짜리 wrapper 제거
- `start.py`
  - `_process_and_save_results` 가 `mysafety_sync_meta` 에 `sqlite_insert(...).on_conflict_do_update(...)` 를 직접 실행 (한 블록)
- `services/report_stats_service.py`
  - `_format_last_sync` 다중 포맷 방어 함수 제거
  - `get_dashboard_stats` 가 `mysafety_sync_meta.last_sync` 를 직접 SELECT 해서 `datetime.fromisoformat(...).strftime(...)` 로 표시
  - `current_crawl.log` mtime fallback 제거 (값이 없으면 `기록 없음`)
  - 사용 안 하게 된 `import os` 정리
- `CLAUDE.md`
  - 위 변경 반영

비고:
- 서버는 항상 `datetime.now().isoformat(timespec='seconds')` 로만 쓰기 때문에 다중 포맷 방어 (`T` ↔ space, `%Y-%m-%d %H:%M:%S` 시도) 는 모두 사용처가 없는 코드였다.
- 헬퍼 두 개도 호출부 1곳씩에서만 쓰이고 SQL 한 줄을 감싸기만 하던 구조라, 인라인 후 호출부 가독성/추적성이 더 좋아진다.
- mtime fallback 은 `last_sync` 가 비어있는 (= 이번 변경 직후 첫 크롤링 전) 경우에만 의미 있던 임시 다리 — 다음 크롤링 한 번이면 자연 해소되므로 제거.

### `--reset` 초기화 누락 보강 + 마지막 크롤링 시각을 `last_sync` 로 영속화

상태: 완료

변경:
- `start.py`
  - `_prepare_database(reset=True)` 가 신고 ID 에 매여 있는 사이드카 테이블 (`mysafety_entry_value`, `mysafety_raw_content`, `mysafety_duplicate_member`, `mysafety_duplicate_group`) 까지 drop 하도록 보강
  - `mysafety_sync_meta` 는 통째로 drop 하지 않고 `key='watchlist'` 만 남긴 채 `last_sync` 등 나머지 키를 삭제 — 다음 크롤링이 다시 채워준다
  - `_process_and_save_results` 가 merge 직후 `crawl_state_store.set_last_crawl_time(engine)` 호출로 마지막 크롤링 시각을 영속 저장
- `services/crawl_state_store.py`
  - `set_last_crawl_time(engine)` / `get_last_crawl_time(engine)` 헬퍼 추가. ISO8601 문자열 (모바일 `_saveSyncTime` 와 동일 형식) upsert
- `services/report_stats_service.py`
  - 대시보드 `last_crawl_time` 을 `current_crawl.log` mtime 대신 `mysafety_sync_meta.last_sync` 에서 읽도록 변경
  - `_format_last_sync` 가 ISO8601 / 레거시 `%Y-%m-%d %H:%M:%S` 형식을 모두 받아 정규화
  - 값이 없으면 기존 mtime fallback 유지
- `core/database/database.py`
  - `duplicate_group_table`, `duplicate_member_table` 모듈 노출 추가 (reset 에서 사용)
- `CLAUDE.md`
  - `mysafety_sync_meta`, reset 정책, last_sync 운영 메모 갱신

비고:
- 기존에는 reset 후에도 옛 신고 ID 의 `entry_value`/`raw_content`/`duplicate_*` row 가 그대로 남아 재크롤링 후 중복군 재계산이나 모바일 import 시 잠재적 orphan 이 될 수 있었다.
- 마지막 크롤링 시각이 로그 파일 mtime 에만 의존했어서 로그 회전/삭제 시 "기록 없음" 으로 떨어졌다. 이제 DB 메타에 영속 보관되며, 모바일과 같은 형식이라 서버↔모바일 DB import 시 round-trip 으로 보존된다.
- `db_backup.restore_from_mobile_db` 의 sync_meta 일괄 교체 동작은 그대로 유지 — 모바일 DB 업로드 시 모바일의 `last_sync` 가 서버의 표시값이 되며, 이는 "방금 모바일 데이터로 서버를 갈아치웠다" 라는 사용자 의도와 일치한다.

### 동영상 프록시 첫 연결 간헐적 실패(TCP RST) 우회

상태: 완료

변경:
- `services/media_proxy_service.py`
  - 동영상 다운로드(`ensure_cached`) 시 `requests.get` 대신 `urllib3.util.retry.Retry`가 적용된 `requests.Session`을 사용하도록 개선
  - 안전신문고 서버 특유의 첫 TCP 연결 RST 또는 간헐적 연결 끊김 현상 발생 시 백그라운드 워커가 설정된 `max_retry` 횟수(기본 5회)까지 자동으로 재시도하도록 보강

비고:
- 기존에는 다운로드 첫 요청이 끊기면 재시도 없이 즉시 에러를 반환하여 프론트엔드가 곧바로 "동영상 준비 실패"를 띄우고 폴링을 멈추는 문제가 있었다. 이번 수정으로 불안정한 연결 상태에서도 훨씬 안정적으로 동영상을 준비할 수 있게 되었다.

---

## 2026-05-07

### 동영상 시킹 복구 + 프록시 캐시 준비를 비동기 분리

상태: 완료

변경:
- `services/media_proxy_service.py`
  - 미디어 URL 호스트를 `*.safetyreport.go.kr`로 제한
  - `prime_cache()` / `get_cache_status()` 추가로 캐시 준비를 백그라운드 작업으로 분리
  - 준비 워커를 4개로 늘려 여러 동영상이 한 번에 warm-up 되도록 조정
- `web/routers/media_route.py`
  - `POST /media/prepare`, `GET /media/status` 추가
  - 실제 `/media/proxy` 파일 응답은 캐시 준비 완료 뒤에만 브라우저가 붙도록 프론트와 연동
- `web/templates/base.html`
  - 상세 모달 동영상을 다시 `/media/proxy` 경유로 되돌림
  - `<video src>`를 즉시 넣지 않고, 준비 상태 문구를 띄운 뒤 캐시 완료 후 `src`를 연결하도록 변경
- `web/templates/data_table.html`
  - 첨부 모달도 동일한 준비/연결 흐름 적용
- `main.py`
  - `/media/` 경로를 로그인 리다이렉트 예외로 처리

검증:
- `python3 -m compileall main.py services/media_proxy_service.py web/routers/media_route.py` 통과
- 서비스 스모크:
  - `prime_cache(url)` 호출 직후 `pending`
  - 다운로드 완료 후 `get_cache_status(url)` → `ready`

비고:
- 원본 첨부 서버는 여전히 `Range`를 무시하고 `200 OK + application/download`만 내려서 직결 재생 시 탐색바가 동작하지 않는다.
- 이번 수정은 "직결로 웹은 덜 막히지만 seek 불가"와 "프록시는 seek 가능하지만 첫 요청이 오래 붙는 문제" 사이에서,
  캐시 준비를 짧은 JSON polling으로 분리해 둘 다 완화하는 방향이다.
- 아래 "동영상 재생 경로 원본 URL 직결로 재복구"는 같은 날의 임시 우회였고, 현재 최종 상태는 이 항목의 비동기 준비 + 프록시 재생 모델이다.

### 동영상 재생 경로 원본 URL 직결로 재복구

상태: 후속 패치로 대체됨

변경:
- `web/templates/base.html`
  - `proxyMediaUrl()`이 다시 원본 URL을 그대로 반환하도록 조정
  - 상세 모달 동영상 `<video src>`가 결과적으로 `/media/proxy` 대신 원본 첨부 URL을 사용
- `web/templates/data_table.html`
  - 첨부 모달 동영상 `<video src>`도 같은 헬퍼를 통해 원본 URL 직결 사용

비고:
- `cache + Range` 프록시 모델은 seek 자체는 처리할 수 있어도, 실제 웹 사용 중에는 같은 오리진의 `/media/proxy` 요청이 누적되면서
  "한 동영상 재생 뒤 다른 화면 로딩 정체"와 `/media/proxy -> /login` 리다이렉트 회귀가 발생했다.
- 이번 복구는 2026-05-07 `09f41c0` 시점과 같은 사용자 체감 동작으로 되돌리는 목적이며,
  프록시 라우트/서비스 코드는 남겨두되 현재 웹 템플릿에서는 사용하지 않는다.

### 동영상 프록시에 자체 Range 핸들링 + 로컬 캐시 도입 (seek 바 복원)

상태: 완료

변경:
- `services/media_proxy_service.py`
  - 단순 pass-through 스트리머에서 "캐시 + Range 핸들러" 모델로 재작성
  - `ensure_cached(url)` → `data/media_cache/<sha256(url)>` 로 upstream 파일을 통째 다운로드 (URL별 `threading.Lock`으로 race 방지, atomic write)
  - `guess_media_type(url)` → URL 경로 기반 `mimetypes` 사용으로 upstream의 `application/download` 덮어쓰기
  - `cleanup_cache(max_age_seconds=7d)` → 만료 캐시 정리 헬퍼
- `web/routers/media_route.py`
  - 캐시 파일을 열어 `Range: bytes=start-end` 헤더를 직접 파싱 후 `206 Partial Content` (또는 `200 OK`)로 응답
  - `Accept-Ranges`, `Content-Range`, `Content-Length` 정확히 세팅
  - upstream 다운로드는 `asyncio.to_thread`로 이벤트 루프 블로킹 없이 처리
- `web/templates/base.html`
  - `proxyMediaUrl()` 헬퍼 부활, 동영상 src를 다시 `/media/proxy?url=...` 경유로 변경
  - `preload="metadata"` 유지, `hidden.bs.modal` abort 핸들러 유지
- `web/templates/data_table.html`
  - 첨부 모달 동영상 src도 프록시 경유로 변경
- `main.py`
  - lifespan startup에서 `media_proxy_service.cleanup_cache()` 호출 (7일 이상 된 캐시 자동 정리)
- `CLAUDE.md`
  - 동영상 재생 운영 메모를 캐시 + Range 모델로 갱신

검증:
- `python3 -m compileall services/media_proxy_service.py web/routers/media_route.py main.py` 통과
- 로컬 스모크:
  - `ensure_cached()` 첫 호출 ~3초(35MB), 2회차 즉시 (cache hit)
  - 라우트 직접 호출:
    - Range 없음 → `200 OK`, `Content-Length=37393212`, `Accept-Ranges=bytes`
    - `bytes=0-1023` → `206`, `Content-Length=1024`, `Content-Range=bytes 0-1023/37393212`
    - `bytes=1000000-` → `206`, `Content-Range=bytes 1000000-37393211/37393212`

비고:
- 직접 `curl -H "Range: bytes=0-1023" <upstream URL>`로 확인한 결과 upstream(`safetyreport.go.kr`)이 Range를 무시하고 `200 OK`로 전체 파일을 보낸다. 또한 `Content-Disposition: attachment`, `Content-Type: application/download`까지 붙여 직결 시 브라우저가 streaming 미디어로 다루지 않는다.
- 따라서 직결 + pass-through 프록시 둘 다 seek 동작을 보장하지 못하므로, 프록시에서 자체적으로 Range를 처리해야 함이 확정되었다.
- 첫 요청은 전체 다운로드가 끝나야 첫 바이트가 나가지만, 같은 URL에 대한 이후 요청은 디스크에서 즉시 부분 응답된다. 모달을 닫았다 다시 열어 seek해도 재다운로드 없음.
- 캐시 파일은 `data/media_cache/<sha256(url)>` 한 단계 평면 구조이며, gitignore의 `data/` 규칙에 의해 자동 제외된다.

### 상세 모달 동영상 직결 재생 복원 + 닫기 시 다운로드/오디오 abort

상태: 완료

변경:
- `web/templates/base.html`
  - `#reportDetailModal` 동영상 src를 `/media/proxy?url=...` 경유 → 원본 URL 직결로 되돌림
  - `proxyMediaUrl()`, `window.handleProxyVideoError` 헬퍼 제거 (호출처 제거에 따라 dead code)
  - `<video preload="metadata">` 유지로 모달 오픈 시 메타데이터 + 첫 프레임만 선로딩
  - 모달 생성 시점에 `hidden.bs.modal` 이벤트 핸들러를 한 번 등록해, 닫힐 때 내부 `<video>`를 `pause()` → `removeAttribute('src')` → `load()` 순으로 정리
- `web/templates/data_table.html`
  - 첨부파일 인라인 미디어용 `#attachModal` 동영상 src도 원본 URL 직결로 변경
  - `#attachModal`에 동일한 `hidden.bs.modal` 핸들러 추가

검증:
- 별도 빌드 없음 (HTML 템플릿 변경)

비고:
- 직전 중복 신고 관리 패치(2026-05-06)에서 동영상 재생 경로를 `/media/proxy` 통과 + `preload="none"`로 바꿨었다. 그 결과 (1) 재생 버튼을 눌러야 다운로드가 시작되는 회귀, (2) `/media/proxy`가 단순 통과 프록시여서 Range 협상이 안 되어 upstream이 200으로만 응답 → MP4 `moov` 위치에 따라 스피너가 끝없이 도는 회귀가 동시에 생겼다.
- 본 패치는 영상 src를 원본 URL 직결로 되돌려 패치 이전과 동일한 Range 협상 경로를 복원한다. 원본 URL 직결은 브라우저가 직접 Range 헤더를 협상해 정상 재생된다.
- `media_proxy_service.py` + `/media/proxy` 라우트는 코드만 남기고 호출처는 없는 dormant 상태로 둔다. 추후 진짜 Range 핸들링을 갖춘 프록시가 필요해질 때 재활성화 가능.
- 영상 재생 중 모달을 백드롭/X/ESC로 닫아도 `<video>` 요소가 DOM에 남아 오디오가 계속 재생되던 문제는 `hidden.bs.modal` 핸들러로 해결.
- `pause()`만으로는 in-flight HTTP 요청이 끊기지 않는 브라우저가 있어 `removeAttribute('src') + load()`로 abort까지 강제한다.

### 모바일 Client 다중 파일 다운로드/삭제 302 리다이렉트 수정

상태: 완료

변경:
- `web/routers/api_route.py`
  - API 키 인증으로 동작하는 `POST /api/v1/files/download-multi`, `POST /api/v1/files/delete-multi` 추가
- `services/file_service.py`
  - 모바일 Client가 받는 상대 경로(`logs/...`, `results/...`) 기준의 다중 ZIP 생성/삭제 공용 로직 추가
- `safetyreport-mobile/lib/services/server_contract.dart`
- `safetyreport-mobile/lib/services/api_service.dart`
  - 모바일 Client의 다중 파일 다운로드/삭제 호출을 세션 로그인용 `/file-browser/*` 레거시 경로 대신
    API 키 기반 `/api/v1/files/*` 경로로 전환

검증:
- `python3 -m compileall services/file_service.py web/routers/api_route.py` 통과
- `dart analyze lib/services/server_contract.dart lib/services/api_service.dart`
  - 에러 없음
  - 기존 style info 4건만 잔존

비고:
- 원인은 모바일 Client가 API 키만 가진 상태에서 세션 로그인용 `/file-browser/download-multi`를 호출해
  `/login`으로 `302` 리다이렉트되던 것이었다.

### 데이터 테이블 선택건 큐 크롤링 추가

상태: 완료

변경:
- `services/crawl_control.py`
  - 웹에서 여러 신고번호를 한 번에 큐 크롤링으로 넘길 수 있는 `enqueue_reports()` 추가
  - 이미 크롤링이 실행 중이면 모바일 enqueue와 같은 방식으로 pending queue에 붙이고,
    비실행 중이면 선택된 신고번호만 담은 큐 파일로 즉시 크롤링 시작
- `web/routers/crawl.py`
  - `POST /crawl/enqueue-selected` 추가
  - 선택 신고번호 목록을 받아 다건 큐 크롤링 시작 또는 pending queue 추가 처리
- `web/templates/data_table.html`
  - 전체/교통위반/주정차위반/기타위반/중복차량 공용 `data_table` 툴바에
    `선택건 크롤링` 버튼 추가 (상단/하단)
  - 체크된 신고번호를 `/crawl/enqueue-selected`로 보내는 AJAX 연결

검증:
- `python3 -m compileall services/crawl_control.py web/routers/crawl.py` 통과

비고:
- 웹 선택건 큐 크롤링도 모바일 단건 enqueue와 동일하게
  "실행 중이면 대기열, 아니면 즉시 큐 지정 크롤링" 규칙을 따른다.

### 서버-모바일 DB 왕복 변환 항목 전수 점검 + 메타데이터 보존 보강

상태: 완료

변경:
- `core/database/models.py`
  - `mysafety_sync_meta` 테이블 추가
  - 모바일 `sync_meta`의 `last_sync`, `watchlist`, 기타 key/value 메타를 서버 DB에도 보존할 수 있게 구조 확장
- `services/db_backup.py`
  - 모바일 DB 복원 시 `reports`, `report_raw`, `entry_value`, `watchlist`뿐 아니라 `sync_meta`, `duplicate_group`, `duplicate_member`도 함께 서버 DB로 이관
  - 중복군은 모바일 레거시 hash가 들어와도 `raw_content` 기준 SHA-256 canonical group_id로 재매핑해 서버와 같은 그룹을 유지
  - `merge_final()`이 중복군을 재생성한 뒤, 모바일에서 들고 온 `note`, `representative_mode`, `representative_id`, `apply_globally`, 멤버 메타(created/updated/priority/raw_match/field_match)를 다시 덮어써 exact 보존

검증:
- `python3 -m compileall core/database/models.py core/database/database.py services/db_backup.py` 통과
- `./venv/bin/python` 임시 모바일 DB → 서버 복원 검증:
  - title/detail/entry_value/raw_content/synced_at 보존 확인
  - `sync_meta(last_sync/watchlist/custom_key)` 보존 확인
  - `duplicate_group(note/apply_globally/manual representative)` 보존 확인
  - `duplicate_member(priority_score/raw_match/field_match/created_at/updated_at)` 보존 확인

비고:
- 서버 전용 `admin_users`, `api_keys`는 모바일 DB에 대응 스키마가 없으므로 모바일 복원 시에도 기존 서버 값을 유지한다.
- 이번 검증 기준으로 일반 신고 row, raw payload, sync meta, 중복군/중복멤버 메타는 서버-모바일 round-trip에서 더 이상 빠지지 않는다.

### API 큐 크롤링 목록 재탐색 누락 수정

상태: 완료

변경:
- `start.py`
  - 큐 신고번호가 DB에 없을 때 `목록 크롤링 후 재검색` 로그만 남기고 실제 탐색을 건너뛰던 조건 수정
  - API direct-login 모드처럼 Selenium driver를 만들지 않는 정상 경로에서도 목록 1~100페이지 재탐색이 실제로 수행되도록 변경
  - 재탐색 분기에서 사용하던 `select` import 누락도 함께 보강

### 데이터 수정 목록 표형 유지 + 수정 버튼 줄바꿈 방지

상태: 완료

변경:
- `web/templates/db_editor.html`
  - 데이터 수정 목록을 표 형식으로 유지
  - `수정` 열 폭과 버튼 `nowrap`을 조정해 버튼 텍스트가 두 줄로 꺾이지 않도록 보정

## 2026-05-06

### 데이터 수정 서비스 분리 + 카드형 목록 정리

상태: 완료

변경:
- `services/db_editor_service.py`
  - 데이터 수정 스키마/조회/저장 로직을 서비스 계층으로 분리
- `web/routers/db_editor_route.py`
  - DB 수정 화면이 서비스 계층을 사용하도록 정리
- `web/templates/db_editor.html`
  - 데이터 수정 목록을 `신고번호 DESC` 카드형 리스트로 재구성
- `web/templates/db_editor_form.html`
  - `범칙금_과태료` 예시 문구를 서버 스키마에서 내려받아 표시하도록 정리
- `web/routers/api_route.py`
  - `/api/v1/editor/schema`, `/api/v1/editor/{category}/{record_id}` 조회/저장 API 추가

### 크롬 익스텐션용 중복 신고 완료 알림 연결

상태: 완료

변경:
- `services/crawl_state_store.py`
  - `crawl_done_ext.json`에 중복 신고 변경 요약을 더 자세히 저장하도록 확장
  - `duplicate_change_type`, `body`, `representative_mode_label`을 함께 기록
- `web/routers/api_route.py`
  - `GET /api/v1/crawl/done/ext` 응답에 `duplicate_changed_count` 포함

검증:
- `python3 -m compileall services/crawl_state_store.py web/routers/api_route.py` 통과

비고:
- 이 변경은 크롬 익스텐션이 크롤링 완료 후 일반 신고 변경과 중복 신고 변경을 구분해 알림 문구를 만들 수 있도록 하기 위한 서버 계약 보강이다.

### 대표건/원본 기준 토글 설정 페이지 전역화

상태: 완료

변경:
- `settings/settings.py`
  - `SETTINGS.use_representative_records` 설정값 추가
  - 기본값은 `True`로 두어 기존 기본 집계가 대표건 기준으로 유지되도록 설정
- `web/routers/settings_route.py`, `web/templates/settings.html`
  - 설정 페이지 `기타 데이터 필터 세팅`에 `중복 신고 대표건만 반영` 스위치 추가
  - `취하 데이터 숨기기` 바로 아래에 배치하고, 저장 시 `config.ini`에 전역 반영되도록 연결
- `web/routers/dashboard.py`, `web/routers/data.py`, `web/routers/stats.py`, `web/routers/api_route.py`
  - 대시보드, 전체 신고 조회, 차량/주소 검색, 통계, 모바일 API 기본 dedupe 모드가 전역 설정을 따르도록 변경
  - 설정이 켜져 있으면 `canonical`, 꺼져 있으면 `raw`를 기본값으로 사용
  - 명시적인 `dedupe` 쿼리 파라미터가 들어오면 그 값은 그대로 허용
- `web/templates/data_table.html`, `web/templates/stats.html`
  - 페이지별 `대표건 기준 / 원본 기준` 전환 버튼 제거
  - 기본 집계 기준은 설정 페이지에서만 바꾸도록 단일화

검증:
- `python3 -m compileall settings/settings.py web/routers/settings_route.py web/routers/data.py web/routers/stats.py web/routers/dashboard.py web/routers/api_route.py` 통과
- `./venv/bin/python` import 스모크 테스트:
  - `use_representative_records=True`일 때 data/stats/dashboard/api 기본 모드가 모두 `canonical`으로 해석됨을 확인

비고:
- 중복 신고 관리 화면의 그룹별 `대표건 기준 전역 반영` 스위치는 그대로 유지된다.
  이 스위치는 특정 중복군을 canonical 집계에 포함할지 여부를 정하는 역할이고,
  이번 설정은 "앱 전체가 canonical 기준으로 보일지 raw 기준으로 보일지"의 기본값을 정하는 전역 스위치다.

### 중복 신고 관리 2차 정리 / 상태 축 분리 / 동영상 프록시 추가

상태: 완료

변경:
- `services/duplicate_group_service.py`
  - 중복 상태를 `review_required`, `confirmed_duplicate`, `not_duplicate`로 재정의
  - 대표건 선택 방식을 `auto`, `manual`로 분리
  - `auto` 모드에서는 중복군 재생성 때마다 최신 `처분/처리상태/답변일/synced_at/신고번호` 우선순위로 대표건 자동 재선정
  - `manual` 모드에서는 사용자가 고른 대표건을 유지
  - `not_duplicate`는 중복 신고 관리 화면에서는 보이지만 canonical projection에서는 일반 원본 신고처럼 모두 반영되도록 조정
  - 그룹 일괄 상태 변경 API 추가
- `core/database/models.py`
  - `mysafety_duplicate_group`에 `representative_mode` 컬럼 추가
- `web/routers/duplicate_route.py`
  - 중복 상태 필터를 `duplicate_status` 기준으로 단순화
  - 선택 그룹 일괄 상태 변경 POST 경로 추가
- `web/templates/duplicate_groups.html`
  - 상단 상태 카드를 `전체 / 검토 필요 / 중복 확정 / 중복 아님` 필터로 재구성
  - 그룹 좌측 체크박스 + 일괄 상태 변경 UI 추가
  - 일괄 처리 바를 `상태 변경` + `대표건 선정` 2개 드롭다운 구조로 정리
  - 대표건 선정도 `자동 선정 / 수동 선정`을 그룹 단위로 일괄 변경 가능하게 확장
  - 그룹별 설정에서 `중복 상태`와 `대표건 선정(자동/수동)`을 분리
  - 개별 그룹 저장 시 `자동 선정` 상태라도 사용자가 다른 child를 대표건으로 골랐다면 저장 시 자동으로 `수동 고정`으로 전환되도록 보정
  - child 행은 `신고번호 DESC` 순으로 표시
  - `ID` 컬럼은 안전신문고 직접 이동 링크, `신고번호`는 상세 모달 링크로 정리
  - child 표의 `카테고리` 컬럼을 `신고메뉴`로 바꾸고 `entry_value`를 표시하도록 변경
  - `차량번호`, `담당자` 컬럼을 각 `/data/<category>` 검색 링크로 연결
  - 일괄 상태 변경 툴바가 줄바꿈되지 않도록 레이아웃 여백 조정
- `services/media_proxy_service.py`, `web/routers/media_route.py`
  - 원격 첨부 동영상을 서버가 실시간 스트리밍 프록시하는 `/media/proxy` 경로로 변경
  - 더 이상 전체 파일을 먼저 캐시한 뒤 응답하지 않도록 수정
- `web/templates/base.html`, `web/templates/data_table.html`
  - 상세 모달/첨부 모달의 `<video>`가 원격 원본 URL 대신 `/media/proxy`를 우선 사용하도록 변경
  - 프록시 실패 시 원본 URL로 fallback
  - `<video preload>`를 `none`으로 낮춰 모달을 여는 순간 여러 동영상이 동시에 선로딩되지 않도록 조정
- `services/crawl_log_service.py`, `services/crawl_control.py`, `services/crawl_manager.py`
  - `rotate_crawl_log()` 공용 모듈 분리로 `crawl_control <-> crawl_manager` 순환 import 제거
- `web/templates/base.html`
  - 사이드바의 `중복 신고 관리` 메뉴를 `감시 목록 관리` 바로 아래로 이동

검증:
- `python3 -m compileall core services web main.py` 통과
- `./venv/bin/python` 로컬 HTTP 서버 스모크 테스트:
  - `/media/proxy`가 업스트림 응답을 스트리밍 형태로 열고 전체 파일 선다운로드를 하지 않음을 확인
- `./venv/bin/python` 인메모리 SQLite 스모크 테스트:
  - 자동 대표건이 초기에는 `과태료/수용` 건으로 선택됨
  - 수동 고정 후 재생성해도 대표건 유지 확인
  - 자동 모드로 되돌린 뒤 데이터 우선순위가 바뀌면 대표건이 다시 자동 전환됨
  - `not_duplicate` 상태에서는 canonical 조회가 child 전부를 다시 노출함
  - child 목록이 `신고번호 DESC`로 정렬되고 `entry_value`가 노출됨을 확인
- `duplicate_route`, `media_route` import 확인

비고:
- 현재 `대표건 기준 전역 반영`은 `confirmed_duplicate` 또는 `review_required` 그룹에서만 의미가 있다.
  `not_duplicate`는 canonical에서도 원본 행을 모두 보여주는 방향으로 해석한다.
- 동영상 seek 문제는 원격 첨부 서버의 range/streaming 호환성 이슈 가능성을 고려해 서버 프록시로 우회했다.

### 중복 신고 관리 1차 구현 / 대표건 canonical 집계 도입

상태: 완료

변경:
- `core/database/models.py`
  - `mysafety_duplicate_group`, `mysafety_duplicate_member` 테이블 추가
- `services/duplicate_group_service.py`
  - `mysafety_raw_content` 기반 payload exact 중복군 재생성
  - 대표건 자동 추천
  - raw/canonical projection 공용 로직
  - 중복군 상태/대표건/전역 반영 여부 업데이트 로직
- `core/database/database.py`
  - `upgrade_schema()`와 `merge_final()` 후 payload exact 중복군 자동 재생성 연결
- `services/report_query_service.py`
  - 전체 조회, 카테고리 조회, 차량/주소 검색, 중복차량 조회에 `mode=raw|canonical` 지원 추가
- `services/report_stats_service.py`
  - 대시보드와 부서 통계에 `mode=raw|canonical` 지원 추가
- `web/routers/data.py`, `web/routers/api_route.py`, `web/routers/dashboard.py`, `web/routers/stats.py`
  - 웹/API 조회 경로에 `dedupe` 모드 연결
  - 대시보드와 통계는 대표건 기준을 기본값으로 사용
- `web/routers/duplicate_route.py`, `web/templates/duplicate_groups.html`
  - 중복 신고 관리 메뉴 추가
  - 중복군 재생성, 대표건 변경, 상태 변경, 전역 반영 on/off UI 추가
- `web/templates/data_table.html`, `web/templates/stats.html`, `web/templates/base.html`
  - raw/canonical 모드 전환 버튼 및 네비게이션 추가
- `.gitignore`
  - `README.md`, `CHANGELOG.md`, `CLAUDE.md`를 제외한 기타 `md` 파일은 로컬 보관용으로 되돌림
  - `IMPLEMENTATION_PLAN.md`, `SYNC_ROUNDTRIP_PLAN.md`는 git 추적 해제

검증:
- `python3 -m compileall core services web main.py` 통과
- 인메모리 SQLite 샘플 기준 검증:
  - payload identical 2건이 1개 중복군으로 생성됨
  - 대표건이 `과태료/수용` row로 선택됨
  - canonical projection 결과 ID `1,3`만 남음
  - canonical 대시보드 total/processing/accept 집계가 기대값으로 계산됨
  - canonical 기관 통계 total이 1건으로 축소됨
- `node --check` 기반 템플릿 인라인 스크립트 문법 검사:
  - `base.html`, `data_table.html`, `stats.html` 통과

비고:
- 1차 구현은 `mysafety_raw_content`가 있는 payload exact 그룹만 자동 확정 대상으로 삼는다.
- `raw_content`가 없는 데이터의 review 후보 그룹화는 후속 단계다.

### 기존 서버 DB synced_at 백필 / 웹 상세·첨부 렌더 방어

상태: 완료

변경:
- `core/database/database.py`
  - `upgrade_schema()`가 스키마 업그레이드 뒤 `backfill_synced_at()`를 자동 실행하도록 변경
  - 기존 서버 DB에서 `mysafetydetail_*`.`synced_at`가 비어 있으면 `답변일` 우선, 없으면 `신고일` 기준으로 epoch ms를 백필
  - 백필이 발생한 경우 `merge_final()`을 다시 돌려 `mysafetymerge_*`에도 `synced_at`를 즉시 전파
- `services/db_backup.py`
  - `/backup/upload`로 서버 형식 DB를 복원할 때도 복사 직후 `upgrade_schema()`를 실행해, 앱 재시작 없이 `synced_at`/`mysafety_raw_content` 마이그레이션과 백필이 적용되도록 수정
- `web/templates/base.html`
  - 신고 상세 모달이 `지도/첨부사진/첨부파일` 등을 문자열로 가정하다가 죽지 않도록 값 정규화 계층 추가
  - 배열/객체/빈값/예상 밖 타입이 와도 문자열화 또는 안전한 빈값 처리로 떨어지게 수정
  - 상세 렌더 중 예외가 나면 전체 페이지를 멈추지 않고, 경고 메시지와 핵심 필드만 보여주는 fallback 모달로 복구
- `web/templates/data_table.html`
  - 목록 렌더, 첨부 일괄확인 모달, CSV 내보내기에서 첨부 필드를 모두 공용 정규화 함수로 처리
  - `data-links`를 단순 콤마 join 대신 JSON payload로 바꿔 URL 값과 타입 차이에 더 강하게 수정

검증:
- `python3 -m compileall core services web main.py start.py` 통과
- `./venv/bin/python` + 임시 복사본 `data-example.db` 기준 검증:
  - `mysafetydetail_*` / `mysafetymerge_*`에 `synced_at` 컬럼 자동 추가 확인
  - 백필 결과 `traffic 2665`, `parking 35`, `other 22`건 채움
  - 백필 후 detail/merge 6개 테이블 모두 `synced_at NULL 0건` 확인
- `node --check` 기반 템플릿 인라인 스크립트 문법 검사:
  - `base.html` 통과
  - `data_table.html`은 Jinja 값 치환 후 통과

비고:
- 이번 백필은 기존 데이터의 실제 동기화 시각을 복원하는 작업이 아니라, 최근 답변 정렬을 깨지 않기 위한 초기 기준 시각을 `답변일/신고일`에서 안전하게 추정해 채우는 일회성 호환 작업이다.

### 서버-모바일 round-trip 메타데이터 보존 / 최근 답변 synced_at 정렬

상태: 완료

변경:
- `SYNC_ROUNDTRIP_PLAN.md`
  - `synced_at`, `entry_value`, `raw_content` round-trip 보존 계획과 실행 순서를 전용 문서로 정리
- `core/database/models.py`
  - `mysafetydetail_*`, `mysafetymerge_*` 에 `synced_at INTEGER` 추가
  - 원본 payload 보존용 `mysafety_raw_content` 테이블 추가
- `core/database/database.py`
  - detail upsert 시 `신규/실제 변경`일 때만 `synced_at` 를 현재 시각으로 갱신
  - 동일 내용 재크롤은 기존 `synced_at` 를 유지
  - `merge_final()` 이 detail 의 `synced_at` 를 merge 로 전파하도록 수정
- `services/report_stats_service.py`
  - 최근 3일 답변 목록을 `synced_at DESC` 기준으로 정렬
  - `synced_at` 가 없는 과거 데이터는 `답변일 DESC`, `신고번호 DESC` fallback 정렬
- `services/db_backup.py`
  - 모바일 DB 복원 시 `reports.synced_at` 를 detail/merge 로 보존
  - `report_raw` 또는 `reports.raw_content` 의 payload 를 `mysafety_raw_content` 로 보존
  - `entry_value` 와 함께 raw payload 테이블도 복원 시 초기화/재적재

검증:
- `python3 -m compileall core services web start.py main.py` 통과
- 임시 SQLite DB 기준으로 `detail_to_sql()` 검증:
  - 신규 insert 시 `synced_at` 생성
  - 동일 내용 재크롤 시 `synced_at` 유지
  - 실제 변경 재크롤 시 `synced_at` 갱신
  - `merge_*` 로 `synced_at` 전파 확인

비고:
- 모바일 import/export 대응과 `report_raw` 도입은 `safetyreport-mobile` 레포의 동일자 CHANGELOG 참고
- 이번 변경으로 서버 DB 는 모바일의 `entry_value`/`raw_content`/`synced_at` 를 이전보다 훨씬 덜 잃어버리게 됨

### 만족도 재분류 무결성 보강 (조회 실패 vs 실제 미참여 구분)

상태: 완료

변경:
- `services/satisfaction_fetcher.py`
  - 만족도 조회 결과를 `SatisfactionLookupResult(score, cause, confirmed)` 로 표준화
  - 네트워크/HTTP/DOM 조회 실패는 `confirmed=False` 로 구분하고, 실제 조회 성공 후 점수 없음인 경우만 `confirmed=True` 로 처리
  - Selenium 팝업 경로는 `STSFDG_SCORE`/`STSFDG_CAUSE` 마커가 보이는데 체크 점수만 없는 경우를 미참여로 간주
- `core/crawler/detail_pipeline.py`
  - `만족도조사여부 == "참여 완료"` 건 보강 시, 조회 실패면 기존 상태를 유지하고
    확정 미참여일 때만 `참여 가능` 으로 다운그레이드하도록 수정

검증:
- 리팩토링 직전(`e2d0f1e^`)의 `services/parser.py` 와 현재 `parse_json_details()` 를
  저장된 API raw fixture(`testresults/*_api_raw.json`) 기준으로 대조했고 결과가 모두 동일함을 확인
- `detail_pipeline` 의 만족도 분기(점수 있음 / 확정 미참여 / 조회 실패 유지)는 스텁 테스트로 확인

비고:
- 이번 수정은 리팩토링 이전부터 있던 잠재 리스크를 줄이는 안전장치다.
  초기 DB 재구축 시 만족도 조회 일시 실패가 `참여 완료 -> 참여 가능` 오분류로 이어지는 경로를 막는다.

### 공용 서비스 계층 분리 / 크롤러 공통 파이프라인 정리 / 모바일 Client 계약 유지 리팩토링

상태: 완료

변경:
- `core/database/engine.py`
  - 라우터/서비스 전반이 공용 `get_engine()` 경로를 사용하도록 정리
- `core/utils/retry.py`
  - API/크롤링 공용 재시도 설정 헬퍼 추가
- `core/crawler/api_client.py`
  - direct login 세션과 Selenium 브라우저 fallback 컨텍스트 생성을 공용화
- `core/crawler/title_pipeline.py`, `core/crawler/detail_pipeline.py`
  - API/legacy 크롤러가 같은 목록/상세 정규화 파이프라인을 타도록 정리
- `services/crawl_control.py`
  - 크롤링 시작/중지/재개, 큐 파일 생성, 로그 헤더/회전 처리 로직을 공용화
- `services/crawl_state_store.py`
  - `crawl_done.json`, `crawl_done_ext.json`, `crawl_changes.json` 읽기/쓰기 로직 집중
- `services/export_service.py`, `services/file_service.py`, `services/rating_service.py`
  - export, 파일 브라우저, 모바일/웹 별점 batch 시작 흐름을 공용 서비스로 분리
- `services/report_query_service.py`, `services/report_stats_service.py`
  - 조회/검색과 대시보드/통계를 분리
  - 기존 `services/data_service.py` 는 import 호환용 facade 로 축소
- `core/database/database.py`
  - `get_pending_detail_ids`, `detail_to_sql`, `normalize_police_agency` 공개 함수 추가
  - 기존 `get_cNo`, `deatil_to_sql` 는 호환 alias 로 유지
- `web/routers/*.py`
  - 모바일 API, 웹 크롤링, 파일 브라우저, 별점 라우터가 공용 서비스 계층을 사용하도록 연결
- `services/report_stats_service.py`
  - 통계 조회에서 필요한 컬럼만 읽고, 연도/날짜/시간/경찰 포함 여부/단순 텍스트 일부를 SQL 단계로 먼저 내리도록 개선
- `IMPLEMENTATION_PLAN.md`
  - 서버/모바일 릴리즈 체크리스트와 배포 순서/롤백 기준 추가

비고:
- 모바일 앱 쪽 Client 계약 상수화 작업은 `safetyreport-mobile` 레포의 동일자 CHANGELOG 참고
- 이번 변경은 API 경로/이벤트 이름은 유지하고, 중복 구현만 공용 계층으로 모으는 데 초점을 둠

---

## 2026-05-05

### 모바일 대시보드 더보기 / 상세 모달 필드 링크 (모바일과 함께 작업)

상태: 완료

변경:
- `services/data_service.py`
  - `get_dashboard_stats` 의 `recent_answers` 한도를 20 → 200 으로 상향. 대시보드에서
    가려졌던 항목이 모바일 더보기 페이지에서 모두 보이도록 함
  - `recent_answers` / `watchlist` 행 dict 에 `category` 필드 추가 (`traffic`/`parking`/`other`)
    — 모달 상세에서 카테고리별 필터 링크 생성 시 사용
  - `get_duplicate_records`, `get_all_watchlist` 도 카테고리 라벨 부여
- `web/templates/base.html`
  - `linkField()` 헬퍼 추가: 차량번호 / 위반장소 / 위반법규 / 담당자 4개 필드를
    `/data/<category>?car|law|location|person=<value>` 링크로 렌더링
  - 행 데이터의 `category` 필드를 우선, 없으면 현재 URL `/data/<cat>` 로 추정 (data_table 페이지 호환)
- `web/templates/data_table.html`
  - URL 쿼리 `car` / `law` / `location` 파라미터를 받아 상세 검색 입력에 자동 채우고
    `qAgency || qPerson || qCar || qLaw || qLocation` 일 때 자동 검색 실행

비고:
- 모바일 측 변경: `safetyreport-mobile` 의 동일자 CHANGELOG 참고
- 추가된 `recent_answers[:200]` 한도는 통상적인 3일 답변 수보다 충분히 큰 안전 캡

### 카테고리 전파 후속 보강 (모바일/웹 상세 링크 원탭 복귀)

상태: 완료

변경:
- `services/data_service.py`
  - `_get_records_from_table(..., category=...)` 인자를 추가하고
    `get_traffic_records`, `get_parking_records`, `get_other_records` 에서
    각 행의 `category` 를 기본 주입하도록 정리
  - `get_all_records` 가 raw table 조회 대신 카테고리별 getter 를 합치는 방식으로 변경
  - `search_by_vehicle`, `search_by_address`, `get_unrated_records` 결과에도
    각 행의 `category` 를 유지하도록 보강

비고:
- 초기 변경은 대시보드/감시목록/중복차량 중심이었고, 이번 후속 수정으로
  합본 목록/차량검색/주소검색/별점 대상 목록까지 category 보존 범위를 맞췄다.
- 이 필드가 빠지면 웹 상세 모달 링크는 `/data/all` 로, 모바일 상세 링크는 기본 탭으로
  흐를 수 있어 원래 신고 카테고리 복귀가 깨진다.

---

## 2026-05-02

### 상세검색 만족도 조사 여부 드롭다운 추가

상태: 완료

변경:
- `web/templates/data_table.html`
  - 상세검색 사이드바의 별점사유 뒤에 `만족도 조사 여부` 단일선택 `<select>` 드롭다운 추가
  - 옵션: 전체 / 참여 완료 / 참여 가능
  - JS DataTables 커스텀 필터에 `만족도조사여부` 필드 매칭 로직 추가
- `CLAUDE.md`
  - 웹 상세검색 문법 섹션에 만족도 조사 여부 드롭다운 설명 추가

---

## 2026-05-01

### 모바일 Client 별점 API 추가 (302 로그인 리다이렉트 대응)

상태: 완료

변경:
- `web/routers/api_route.py`
  - `POST /api/v1/rating/start` 추가
  - 모바일 Client가 관리자 세션이 필요한 웹 `/rating/start` 대신 API 키 기반 경로로 별점 배치 작업을 시작할 수 있게 수정
  - 내부적으로는 기존 `data_service.resolve_ids_for_rating()`와 `star_rating_service.run_batch_rating()`를 재사용하고 `current_rating.log` 회전도 동일 규칙으로 맞춤

### 상세검색 AND/OR + 다중선택 확장

상태: 완료

변경:
- `web/templates/data_table.html`
  - 상세검색 상단에 `&` = AND, `,` = OR 안내 문구 추가
  - `차량번호`, `신고번호`, `신고명`, `위반법규`, `담당자`, `위반장소`, `처리기관`, `범칙금_과태료`, `별점사유`, `신고내용`, `처리내용`을 DataTables 커스텀 필터 기반 AND/OR 검색으로 전환
  - `처리상태`를 현재 DB 레코드에서 뽑은 distinct 값 기반 다중선택 드롭다운으로 변경
  - `별점`을 `없음`, `1~5점` 다중선택 드롭다운으로 변경
  - 두 드롭다운의 선택 항목 우측에 초록 `v` 표시
- `services/data_service.py`
  - `_parse_and_or_groups()`, `_matches_and_or_text()`, `_apply_text_query()` 추가
  - 통계 상세검색의 `처리기관`, `신고명`, `위반장소`에 같은 `&` / `,` 문법 적용
  - `agencyExact`는 단일어 입력일 때만 exact match를 사용하고, 구분자 포함 시 AND/OR 부분검색 규칙 우선 적용
- `web/templates/stats.html`
  - 통계 상세검색 상단에 같은 검색 문법 안내 추가
  - `agencyExact` 설명을 단일어 exact 기준으로 정리

### 레거시 크롤러 응답 선택/페이지 안정화

상태: 완료

변경:
- `core/crawler/crawldetail.py`
  - 레거시 상세 페이지에 `처리결과` 테이블이 여러 개 있을 때 마지막 테이블을 최신 답변으로 선택
  - `testresults/57250864_legacy_raw.html` 기준으로 `불수용`이 아닌 `수용 / 과태료 50,000원` 응답을 읽도록 수정
- `core/crawler/crawltitle.py`
  - 페이지 이동 후 `tbody` 교체를 기다리도록 보강
  - `stale element reference` 발생 시 같은 페이지 스크래핑을 최대 3회 재시도하도록 수정

### 행정구역별 Top5 대시보드 2중 카테고리 개편

상태: 완료

구성:
- `services/sunwi_fetcher.py`
  - `CATEGORY_GROUPS` 기반 대분류/소분류 구조 도입
  - 대분류
    - `불법주정차신고`
    - `자동차·교통위반`
  - 각 소분류별 전국 Top5 산출
  - CSV 컬럼을 `대분류`, `소분류` 중심 구조로 확장
- `services/sunwi_service.py`
  - 대분류/소분류 중첩 구조를 캐시에 저장하도록 변경
  - 다운로드 파일명을 `sunwi_category_top5_latest.csv`로 유지하면서 새 컬럼 구조 반영
- `web/routers/dashboard.py`
  - `/sunwi/download/top5` 경로 유지
- `web/templates/index.html`
  - 대시보드 우측 카드에 대분류/소분류 화살표를 각각 배치
  - 부모 변경 시 소분류 인덱스 초기화
  - 5초 자동 전환은 소분류 기준으로 순환하고, 끝에 도달하면 다음 대분류로 이동
  - 카드 본문은 선택된 소분류의 Top5 행정구역을 세로 스택으로 표시

### 설정 기본값 조정

상태: 완료

변경:
- `settings/settings.py`
  - `max_retry_attemps` 기본 fallback 값을 `3`에서 `5`로 변경
- `web/routers/settings_route.py`
  - 설정 페이지 표시 기본값과 폼 기본값도 `5`로 동기화

### macOS quarantine 우회 보강

상태: 완료

변경:
- `scripts/build/build_exe.py`
  - 생성되는 `run.command`가 실행되면 `run.command`, `mysafetyreport`, `_internal`에 대해 `xattr -dr com.apple.quarantine`를 시도하도록 추가
  - 첫 실행 전 Gatekeeper 차단 자체를 없애지는 못하지만, 사용자가 `run.command`를 한 번 허용한 뒤 내부 `.so` 연쇄 차단을 줄이는 목적
- `core/utils/updater.py`
  - macOS 자동 업데이트 스크립트가 파일 복사 직후 같은 대상들에 대해 quarantine 재귀 해제를 시도하도록 추가
  - 인앱 업데이트 후 다시 `grp.cpython-314-darwin.so`, `zstd.cpython-314-darwin.so`류가 차단되는 가능성을 낮추는 용도

### 수동 테스트 워크플로 플랫폼별 분리

상태: 완료

변경:
- 기존 `build-macos-manual.yml` 제거
- `workflow_dispatch` 전용 테스트 워크플로를 플랫폼별 4개로 분리
  - `build-windows-manual.yml`
  - `build-linux-manual.yml`
  - `build-macos-x64-manual.yml`
  - `build-macos-arm64-manual.yml`
- 각 워크플로는 `build.yml`의 해당 플랫폼 빌드 job과 동일한 러너/설치 흐름을 사용하고, 태그 체크/릴리즈 생성 없이 아티팩트 업로드까지만 수행

---

## 2026-04-30

### 문서 정리
- `CLAUDE.md`는 구조/운영 메모 중심으로 정리하고, 작업/버그/세션 이력은 `CHANGELOG.md`로 분리
- 이 세션 기준으로 최근 macOS 빌드/크롤러 로그인/업데이트 체크 수정 내역을 함께 정리

### 행정구역별 Top3 대시보드 추가

상태: 작업 트리 반영

구성:
- `services/sunwi_fetcher.py`로 통계 수집 코어를 배치
  - 대상 월을 현재 `YYYYMM` 기준으로 계산
  - 서버 부담 완화용 랜덤 sleep/휴식 로직 제거
  - 카테고리별 Top3 가공 함수와 CSV 저장 함수 분리
- `services/sunwi_service.py` 추가
  - 로그인 없이 안전신문고 통계 API 별도 호출
  - 서버 시작 후 즉시 1회 수집
  - 이후 3시간마다 재수집
  - `data/results/sunwi_category_all_latest.csv`, `sunwi_category_top3_latest.csv` 저장
  - 메모리 캐시를 통해 대시보드가 바로 읽을 수 있게 구성
- `web/routers/dashboard.py`
  - 대시보드 렌더 컨텍스트에 `sunwi` 데이터 추가
  - `/sunwi/download/top3` CSV 다운로드 엔드포인트 추가
- `web/templates/index.html`
  - 기존 두 통계 섹션을 좌측 컬럼으로 정리
  - 우측에 행정구역별 Top3 카드 추가
  - 카테고리 좌우 화살표, 내용 좌우 화살표, 5초 자동 전환, CSV 다운로드 링크 추가
- `main.py`
  - `sunwi_service.start_background_refresh()` / `stop_background_refresh()` 연결

### macOS 워크플로 통합 정리

상태: 작업 트리 반영

변경:
- `build.yml`
  - 기존 self-hosted `macOS x64` 릴리즈 빌드 유지
  - GitHub-hosted `macOS arm64` 릴리즈 빌드 추가
  - 릴리즈 생성 시 `mysafetyreport-macos-x64.zip`, `mysafetyreport-macos-arm64.zip` 두 파일을 모두 첨부
- 테스트 워크플로 정리
  - 기존 `build-macos-test.yml`, `build-macos-arm64-test.yml` 삭제
  - 이후 구조가 다시 바뀌어 플랫폼별 수동 워크플로 4개로 분리됨

### macOS 빌드/배포 정비

| 상태 | 커밋 | 내용 |
|------|------|------|
| 완료 | `5842333` | macOS portable build/test workflow 추가 |
| 완료 | `4b9fab5` | self-hosted macOS Universal 2 진단 스크립트 추가 |
| 완료 | `2603815` | macOS developer tools 사전 점검 추가 |
| 완료 | `631a77b` | self-hosted macOS 빌드를 Universal 2에서 x64 전용으로 전환 |
| 완료 | `dd58038` | `actions/setup-python` 대신 self-hosted macOS의 로컬 Python 사용 |
| 완료 | `5328eaf` | `build-macos-test.yml`을 `dev` 브랜치 기준으로 조정하고 테스트 산출물을 `/Users/better0101`으로 이동하도록 변경 |

세부 메모:
- `build-macos-test.yml`
  - `push.branches: [dev]`
  - 준비 job은 `[self-hosted, Linux, X64, 235]`
  - 테스트 zip은 artifact 업로드 대신 `/Users/better0101`으로 이동
- `build.yml`
  - macOS 릴리스 빌드는 self-hosted x64 기준으로 정리
  - 로컬 Python 경로와 Xcode developer dir를 self-hosted 러너에서 직접 점검

### 크롤러 로그인 흐름 정리

| 상태 | 커밋 | 내용 |
|------|------|------|
| 완료 | `0a8e76d` | macOS 환경에서 Selenium 로그인 fallback 검증/보강 |
| 완료 | `5328eaf` | `legacy` / `api` / 비회원 로그인 흐름 분리 정리 |
| 완료 | `0baa5b4` | `crawl_type` 값 `web`을 전역에서 `legacy`로 통일 |

세부 메모:
- `legacy`
  - `direct_login`이나 쿠키 주입을 타지 않음
  - 회원은 저장된 ID/PW로 Selenium UI 로그인
  - 비회원은 Chrome 창에서 사용자가 로그인 후 `재개` 신호를 기다림
- `api`
  - 먼저 `direct_login` 시도
  - 실패 시 Selenium으로 로그인한 뒤 브라우저 컨텍스트 `$.get` API fallback 사용
- 설정/라우터/UI 전반에서 `crawl_type=legacy`를 기준값으로 사용하도록 정리

### macOS 패키지 버전 체크/업데이트 체크 복구

상태: 작업 트리 반영

증상:
- macOS 패키지 앱 시작 시 `업데이트 확인 중... (서버 연결 실패, 건너뜀)`
- 웹 사이드바는 `/version/latest` 호출 실패로 `버전 확인 불가`
- Linux Docker 이미지는 같은 GitHub 최신 버전 체크가 정상 동작

원인 추정:
- PyInstaller로 패키징된 macOS 앱에서 `urllib` HTTPS 인증서 체인을 기본 경로로 못 찾는 경우가 있음
- 현재 릴리스 산출물명은 `mysafetyreport-macos-x64.zip`인데 업데이트 모듈은 예전 `macos-intel` 이름을 참조하던 흔적이 있었음

수정 파일:
- `core/utils/updater.py`
  - `certifi` 기반 SSL context 생성
  - GitHub API 조회와 zip 다운로드를 모두 `_urlopen()` 경유로 통일
  - macOS 릴리스 에셋명을 `mysafetyreport-macos-x64.zip` / `mysafetyreport-macos-arm64.zip` 기준으로 분기
  - 캐시 주석을 실제 동작(5분)과 일치하도록 정리
- `scripts/build/build_exe.py`
  - `--collect-data=certifi` 추가
- `requirements.txt`
  - `certifi>=2024.0.0` 명시 추가

검증:
- `python3 -m py_compile core/utils/updater.py scripts/build/build_exe.py`

---

## 2026-04-27

### 직접 로그인 + 카테고리별 엑셀/시트 + 55분 keep-alive

직접 로그인:
- `core/crawler/direct_login.py`
  - `curl_cffi`(Chrome impersonate) + RSA(PKCS1v15) + OAuth2 토큰 발급 기반 로그인 추가
  - 토큰을 `data/auth_token.json`에 디스크 캐시
  - `start_keepalive(interval=55*60)`로 55분 주기 재로그인
  - `main.py` lifespan startup에서 자동 시작
- API 크롤러
  - `crawltitle_api`, `crawldetail_api`가 Selenium driver 없이 `curl_cffi` 직접 호출
  - 401 응답 시 강제 토큰 갱신 후 1회 재시도
- `start.py`
  - API 모드에서는 Selenium driver 자체를 생성하지 않음
  - 당시 레거시 모드는 Selenium 로그인 대신 JSESSIONID/WMONID 쿠키 주입 흐름을 사용했음

카테고리별 엑셀/스프레드시트:
- `database.load_results_by_category()`로 카테고리 dict 반환
- `export.save_to_excel()`, `export.save_to_google_sheet()`에 dict 모드 추가
- 구글 시트는 기존 통합 `data` 시트를 삭제하고 `교통위반`, `주정차위반`, `기타위반` 시트 생성

기타:
- `/api/v1/settings/db` 추가
  - Standalone 모바일 앱의 DB 다운로드용
  - `X-API-Key` 또는 `?api_key=` 허용
- 모바일 별점/사유 동기화
  - `Report.rating`, `Report.ratingCause` 필드 추가
  - 로컬 DB 스키마 v3 → v4 (`별점`, `별점사유`)

### 만족도조사 별점/사유 자동 수집 + 통계 별점 평균 컬럼

수집 경로:
- detail 크롤링 직후 `만족도조사여부 == "참여 완료"` 건만 추가 조회
- API 방식
  - `/api/v1/portal/statistics/satisfactionstatistics/score/{spp}/{phone}` 호출
  - `STSFDG_SCORE`, `STSFDG_CAUSE` 사용
- 레거시 방식
  - 만족도 팝업 HTML 진입 후 `STSFDG_SCORE`, `STSFDG_CAUSE` 추출
- 공통 fetcher: `services/satisfaction_fetcher.py`

동작:
- `settings.phone_number`가 없으면 조회 스킵
- 점수 0/None이면 `만족도조사여부`를 `참여 가능`으로 다운그레이드
- `mysafety` 및 `mysafetymerge_*` 테이블에 `별점`, `별점사유` 컬럼 추가

통계:
- `stats.html` 모든 통계 테이블에 `별점 평균` 컬럼 추가
- 표시 형식: `★ X.XX (n)`
- 모바일 `/api/v1/stats`에도 `avg_rating`, `rating_count` 노출

---

## 2026-04-20

### WS 브로드캐스트 병렬화 및 크롤 완료 로직 공통화

문제:
- 다수 기기 연결 시 WS 브로드캐스트가 순차 전송이라 느린 기기 때문에 전체가 지연
- `broadcast_from_thread()`의 블로킹이 크롤링 완료 처리 스레드를 붙잡음
- `crawl.py`, `api_route.py`에 완료 후 처리 로직이 중복

수정:
- `services/ws_manager.py`
  - `broadcast()`를 `asyncio.gather` 기반 병렬 전송으로 변경
  - `broadcast_from_thread()`에서 `future.result(timeout=5)` 제거
- `services/crawl_manager.py`
  - `run_after_crawl(proc, log_file)` 추가
  - `launch_pending_crawl(pending)` 추가
  - `_rotate_log(log_file)` 내부 헬퍼 추가
- `web/routers/crawl.py`
  - 완료 후 처리 로직을 `crawl_manager.run_after_crawl()`로 위임
- `web/routers/api_route.py`
  - 동일하게 완료 후 처리 공통화

### 통계 4-way 확장

위반법규 필터:
- `data_service.py`의 `calc_stats()`에 `by_law` 통계 추가
- `stats.html`에서 카테고리별 위반법규 버튼을 동적으로 렌더링
- `law` 필터는 `?law=` 파라미터로 유지
- `없음` sentinel(`__없음__`) 지원

금액/합계:
- 기관별/담당자별/위반법규별 테이블에 `총 과태료` 컬럼 추가
- 교통위반 총 과태료 배너 표시
- 각 통계 테이블 `tfoot` 합계 행 추가

모바일 확장:
- `/api/v1/stats`에 `law` 파라미터 추가
- `totalFineAmount`, `availableLaws`, `hasEmptyLaw` 확장
- 위반법규 선택용 `DraggableScrollableSheet` 추가

데이터 페이지 연동:
- `data.py`의 traffic/parking/other 라우터에 `law` 필터 추가
- stats 행 클릭 시 해당 `law`를 붙여 데이터 상세로 이동

---

## 2026-04-18

### UI 개선

`data_table`:
- 체크박스 선택 행을 노란색으로 하이라이트
- `selectedRows` Set으로 페이지 전환 후에도 선택 복원
- `selectAll`, `btnReset`, ID 복사, 감시목록 추가 흐름을 Set 기반으로 통일

통계 페이지:
- 답변일 기준 연도별 탭 추가
- `?year=` 파라미터로 상태 유지
- `총 처리건수`와 `과태료` 사이에 `답변 소요 평균` 컬럼 추가
- 모바일 카드에도 `X.X일 / 평균 소요` 표시

---

## 2026-04-17

### detail 크롤링 시 title 필드 자동 갱신

목적:
- `mysafety`의 제목 계열 5개 필드를 detail 크롤링 단계에서 최신 값으로 동기화
- 보완 처리 후 변경된 차량번호, 발생일시, 장소, 신고명 등을 detail만으로 반영 가능하게 함

변경:
- `services/parser.py`
  - 보완 완료 판정 및 보완 의견 override 로직 추가
  - `parse_details(..., page_soup=None)` / `parse_json_details()`가 `title_fields` 반환
  - `신고일`은 날짜만 저장하도록 정리
  - `progress_status`를 반환 dict에서 제거하지 않도록 조정
- `core/crawler/crawldetail_api.py`, `crawldetail.py`
  - yield를 5-tuple `(df, category, entry_value, progress_status, title_fields)`로 확장
- `core/database/database.py`
  - `deatil_to_sql()`이 `title_fields`를 받아 `mysafety`를 직접 업데이트
  - 만족도조사여부는 `참여 완료` 상태를 함부로 다운그레이드하지 않도록 방어

보완 완료 판정:
- API: `SPLMNT_CMPTN_DT` 있고 `SPLMNT_CMPTN_YN != 'N'`
- 레거시: `splmntDivBody` 마지막 table에 `보완 완료 일시` 존재

### WebSocket keepalive ping timeout 수정

증상:
- 모바일 WS 연결이 주기적으로 `1011`로 끊기고 Cloudflare `502`가 발생

원인:
- uvicorn 기본 WS ping(20초)을 Cloudflare가 relay하지 않아 서버가 pong을 못 받음

수정:
- `main.py`의 `uvicorn.run()`에 `ws_ping_interval=None`, `ws_ping_timeout=None`
- 앱 레벨 30초 JSON ping(`_pinger` in `ws_route.py`) 사용
- `websockets` 로거를 ERROR 레벨로 낮춰 노이즈 억제

### 리팩토링 내역

적용 커밋:
- `e64b2ae` — `REFACTOR.md`의 5개 항목 적용

적용 요약:
- Task 1
  - `_C_NOW_STATUS` dict 상수 추출
  - `crawltitle_api.py`의 데드코드(`use_minimal_crawl`, `found_in_progress`, `empty_page_count`) 제거
- Task 2
  - API/레거시 파서 동작 통일
  - `full_text` 버그 제거
  - penalty 교정 헬퍼 `_apply_penalty_corrections()` 추출
- Task 3
  - `data_service.py`의 `_row_to_dict()` / `_REPORT_FIELDS` 추출
- Task 4
  - 경찰서명 정규화 유틸 중복 제거
- Task 5
  - `sync_rating_status()`의 반복 UPDATE를 루프화

### REFACTOR.md 원안 이관

Task 1 — `c_now` 상태 매핑 dict화:
- `services/parser.py`에 `_C_NOW_STATUS` 상수 정의
- `parse_json_details()`와 `crawltitle_api.py`가 같은 dict를 사용
- `crawltitle_api.py`에서 `use_minimal_crawl` 관련 데드코드 제거

Task 2 — `parser.py` 레거시/API 파서 동작 통일:
- API 파서 `full_text` 버그 제거
- 레거시 범칙금/과태료 정규식 유연화
- `violation_law` 검색 대상을 `processing_content`로 통일
- `processing_finish = "Y"` 조건에 `답변완료` 포함
- reject/warning/미확인 교정을 공통 헬퍼로 추출

Task 3 — `data_service.py` row → dict 헬퍼 추출:
- `recent_answers`, `watchlist_items`의 중복 dict 생성 로직을 `_row_to_dict()`로 통일
- `결과 = 처리상태` alias를 헬퍼 내부로 이동

Task 4 — `normalize_police` 유틸 중복 제거:
- 경찰서명 정규화 함수를 공용 유틸로 승격
- 파일 간 중복 정의 제거와 순환 참조 위험 검토

Task 5 — `database.py` `sync_rating_status` 루프화:
- `title_table`, `merge_traffic_table`, `merge_parking_table`, `merge_other_table`에 대한 동일 UPDATE를 루프로 통합

## 2026-05-02

### 문서 역할 분리 정리

변경:
- `CLAUDE.md`
  - 작업 이력성 메모를 제거하고 구조/작동 방식/운영 메모만 남기도록 정리
  - 작업/버그/세션 이력은 `CHANGELOG.md`에만 남긴다는 규칙을 명확히 함
- `CHANGELOG.md`
  - 최근 `CLAUDE.md`에 남아 있던 변경 이력 요약은 이 파일의 날짜별 항목으로 관리하도록 정리
  - 2026-05-02 구간에 섞여 있던 stray task/checklist 메모를 제거해 changelog 형식으로 정돈

### sunwi 모바일 API + 대시보드 정리

변경:
- `/api/v1/sunwi/payload` 추가: 모바일 Client `신고현황` 탭에서 서버 sunwi 캐시를 그대로 표시할 수 있도록 payload 노출.
- `/api/v1/sunwi/export/{kind}` 추가 (`all`, `top5`): 서버 결과 경로에 CSV 생성을 보장하고 생성 경로를 반환.
- `services/sunwi_service.py` 캐시에 `all_rows`, `top5_rows`를 함께 보존하고, 필요 시 현재 캐시 기준으로 CSV를 다시 저장하는 `ensure_csv()` 추가.
- 웹 라우터에 `/sunwi/download/all` 추가.
- 대시보드의 sunwi 카드 폭을 기존 대비 절반 수준으로 축소 (`col-xl-4` → `col-xl-2`, 본문 `col-xl-8` → `col-xl-10`).
- sunwi 카드 항목에서 행정구역 중복 표기(`시도 · 시군구` 보조 텍스트) 제거.
- 대시보드 두 막대그래프는 라벨 텍스트 없이 퍼센트 숫자만 표시하도록 수정.

---

## 2026-04-16

### 자동 업데이트 기능

`core/utils/updater.py` 추가. 프로그램 시작 시 GitHub Releases API로 버전 확인.

환경별 동작:
- Docker (`/.dockerenv`): `docker pull ghcr.io/fentanest/safetyreport:latest` 안내만 출력
- 컴파일 바이너리 (`is_frozen=True`): 대화형 프롬프트 후 다운로드 → 교체 → 재시작
  - Linux: `os.replace` + `os.execv`
  - Windows: `_update.bat` 생성 후 현재 프로세스 종료, bat이 교체 후 재시작
- 개발 환경 (`is_frozen=False`): `git pull` 안내만 출력

당시 릴리스 에셋 규칙:
```text
mysafetyreport-win.zip
mysafetyreport-linux.zip
```

---

## 주요 버그 이력

| 버그 | 원인 | 수정 |
|------|------|------|
| 모바일 Client 파일 브라우저에서 `current_crawl.log` 다운로드 무반응/서버 로그 에러 | 라이브 로그 파일을 그대로 `FileResponse`로 전송하면 전송 도중 파일 길이가 바뀔 수 있음 | `/api/v1/files/download` 가 `current_crawl.log` / `current_rating.log` 요청 시 임시 스냅샷(`copy2`)을 만든 뒤 그 파일을 전송하고 응답 후 삭제 |
| FilteredListScreen parking 카테고리 미처리 | `_getReports()`의 else 분기가 `'parking'`을 traffic+other 합산으로 반환, `initState()`에서 parking prefetch 누락 | `'parking'` 분기 추가, 카테고리별 개별 prefetch로 변경, all 분기에 parkingReports 포함 |
| stats 페이지 500 에러 | Jinja2 `agency_rows` 매크로가 parking 탭에서 사용되는데 정의는 아래쪽에 있어 `UndefinedError` 발생 | 매크로 및 `{% set %}` 블록을 `tab-content` 최상단으로 이동 |
| 엑셀/시트 처리기관 경찰서 단축 | `database.py` `load_results()`의 정규화 로직이 마지막 단어만 남김 | `data_service.py`와 동일하게 전체 경찰서명 유지 방식으로 변경 |
| WS 이벤트 미전달 | 배경 스레드에서 `new_event_loop()` 사용 → 메인 루프 WebSocket 접근 불가 | `broadcast_from_thread()` + `run_coroutine_threadsafe()` |
| 취하 민원 과태료 오파싱 | 취하 확정 전 처리상태 기준으로 과태료 설정 | 취하 확정 시 penalty 초기화 |
| `/api/v1/` auth 차단 | `auth_middleware`가 세션 없는 API 요청을 302 리다이렉트 | `_PUBLIC_PREFIXES`에 `/api/v1/` 추가 |
| 모바일 `/api/v1/reports/traffic` 500 | pandas `NaN`이 JSON 직렬화 실패 유발 | `df.fillna('')` 후 `to_dict()` |
| 다중 선택 크롤링 1건만 전송 | 건별 `enqueue` 호출이 두 번째부터 busy 반환 | `queue_list` 기반 일괄 전송 추가 |
| WS 클라이언트 IP가 프록시 IP로 표시 | 프록시 뒤에서는 `websocket.client.host`가 실제 사용자가 아님 | `X-Forwarded-For` 우선 처리 |
| 신고번호 정규식 패턴 불일치 | 새 신고번호 형식 `SPP-2603-1434237` 미인식 | `SPP-\d{4}-\d{6,8}` 패턴으로 변경 |
| 연속 enqueue 요청 드롭 | 크롤링 중 추가 요청이 폐기됨 | `_pending_queue` 도입 후 완료 시 자동 재실행 |
| 통계 기관 필터 포함 매칭 | `str.contains`로 유사 기관명까지 함께 매칭 | `agencyExact` 파라미터 추가 |
| 통계 검색 500 에러 | `str.contains(regex=True)`가 특수문자 입력에 취약 | `regex=False` 적용 |
| 모바일 첨부 URL 분리 오류 | 개행 구분 URL을 `,` 기준으로 분리 | `split('\n')` 및 `%0A` 지원으로 수정 |
| ARM64 chromedriver Exec format error | x86_64 chromedriver가 ARM64에서 실행됨 | ARM64 감지 시 `/usr/bin/chromedriver` 사용 |
| 레거시 크롤러 처리결과 테이블 오파싱 | 첫 번째 테이블을 읽어 수정 전 답변을 가져옴 | 마지막 테이블(`[-1]`) 사용 |
| 교통위반 과태료 미확인 분류 누락 | 수용인데 금액 파싱 실패 시 값이 비어 있음 | `"미확인"` 상태 추가 |
| 크롤링 방식 설정 위치 분산 | 크롤링 제어 화면에서만 설정 가능해 유지성 저하 | 웹 설정 페이지로 이동, API 저장 지원 |
| 파일 브라우저 다운로드 불가 | 목록만 조회 가능하고 다운로드 엔드포인트 없음 | `/api/v1/files/download?path=` 추가 |
| 재설치 후 구 데이터 잔존 | Android 기본 `allowBackup=true`로 데이터 복원 | `android:allowBackup="false"` 추가 |
| 첨부사진/동영상 무한 로딩 | 쿼리스트링 URL 인식 문제와 재시도 부족 | `Uri.path` 기반 판별, 타임아웃/자동 재시도/에러 UI 추가 |
| WebSocket 비정상 종료 시 세션 소멸 | `SessionMiddleware`가 WebSocket scope에도 적용 | `_WebSocketSafeSessionMiddleware` 추가 |
| cloudflared health check로 세션 소멸 | `/` health check가 인증과 충돌 | `/health` 추가 후 health check 경로 분리 |
| API 크롤러 상태코드 12 미처리 | `검토중` 상태 매핑 누락 | `c_now == 12` → `검토중` 추가 |
| reset 시 parking 테이블 미초기화 | reset 목록에서 parking 상세/merge 테이블 누락 | parking 테이블 추가 |
| `title_to_sql` too many SQL variables | 대량 INSERT가 SQLite 변수 한도 초과 | 100행 배치 분할 + `in_()` 쿼리 청크 처리 |
| 큐 크롤 후 `mysafety.상태`가 `진행`으로 고착 | title 갱신이 생략돼 상세 상태가 상위 테이블로 반영되지 않음 | `deatil_to_sql()`에서 progress 상태 직접 반영 |
| `C_MANAGER_TYPE_NM`이 `진행`으로 남는 문제 | 담당자 처리상태 텍스트를 그대로 저장 | `C_R_PROC_STAT_NM` 및 detail `C_NOW` 기반으로 강제 교정 |
| `crawltitle_api.py`의 `C_NOW` 문자열 타입 미처리 | `"10"` 문자열이 정수 매핑에서 빠짐 | `int(float(c_now))` 변환 추가 |
| 2차 크롤링 시 이전 알림 재발송 | `crawl_changes.json`이 남아 재브로드캐스트됨 | 변경이 없으면 `clear_crawl_changes()`로 삭제 |
| 텔레그램 봇 NetworkError 스택트레이스 노이즈 | 장기 polling 종료 예외가 모두 ERROR로 출력 | `NetworkError`/`TimedOut`를 DEBUG 처리 |
| 신고일 시간까지 저장 | `신고일` 컬럼에 시간까지 보관 | `.split()[0]`으로 날짜만 저장 |
| 보완 차량번호 미갱신 | `SPLMNT_CMPTN_YN == 'Y'` 조건이 너무 엄격 | `SPLMNT_CMPTN_DT` 있고 `SPLMNT_CMPTN_YN != 'N'` 조건으로 완화 |
| WS keepalive ping timeout → Cloudflare 502 | uvicorn 기본 WS ping과 Cloudflare relay가 충돌 | uvicorn ping 비활성화, 앱 레벨 ping 사용 |
| `parse_details()` progress_status 미반환 | 내부 필드를 pop해서 호출부와 불일치 | pop 제거 |
| 버전 체크 엔드포인트 중복 | 모바일과 웹/확장이 서로 다른 경로로 같은 정보를 조회 | `/version` 제거, 모바일은 `/api/v1/server/version`으로 통합 |
| `/server/version` up_to_date 오판 | 문자열 비교만 사용 | `_version_gt(latest, current)` 비교로 교체 |
