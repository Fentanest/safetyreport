# 도구 · 스킬 정본

설치·버전·가동 확인 기록. 2026-09-24 Opus 가 설치하고 실제 실행으로 확인했다. 자동 전체 업그레이드는 하지 않는다.

## 가동 증빙
| 도구 | 버전 / 출처 | 설치 위치 · 범위 | 확인한 작업 | 결과 |
|---|---|---|---|---|
| agy (Gemini) | 1.2.9, `~/.local/bin/agy`, Google OAuth(기존) | 호스트 전역(기존 설치 재사용) | G1·G1b·G2 작업 실행: 셸·파일 쓰기·Python·Node·브라우저(Playwright 라이브러리)·fixture 서버·Docker build/run | 동작. 품질 문제는 docs/reviews 참고 |
| Python | 3.14.6 (`/usr/bin/python3.14`), `requirements.txt` | worktree `.venv`(추적 안 함) | unittest 25개, fixture 서버 | 통과. 공용 `safetyreport/venv` 는 `itsdangerous` 누락으로 `main` import 실패 → 사용 안 함 |
| pyflakes | pip 최신(개발 전용, `.venv` 에만) | `.venv` | 변경 파일 undefined name 검사 | 0건 |
| Playwright Test | `@playwright/test` 1.63.0 (microsoft/playwright) | `tools/web-tests` devDependency, `package-lock.json` 고정 | `specs/fixture-smoke.spec.ts` chromium+firefox | chromium·firefox·webkit 15/15 통과. WebKit 시스템 의존성은 `sudo npx playwright install-deps webkit`(2026-09-24 사용자 승인)으로 설치 |
| axe | `@axe-core/playwright` 4.13.0 (dequelabs) | 같은 곳 | 로그인/대시보드/목록/통계 위반 수 기록(report-only) | 3/3/3/1 |
| Playwright CLI | `@playwright/cli` 0.1.21 (microsoft/playwright-cli) | 같은 곳, 실행 `npx playwright-cli -s=<세션>` | `install --skills`(claude, agents) | 스킬 설치 + Opus 세션 `sr-opus` 로 fixture 로그인→`/stats`→`find`→`screenshot`→`close` 확인(인메모리 프로필, `.playwright-cli/` 스냅샷은 추적 안 함). G2 는 CLI 대신 라이브러리 스크립트를 썼다 |
| Playwright CLI 스킬 | CLI 0.1.21 번들 SKILL.md + references | `.claude/skills/playwright-cli/`, `.agents/skills/playwright-cli/` — **Git 미추적**, 재생성: `tools/web-tests/node_modules/.bin/playwright-cli install --skills` / `--skills agents` | 파일 생성 확인 | 새 세션에서 인식 확인 필요 |
| Chrome DevTools MCP | `chrome-devtools-mcp@1.10.1` (ChromeDevTools), npx | `.mcp.json`(Claude), `.agents/mcp_config.json`(agy) — 둘 다 추적. 옵션 `--isolated --headless --no-usage-statistics --no-performance-crux --viewport 1440x900` | stdio 핸드셰이크 → 도구 30개 → `new_page`/`navigate_page`(fixture 로그인) → 콘솔·네트워크 조회 | 동작(`.agent-runs/inventory/mcp_probe.txt`). agy 세션 로드 확인(`--add-dir` 워크스페이스에서 도구 30개 나열, gemini-3.8-flash-low 조회 프롬프트). agy headless 안에서의 **실제 MCP 도구 호출은 미검증**(MCP 도구 기본 승인 정책상 soft-deny 가능). Claude 세션 로드는 새 세션에서 확인 필요 |
| Anthropic frontend-design | anthropics/skills `skills/frontend-design` @ `41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f`, Apache-2.0(LICENSE.txt 동봉) | `.claude/skills/frontend-design/`, `.agents/skills/frontend-design/` — **Git 미추적**. SKILL.md sha256 `d91970639e9f…c1d3` | 본문 검토 | 사용 규칙: 승인 시안이 우선, 새 컨셉 창작 금지(ui-renewal-spec §1) |
| 프로젝트 스킬 sr-* 5종 | 이 저장소(블루프린트 초안을 실제 명령에 맞게 수정) | 원본 `.agents/skills/sr-*/SKILL.md`, 사본 `.claude/skills/sr-*/SKILL.md` — 추적 | `python3 scripts/dev/sync_project_skills.py --repo . --check`; `agy --add-dir . -p "/skills"` | same 5/5, agy 가 sr-* 5종·frontend-design·playwright-cli 인식. Claude 인식은 새 세션에서 확인 필요 |
| Docker | 29.8.1, context `default` | 호스트 | compose.devtest 빌드·기동·정리(G2) | 동작 |
| Node | 22.17.1 (nvm) | 호스트 | npm ci / playwright | 동작 |

webapp-testing(Anthropic)은 설치하지 않았다: 영구 회귀를 Playwright Test(TypeScript)로 정했고, 그 helper 의 서버 기동 예시는 이 앱에서 외부 작업을 시작시킨다.
Flutter/Compose 스킬, React/Next 전제 스킬, "공식 FastAPI/PyInstaller 스킬"(존재 확인 안 됨)은 쓰지 않는다. Selenium 은 크롤러 기능이므로 UI 테스트 도구와 무관하게 유지한다.

## 외부 스킬 재설치
```sh
# Playwright CLI 스킬
tools/web-tests/node_modules/.bin/playwright-cli install --skills          # → .claude/skills/playwright-cli
tools/web-tests/node_modules/.bin/playwright-cli install --skills agents   # → .agents/skills/playwright-cli
# frontend-design (커밋 고정)
SHA=41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f
for d in .claude/skills/frontend-design .agents/skills/frontend-design; do mkdir -p $d
  for f in SKILL.md LICENSE.txt; do gh api "repos/anthropics/skills/contents/skills/frontend-design/$f?ref=$SHA" -q .content | base64 -d > $d/$f; done; done
```
`playwright-cli install` 은 저장소 루트에 빈 `.playwright/` 디렉터리도 만든다(추적 안 함).

## 프로젝트 스킬 편집 규칙
`.agents/skills/sr-*` 만 고치고 `python3 scripts/dev/sync_project_skills.py --repo . --apply`(충돌 시 `--apply --replace`)로 `.claude/skills` 에 복사한다. 두 곳을 손으로 따로 고치지 않는다.
