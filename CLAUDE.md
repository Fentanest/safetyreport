@AGENTS.md
@PROJECT_RULES.md

# CLAUDE.md — Opus(통합 책임자) 역할

기존 50KB CLAUDE.md 의 기술 정보는 2026-09-24 에 `docs/architecture/` 로 옮겼다(원문: `docs/architecture/legacy-claude-reference.md`,
대응표: `docs/architecture/README.md`). 작업 영역의 문서만 필요할 때 읽는다. 모든 상세 문서를 import 하지 않는다.

## 역할
- 요구 범위 해석, 기능 보존표·통계 명세 확정, 공유 셸/테마 토큰/공통 JS 소유, 작업 분해, 위험 판단, 통합, 최종 판정.
- Gemini 위임은 `docs/agent-dispatch-runbook.md` 절차로 **실제 agy 프로세스**를 실행한다. Claude 서브에이전트에 "Gemini" 이름을 붙여 대체하지 않는다.
- Gemini 결과는 exit code 가 아니라 산출물·stderr·권한 거부·diff·사후 파일 검사로 판정하고 `docs/reviews/` 에 남긴다.

## 작업 후 기록 (기존 작업 규칙 개정판)
- 구조 변경 → 해당 `docs/architecture/*` 또는 `docs/development/*`, 작업/버그/세션 이력 → `CHANGELOG.md`, 코드 → git 커밋.
- 이전의 "README/CLAUDE/CHANGELOG 만 추적" 규칙은 폐기했다. 유지보수 문서(`AGENTS.md`, `PROJECT_RULES.md`, `GEMINI.md`, `docs/**/*.md`),
  프로젝트 스킬(`.agents/skills/*/SKILL.md` 원본, `.claude/skills/*` 동기화 사본), 회귀 테스트(`tests/test_*.py`)를 추적한다. 개인 계획 메모는 여전히 로컬.

## 도구
- 프로젝트 스킬 원본 `.agents/skills/sr-*`, Claude 사본은 `python3 scripts/dev/sync_project_skills.py --repo . --apply` 로 동기화.
- 외부 스킬(frontend-design, playwright-cli)과 MCP(chrome-devtools) 설치·버전은 `docs/development/skills-and-tools.md`.
