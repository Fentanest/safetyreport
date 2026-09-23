# .gitignore / .dockerignore 개편 기록 (2026-09-24)

## 배경
기존 `.gitignore` 의 `*.md`, `*.json`, `test_*` 는 개인 메모·설정·임시 스크립트를 막으려는 규칙이었지만,
에이전트 공통 문서(AGENTS/GEMINI/PROJECT_RULES), 스킬(SKILL.md), 설계 문서, 회귀 테스트까지 숨겼다.
실제로 `tests/test_db_backup_regression.py` 는 메인 checkout 에만 있고 한 번도 추적되지 않았다(이 파일은 사용자 로컬 테스트라 이번에 옮기지 않았다 — 필요하면 사용자 확인 후 추가).

## Git (적용)
- 보호 유지: `data/`, `auth/`, `logs/`, `config.ini`, `.env`, `*.xlsx`, `*.log`, `*.json`, `*.md`, `test_*`.
- 새 보호: `*.db`, `*.db-wal`, `*.db-shm`(루트에 DB 사본이 놓이는 사례가 있었다), `.venv/`, `.agent-runs/`, `.playwright-cli/`, `.playwright/`,
  `node_modules/`, `tools/web-tests/{test-results,playwright-report,.auth}/`, `.claude/settings.local.json`, 외부 스킬 설치 폴더, `docs/design/reference/*.png`.
- 명시적 예외(추적): `/README.md` `/CLAUDE.md` `/CHANGELOG.md` `/AGENTS.md` `/GEMINI.md` `/PROJECT_RULES.md`, `/docs/**/*.md`,
  `/.agents/skills/**/SKILL.md`, `/.claude/skills/**/SKILL.md`, `/.agents/mcp_config.json`, `/.mcp.json`, `/tests/test_*.py`,
  `/tools/web-tests/{package.json,package-lock.json,tsconfig.json}`.
- CSV(`docs/design/*.csv`)와 `.ts` 는 원래 무시 대상이 아니다.

검증(2026-09-24): `git check-ignore` 로 위 예외 경로는 추적 가능, `data/config.ini`·`*.db`·`.agent-runs/`·`node_modules/`·참고 PNG 는 무시됨을 확인.
커밋 전 staged 목록에 `data/`, `config.ini`, `.env`, `auth/`, `*.db`, API 키 파일이 없는지 확인했다.

## Docker (적용)
기존 제외를 유지하고 `docker-compose*.yml`, `.agents/`, `.mcp.json`, `.playwright/`, `.agent-runs/`, `.playwright-cli/`, `.venv/`, `node_modules/`,
`tools/`, `docs/`, `tests/`, `scripts/dev/`, `*.db*` 를 추가했다. `web/static`, `web/templates` 는 제외하지 않는다.
검증: 개발 Docker 빌드 후 컨테이너 `/app` 목록에서 위 항목 부재, `web/static`·`web/templates` 존재 확인(G2 실행, Opus 재확인).
기존부터 들어가는 `Dockerfile.build`, `mysafetyreport.icns` 는 이번에 바꾸지 않았다.

## 새 파일을 추가할 때
`git check-ignore -v <경로>` 로 판정을 먼저 본다. fixture JSON 등 새 JSON 을 추적해야 하면 그 경로만 예외로 추가한다(전체 해제 금지).
