# 웹UI 검증 계획

현재 상태(2026-09-24): 레이어 1·2·3 의 기반과 baseline 이 있다([baseline-2026-09-24.md](baseline-2026-09-24.md)). 테마 관련 검증은 시범 구현과 함께 추가한다.

## 1. 검증 레이어
| 레이어 | 도구·위치 | 실행 |
|---|---|---|
| 1. Python 단위·계약 | `tests/`(unittest) — 기존 회귀 7 + fixture seam 12 + 통계 의미 6 | `SAFETYREPORT_DATA_DIR=$(mktemp -d) .venv/bin/python -m unittest discover -s tests -p "test_*.py"` |
| 2. HTTP/라우트·인증 | fixture 서버 + curl 또는 Playwright `request` | `scripts/dev/fixture_server.py serve …` |
| 3. 브라우저 회귀 | `tools/web-tests`(Playwright Test 1.63.0, 개발 전용 Node) — webServer 가 fixture 서버를 임시 루트로 자동 기동 | `cd tools/web-tests && npm ci && npx playwright test --project=chromium --project=firefox` (`SR_TEST_PORT`, `SR_PYTHON`, `SR_FIXTURE_DIR` 로 조정) |
| 4. 접근성 | `@axe-core/playwright` + 키보드/초점/확대/대비 수동 | 현재 report-only(위반 수 기록). 시범부터 "신규 위반 0" 기준 |
| 5. 패키징 | source/frozen/Docker smoke | [../development/runtime-and-packaging.md](../development/runtime-and-packaging.md) §5-7 |
| 탐색·진단 | `playwright-cli -s=<세션>`(에이전트 탐색), chrome-devtools MCP(콘솔·네트워크·성능) | 영구 테스트로 옮길 만한 것은 `tools/web-tests/specs` 로 |

Anthropic webapp-testing 의 Python Playwright 스크립트를 두 번째 프레임워크로 유지하지 않는다.

## 2. 준비 완료 판단
고정 sleep·전역 `networkidle` 금지: 대시보드는 Sunwi 3초/30초 폴링, 모든 페이지는 5분 버전 확인이 있다.
로그인 후 URL, 표 첫 행, 통계 표의 특정 기관 행, 모달 제목 같은 명시적 assertion 으로 기다린다.
테마 전환 뒤에는 `html[data-bs-theme]` 값과 대표 요소의 계산된 색을 확인한다.

## 3. 필수 동선 (시범 범위부터)
| 영역 | 확인 | 상태 |
|---|---|---|
| 로그인/세션 | 정상·오류, 일반 이동 302 vs AJAX 401, 두 테마 | 302/401 자동화됨, 테마 미구현 |
| 목록 | 상세검색 AND/OR, 처리상태·별점 다중선택, 날짜 범위, 정렬, 페이지, 가로 스크롤, 0건 | 로드만 자동화 |
| 다중선택 | 페이지 이동 후 선택 유지, 선택/현재 페이지/전체 복사 범위, 감시 추가, 선택 크롤링 차단 메시지 | 크롤링 차단만 자동화 |
| 상세 모달 | 필드·링크·장문·보완 요약·첨부, 닫기/포커스 복귀, 동영상 정리 | Gemini 캡처(수동) |
| 통계 | 표 값 = unittest 기대값, 탭·연도·법규·열 선택, 드릴다운 URL, 빈 결과 | 화면 로드·강서경찰서 행 자동화 |
| CSV | 헤더 = th 텍스트, 필터 적용 행 수 | 미자동화 |
| 테마 | light/dark/system, 새로고침 유지, 첫 paint 깜빡임, 모달·offcanvas·flatpickr·DataTables | 시범에서 추가 |
| 관리 화면 | 별점/크롤링은 차단 메시지, 설정·백업·파일·편집은 fixture 루트에서만 | 시범 이후 |

## 4. 화면 매트릭스
1440×900(기본), 1920×1080, 1280×800, 390×844. Chromium·Firefox 필수, WebKit 은 호스트 의존성 해결 후.
키보드 Tab/Shift-Tab/Enter/Escape, 200% 확대, reduced motion, 두 테마. 핵심 페이지 전체 + 나머지 대표 조합으로 제한한다.

## 5. 스크린샷 기준
사용자가 승인한 **실제 앱 렌더**만 golden 으로 쓴다(`toHaveScreenshot`, OS/브라우저별 분리). 생성형 시안은 비교 정답이 아니다.
OS·브라우저·폰트·viewport·DPR·시간·locale·fixture·애니메이션을 고정한다. 날짜 의존 영역(최근 3일 답변)은 fixture 날짜 고정 또는 마스킹.
실패 시 trace·diff·console·network 를 남긴다. 캡처에 API 키·비밀번호가 없어야 한다(기기 화면은 마스킹).

## 6. 금지
제품 결함 때문에 `test.skip`·assert 제거·스냅샷 일괄 갱신을 하지 않는다(healer 제안 포함). fixture/API stub 만 검사한 것을 통합 검증이라 부르지 않는다.

## 7. 보고
각 체크: expected / actual / base SHA / branch / fixture / OS / browser / command / artifact / passed|failed|blocked|not-run.
