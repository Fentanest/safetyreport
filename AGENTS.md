# AGENTS.md — safetyreport 공통 진입점

모든 에이전트(Claude/Opus, Gemini/agy, 기타)가 먼저 읽는 짧은 문서다. 상세는 표를 따라 필요한 것만 읽는다.

## 이 저장소
- "나만의 안전신문고" 서버. Python FastAPI + Jinja2 + Bootstrap 5.3 + jQuery DataTables 웹UI, SQLite(SQLAlchemy), Selenium/curl_cffi 크롤러.
- 제공 형태: Python source, PyInstaller 실행파일(Windows/Linux/macOS x64·arm64), Docker 이미지(`ghcr.io/fentanest/safetyreport`).
- 안전신문고에 **이미 접수한 신고를 조회·관리**한다. 신고 접수 기능은 없다.
- 소비자: 모바일 앱 `safetyreport-mobile`(별도 저장소, `/api/v1` + `/ws/events`), 크롬 확장(`/api/v1/vehicle`, `/api/v1/crawl/done/ext`).
  이 저장소의 웹UI 작업 대상이 아니다. 계약만 지킨다.

## 반드시 지킬 것
[PROJECT_RULES.md](PROJECT_RULES.md) — 범위, 데이터 의미, 협업·권한, 운영 승인 경계, 완료 기준.

## 문서 지도
| 필요할 때 | 문서 |
|---|---|
| 구조·경로·로그·빌드 | [docs/architecture/overview.md](docs/architecture/overview.md) |
| 설정·DB·API·WS·완료 마커 계약 | [docs/architecture/data-contracts.md](docs/architecture/data-contracts.md) |
| 크롤러·파서·처리 규칙 | [docs/architecture/crawling-and-processing.md](docs/architecture/crawling-and-processing.md) |
| 현재 웹UI 동작 규칙 | [docs/architecture/web-ui.md](docs/architecture/web-ui.md) |
| 커뮤니티 계정·필수 게이트·초기화·공유 업로드 | [community-account](docs/architecture/community-account.md) · [community-gate](docs/architecture/community-gate.md) · [community-rebuild](docs/architecture/community-rebuild.md) · [community-upload](docs/architecture/community-upload.md), 계약 사본 `contracts/community-ingest/` |
| 커뮤니티 계정 연결(safeauth) | [docs/architecture/community-account.md](docs/architecture/community-account.md) |
| 원래 CLAUDE.md 원문과 이관 대응표 | [docs/architecture/README.md](docs/architecture/README.md) |
| 웹UI 리뉴얼 설계 정본 | [docs/design/ui-renewal-spec.md](docs/design/ui-renewal-spec.md) |
| 기능 보존표 / 참고 이미지 목록 | [docs/design/feature-matrix.csv](docs/design/feature-matrix.csv) · [docs/design/asset-manifest.csv](docs/design/asset-manifest.csv) |
| 시범 범위 DOM/JS 계약 | [docs/design/pilot-dom-contracts.md](docs/design/pilot-dom-contracts.md) |
| 통계 지표 정의 | [docs/design/statistics-spec.md](docs/design/statistics-spec.md) |
| 개발 서버·fixture·Docker·패키징 | [docs/development/runtime-and-packaging.md](docs/development/runtime-and-packaging.md) |
| 설치한 도구·스킬·버전 | [docs/development/skills-and-tools.md](docs/development/skills-and-tools.md) |
| 테스트 계획 / baseline | [docs/testing/web-ui-test-plan.md](docs/testing/web-ui-test-plan.md) · [docs/testing/baseline-2026-09-24.md](docs/testing/baseline-2026-09-24.md) |
| 에이전트 위임(agy 호출·권한·검증) | [docs/agent-dispatch-runbook.md](docs/agent-dispatch-runbook.md) |
| 검수 기록 | [docs/reviews/](docs/reviews/) |
| 다음 작업 계획 | [docs/plans/](docs/plans/) |

## 문서 원칙
- 작업/버그/세션 이력은 `CHANGELOG.md`에만 기록한다. 실제로 한 변경만 적는다.
- 구조 변경·설계 의도·운영 주의점은 해당 `docs/architecture/*` 문서에 적는다. 루트 문서에 상세를 복사하지 않는다.
- 문서와 코드가 다르면 코드를 근거로 삼고, 차이를 해당 문서의 "코드 대조 정정" 표에 남긴다.
- 임시 로그·캡처·에이전트 산출물은 `.agent-runs/`(추적 안 함)에 둔다.

## 기본 명령
```sh
python3.14 -m venv .venv && .venv/bin/pip install -r requirements.txt   # 개발 venv (공용 venv 를 건드리지 않는다)
SAFETYREPORT_DATA_DIR=$(mktemp -d) .venv/bin/python -m unittest discover -s tests -p "test_*.py"   # baseline 2026-09-24: 18 passed
.venv/bin/python scripts/dev/fixture_server.py serve --data-dir .agent-runs/fixture/<name> --port <port> --reset   # 합성 데이터 서버
cd tools/web-tests && npm ci && npx playwright test --project=chromium --project=firefox                  # 브라우저 스모크
```
`main.py` 를 그대로 실행하면 운영 `data/` 와 외부 서비스(크롤러·텔레그램·Sunwi·GitHub)를 쓴다. 개발 확인은 fixture 서버로 한다.
릴리즈 경로(`build.yml`, `build.sh`, VERSION, 태그, `main` push)는 승인 없이 실행하지 않는다.
