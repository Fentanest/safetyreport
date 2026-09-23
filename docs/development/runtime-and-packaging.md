# 실행 환경 · 테스트 실행 경로 · 패키징

작성 2026-09-24. 여기 적힌 명령은 이 날 Linux(Ubuntu, Python 3.14.6, Node 22.17.1, Docker 29.8.1)에서 실제로 실행해 확인한 것이다.

## 1. 두 축
- 서버 호스트: Python source / PyInstaller 번들(Windows·Linux·macOS x64·arm64) / Docker 컨테이너.
- 브라우저 클라이언트: Chromium·Firefox·WebKit 엔진, 실제 Edge/Safari.
Linux Chromium 통과가 Windows 실행파일·macOS 번들 검증을 뜻하지 않는다. 결과는 [../testing/environment-matrix.csv](../testing/environment-matrix.csv).

## 2. 데이터 루트와 리소스 경로
| 구분 | 경로 | 코드 |
|---|---|---|
| source 데이터 | `<repo>/data/` | `settings/settings.py` `AppSettings.__init__` |
| frozen 데이터 | 실행파일 옆 `data/` | 같은 곳(`sys.frozen`) |
| **테스트 데이터** | `SAFETYREPORT_DATA_DIR` (절대경로로 정규화) | `core/utils/runtime_mode.data_dir_override` |
| 템플릿·static | source 는 저장소, frozen 은 `_MEIPASS` | `core/utils/path_utils.resource_path` |

`settings` 는 import 시점에 데이터 루트를 정하고 `main.py` 는 import 시 엔진·로그·세션 키를 만든다. 그래서 환경변수는 **앱 모듈 import 전에** 설정해야 한다.
cwd 를 바꾸는 것만으로는 데이터가 분리되지 않는다.

## 3. fixture 모드 (외부 부작용 차단)
`SAFETYREPORT_FIXTURE_MODE=1` 이면 서버 쪽에서 막는다(브라우저 fetch mock 이 아님). 막힌 동작은 `ExternalSideEffectBlocked(RuntimeError)` 를 던져
기존 오류 처리 경로(`{"status":"error","message":...}`)로 사용자에게 보이고, 로그에 `[fixture] blocked: <동작>` 이 남는다.

| 대상 | 방식 | 위치 |
|---|---|---|
| 크롤러 subprocess(웹·모바일·스케줄러 공통) | block | `services/crawl_manager.CrawlManager.start_crawl` |
| 별점 일괄 제출 | block | `services/rating_service.start_batch_rating` |
| 안전신문고 직접 로그인 / keep-alive | block / skip | `core/crawler/direct_login._make_session`, `start_keepalive` |
| 카카오 지오코딩 요청 | block | `services/geocode_service.resolve_address` |
| Sunwi 통계 수집 / 백그라운드 루프 | block / skip | `services/sunwi_fetcher.build_session`, `sunwi_service.start_background_refresh` |
| 원격 미디어 다운로드 | block | `services/media_proxy_service.ensure_cached` |
| GitHub 릴리스 조회 | block | `core/utils/updater._urlopen` |
| 텔레그램 알림 / 봇 프로세스 | block / 설정으로 끔 | `core/utils/notifier.main`, `settings.telegram_enabled=False` |
| 구글 시트 업로드 | 설정으로 끔 | `settings.google_sheet_enabled=False` |
| 시작 시 지오코딩 백필 / 스케줄러 | skip | `main.lifespan` |

- fixture 모드는 `SAFETYREPORT_DATA_DIR` 없이 켜지면 설정 초기화 단계에서 거부된다(운영 `data/` 보호).
- 계약 테스트: `tests/test_fixture_runtime_mode.py`(Popen·Thread·HTTP 함수가 **호출되지 않음**을 mock 으로 확인).
- 남은 경로(의도적으로 막지 않음): `start.py` 를 사람이 직접 실행하는 경우의 Selenium(`core/crawler/driv.py`). 웹/모바일/스케줄러에서 도달하는 경로는 위 크롤러 가드에서 끊긴다.
- 로컬 부작용은 fixture 루트 안에서 일어난다(설정 저장, DB 수정, CSV/엑셀, 로그, `media_cache`).

