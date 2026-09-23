# 에이전트 위임 런북 (Opus → Gemini/agy)

작성 2026-09-24. 표의 명령·결과는 이 날 이 호스트에서 실제로 실행한 것이다(G1·G1b·G2 작업). 추정은 "미검증"으로 적는다.
자매 저장소 `safetyreport-mobile/docs/agent-dispatch-runbook.md` 의 실측 결과와 같은 agy 설치를 쓴다.

## 1. 역할
- Opus: 공유 셸·테마 토큰·공통 JS 의 소유자, 계약 확정, 작업서 발행, 통합, 최종 판정.
- Gemini: **실제 agy 프로세스**로 호출하는 공동 개발자. 구현·브라우저 검증·테스트·Docker smoke 를 맡는다. Claude 서브에이전트에 Gemini 이름을 붙이지 않는다.

## 2. agy 기본 정보 (실측)
| 항목 | 값 |
|---|---|
| 실행 파일 | `~/.local/bin/agy` 1.2.9 |
| 인증 | 기존 Google OAuth(`~/.gemini/oauth_creds.json`). 구 `gemini` CLI·API 키 경로 없음 |
| 고정 모델 | **`gemini-3.1-pro-high`**(`agy models` 의 Pro 최상위). 가벼운 조회 프롬프트는 `gemini-3.8-flash-low` 를 명시해 쓸 수 있다. 조용히 바꾸지 않는다 |
| 전역 권한 | `~/.gemini/antigravity-cli/settings.json` `command(*)`, `write_file(*)`, `read_file(*)` 허용(다른 프로젝트와 공유 — 이 작업에서 수정하지 않음) |
| 워크스페이스 인식 | `--add-dir <worktree>` 필요. 루트 `GEMINI.md`/`AGENTS.md`, 스킬 `.agents/skills/*`, MCP `.agents/mcp_config.json` 을 읽는다 |
| 확인한 도구 | `view_file`, `write_to_file`, `replace_file_content`, `run_command`, `read_url_content`, `search_web`, 서브에이전트류(G1 probe.md) |
| 스킬 인식 | `agy --add-dir . -p "/skills"` → sr-* 5종, frontend-design, playwright-cli 표시(쿼터 없이 응답) |
| MCP 인식 | 워크스페이스 세션에서 chrome-devtools 도구 30개 나열 확인. headless 안 실제 MCP 도구 호출은 미검증(기본 Ask → soft-deny 가능) |

## 3. 호출 문법 (검증됨)
```sh
git worktree add --detach ../safetyreport-gemini-<task> <base-sha>
cd ../safetyreport-gemini-<task>
R=.agent-runs/<task>; mkdir -p $R; touch $R/.start-marker    # 작업서는 $R/TASK.md
agy --add-dir "$PWD" --model gemini-3.1-pro-high --print-timeout 45m --output-format json \
    -p "작업서 $PWD/$R/TASK.md 를 읽고 그대로 수행하라. 산출물은 모두 $PWD/$R/ 에 써라." > $R/out.json 2> $R/err.txt
```
- `-p` 바로 뒤에 프롬프트를 둔다. `--sandbox` 는 쓰지 않는다(셸이 전부 실패 — mobile 실측). 이것이 이 프로젝트의 표준 "개발 권한" 구성이다.
- `--output-format json` 결과의 `status`, `response`, `duration_seconds`, `usage`, `conversation_id` 를 보관한다.
- 오래 걸리는 작업은 백그라운드로 돌린다. G2 는 셸 백그라운드 작업(Docker 빌드)을 기다리며 "root agent idle; waiting…" 을 stderr 에 남기고 정상 종료했다.
- 재개가 필요하면 `--conversation <id>` 또는 `-c`(설치 버전 help 로 확인한 옵션).