## 4. fixture 서버
```sh
python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt     # 공용 venv 가 아닌 worktree 전용 venv
.venv/bin/python scripts/dev/fixture_server.py serve --data-dir .agent-runs/fixture/<이름> --port <포트> --reset
#   → http://127.0.0.1:<포트>  로그인 fixture-admin / fixture-pass-1234, API 키는 <data-dir>/fixture-api-key.txt
.venv/bin/python scripts/dev/fixture_server.py seed --data-dir <경로> --reset    # 데이터만 생성
```
- 데이터: 합성 신고 24건(교통 12·주정차 6·기타 6), 연말·연초 경계, 결측 답변일, 장문 기관명/본문, 미평가 별점, 금액 없는 과태료,
  같은 담당자명·다른 기관, 중복 쌍 1개(canonical 에서 1건으로 축소), 취하·보완요청·답변완료, 감시목록 2건, last_sync.
- 안전장치: 저장소 `data/` 또는 그 하위, 표식(`.safetyreport-fixture`) 없는 비어 있지 않은 디렉터리는 거부. `127.0.0.1` 바인딩 기본.
- 포트 배정: 운영 6819, 크롤러 디버그 9222 사용 금지. 2026-09-24 사용: Opus 18619, Gemini 18629, Playwright 18649/18659, Docker 18639/18669.
- 알려진 제약: 브라우저가 UI 라이브러리를 CDN 에서 받으므로 인터넷이 필요하다. http 접속에서는 DataTables 한국어 파일이 CORS 로 실패한다(baseline BL-1).

## 5. 개발용 Docker
운영 `docker-compose.yml`(Watchtower + Docker socket, 고정 이름, `./data`, 6819)은 테스트에 쓰지 않는다.
```sh
SR_DOCKER_PORT=18639 docker compose -f tools/docker/compose.devtest.yml -p safetyreport-devtest up -d --build
curl -s http://127.0.0.1:18639/health
docker compose -f tools/docker/compose.devtest.yml -p safetyreport-devtest down -v    # 이 project 의 컨테이너·볼륨만
```
- 로컬 소스로 이미지 `safetyreport:devtest` 를 빌드(운영 Dockerfile 그대로), fixture 모드, named volume, `127.0.0.1` 포트만.
- `scripts/dev` 는 운영 이미지에 넣지 않고(`.dockerignore`) 읽기 전용 마운트한다.
- 2026-09-24 Gemini(G2)가 project `safetyreport-devtest-g2` 로 빌드·기동·HTTP 확인·`down -v` 까지 수행, Opus 가 로그·컨테이너 파일 목록·정리 상태를 재확인했다.
  이미지 안에 `.venv/node_modules/.agent-runs/docs/tests/tools` 없음, `web/static`·`web/templates` 있음.
  참고: `Dockerfile.build`, `mysafetyreport.icns` 는 기존 `.dockerignore` 에서 빠져 있어 이미지에 들어간다(기존 동작, 영향 경미).
- prune, 다른 컨테이너 조작, 이미지 push 금지. 이 호스트에는 다른 프로젝트 컨테이너가 떠 있다.

## 6. 리소스와 패키징
- `scripts/build/build_exe.py` 가 `web/templates`, `web/static` 을 add-data 로 넣는다. 새 CSS/JS/이미지는 `web/static/ui/` 에 두면 자동 포함.
- 번들 내부를 데이터 저장소로 쓰지 않는다. Node 는 `tools/web-tests` 개발 도구로만 쓰고 제품 실행에 요구하지 않는다.
- 자산 처리 빌드 단계를 추가하면 PyInstaller·수동 빌드 워크플로·Docker 에 똑같이 넣고 산출물에서 확인한다.
- 릴리즈 경로(`build.yml`: main push + VERSION/태그, `build.sh`: 이미지 push)는 승인 없이 실행하지 않는다.

## 7. 환경별 최소 smoke
1. fixture 로 기동, `/health` 200, `/login` 과 세션 인증, AJAX 401.
2. CSS/JS/logo/favicon HTTP 200·MIME, 콘솔·실패 요청 수집.
3. 목록·필터·통계·상세 모달·CSV·차단 메시지(크롤링/별점).
4. 공백·한글이 들어간 설치 경로에서 source/frozen 런처(미검증).
5. 재기동 후 테마 설정·fixture 데이터 유지, 모바일 API/WS 연결.