## 4. 권한 경계
| 개발 작업으로 허용(작업서 기본값) | 사전 승인 필요 |
|---|---|
| 저장소 전체 읽기, 할당·연관 코드 수정, 셸·git(로컬)·Python(`.venv`)·Node(`tools/web-tests`), 웹 문서 조회 | 운영 `data/`·config·계정, 실제 크롤링·별점·외부 전송 |
| fixture 서버 기동/종료(자기 PID), 브라우저(`playwright-cli -s=<세션>`, 격리 프로필), chrome-devtools MCP(`--isolated`) | 운영 컨테이너, `docker system prune`, 다른 프로젝트 컨테이너·이미지 |
| 전용 Docker project(`-p safetyreport-devtest-<task>`, 자기 포트, `down -v` 로 정리) | `main` push, 태그, 릴리즈, 이미지 push, VERSION 변경 |
| 자기 worktree 안 파일 쓰기 | 전역 설정·전역 패키지·공용 venv 변경, 다른 worktree 쓰기 |
worktree 는 보안 경계가 아니다. 경계는 작업서 금지 목록 + 사후 검사로 지킨다:
`find <main-checkout> <dev-worktree> ~/.gemini/antigravity-cli/scratch -newer $R/.start-marker -type f -not -path '*/.git/*'`, 각 worktree `git status --short`, `docker ps -a --filter label=com.docker.compose.project=<project>`.
(사후 검사에 Opus 자신이나 다른 세션의 동시 편집이 잡힐 수 있다 — 파일 내용으로 출처를 구분한다.)

## 5. 자원 배정 (2026-09-24 사용 예)
| 자원 | Opus | Gemini |
|---|---|---|
| worktree | `safetyreport-dev`(dev 브랜치) | `safetyreport-gemini-<task>`(detached) |
| fixture 포트 / 데이터 | 18619 / `.agent-runs/fixture/opus` | 18629 / `<worktree>/.agent-runs/<task>/fixture` |
| Playwright webServer | 18649 | 18659 |
| Docker | project `safetyreport-devtest`, 18639 | `safetyreport-devtest-<task>`, 18669 |
| 브라우저 세션 | `sr-opus` | `sr-gemini` |
운영 6819, 크롤러 9222 사용 금지. 포트는 `ss -ltn` 으로 비어 있는지 확인. 공유 base/theme/공통 JS 는 동시에 편집하지 않는다.

## 6. 작업서 필수 항목
[templates/delegation-task.md](templates/delegation-task.md): task_id, role(implementation/review), base_sha, worktree, model, 목표, owned_files, related_changes,
금지 목록, 자원(포트·데이터 루트·세션·Docker project), 입력 경로, 요구 검사, 산출물 경로, 반환 형식, 완료 기준.
**인용 검증 수단을 함께 준다**: `scripts/dev/web_contract_inventory.py`(라우트·DOM id), `--check-ids`. G1 에서 이것 없이 맡겼을 때 존재하지 않는 id 4개가 나왔다.

## 7. 완료 판정
- exit 0 / `status: SUCCESS` 만으로 완료가 아니다. 산출물 존재, 응답의 명령·종료코드, stderr, 권한 거부 문구, 사후 파일 검사, diff 범위를 본다.
- 주장은 Opus 가 표본 재현한다(파일:줄 열람, 인벤토리 대조, 캡처 열람, 컨테이너 상태 확인). 결과는 `docs/reviews/` 에 남긴다.
- 자기 구현물을 독립 승인하지 않는다. Gemini 가 구현하면 Opus 가, Opus 주요 변경은 Gemini 가 증거로 검수한다.
- 2026-09-24 교훈: 실행 증거(테스트·Docker·캡처)는 신뢰할 만했으나 서술형 판정(“전 항목 none”, “인증 우회”)은 틀린 경우가 있었다. 서술 판정은 반드시 근거 줄과 재현을 요구한다.

## 8. 검수 시점
영역 전환, API·인증·패키징 고위험 변경, 누적 약 5파일/800줄, 통합 직전. 첫 시범 화면은 반드시 두 테마 실제 렌더를 본다. 작은 CSS 수정마다 부르지 않는다.
