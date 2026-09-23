# Gemini(agy) 부트스트랩 작업 검수 — 2026-09-24

- 검수자: Opus 5.5(Claude). 구현자: Gemini `gemini-3.1-pro-high`(agy 1.2.9).
- 원본 산출물: `.agent-runs/gemini/{g1-worktree,g2-worktree}/`(추적 안 함). 각 작업 폴더에 TASK.md, out.json, err.txt, exit_code, started_at.

## 호출 증빙
| 작업 | 역할 | base | worktree | conversation_id | 시간 | 토큰 | exit / status | stderr |
|---|---|---|---|---|---|---|---|---|
| G1 기능 보존표 + 권한 실측 | implementation(조사) | 17df6cb | safetyreport-gemini-g1 | c440e8e2-7136-4a5b-9d4e-c70766e002ee | 122s | 90,117 | 0 / SUCCESS | 없음 |
| G1b 기능 보존표 재작업 | implementation(조사) | 17df6cb | 같은 곳 | 704d689d-4ba4-439d-8f77-df83a7fbe9a1 | 272s | 168,849 | 0 / SUCCESS | 없음 |
| G2 Opus 변경 검수 + 서버·브라우저·Docker 실행 | review + 실행 | 62da7f4 | safetyreport-gemini-g2 | 899af894-5a72-45d3-ab1f-62f825f0e1ec | 404s | 195,193 | 0 / SUCCESS | "root agent idle; waiting up to 50m0s for 1 background task(s)"(정상) |

권한 거부: 세 작업 모두 없음. 사후 파일 검사: Gemini 작업트리 밖에 쓴 파일 없음(검사에 잡힌 dev·메인 checkout 변경은 Opus 편집과 모바일 세션 작업으로 내용 확인).
Gemini 가 실제로 쓴 도구: 셸(`git`, Python venv, `node`, `npm ci`, `npx playwright test`, `docker compose build/up/exec/down`, `curl`), 파일 쓰기, 백그라운드 서버 기동·종료.

## 판정

| ID | 주장 | Opus 확인 | 판정 |
|---|---|---|---|
| G1-1 | 권한 실측(셸·쓰기·Python 3.14.6·FastAPI 0.135.1·Node·Docker) | probe.md 명령·출력 대조 | 수용 |
| G1-2 | 기능표: 초안과 1:1 일치, 화면당 1행 | 요구(동작 단위) 미충족 | **반려** |
| G1-3 | DOM id `#searchForm #dateFilter #dataTable #sunwiTable` | `--check-ids` 결과 모두 MISSING | **반려(허위 근거)** |
| G1-4 | `/stats/map/missing` 근거 `stats.py:20` | 실제 `stats.py:135` | 반려 |
| G1b-1 | 115행, 라우트 98/98 매핑, MISSING 0 | 기계 검증 통과. 단 인벤토리 도구(Opus 작성)가 빈 경로 라우트 2개(`/db-editor`, `/file-browser`)를 놓쳐 실제는 100개 — Opus 도구 결함 | 부분 수용(체크리스트로 사용) |
| G1b-2 | 목록 화면 "시트", "중복검사" 동작 | data_table 에 없음. UI-042 근거 줄은 `#searchLaw` | 반려 |
| G1b-3 | 별점 시작 부작용 "subprocess:crawler" | 실제는 안전신문고 별점 HTTP POST(`star_rating_service`) | 반려 |
| G1b-4 | "/api/v1 전체가 인증 우회" | 37개 라우트 모두 `Depends(_require_api_key*)`, 키 없으면 401 실측 | **반려(오판)** |
| G1b-5 | GET 부작용: `/logout` | 맞음. 단 `/stats/map`(백필 시작) 누락 | 부분 수용 |
| G1b-6 | base.html 전역 함수 결합(`proxyMediaUrl` 등 data_table 호출 줄) | 독립 조사와 일치 | 수용 |
| G2-1 | unittest 18/18 | unittest.txt 확인 | 수용 |
| G2-2 | fixture 서버 18629 기동·캡처 4장(상세 모달 포함) | 캡처 열람: 실제 렌더 | 수용. 단 작업서의 `playwright-cli -s=sr-gemini` 대신 라이브러리 스크립트 사용(지시 이탈) |
| G2-3 | Playwright chromium+firefox 10/10 | playwright.txt 확인 | 수용 |
| G2-4 | Docker 빌드·HTTP 200·이미지 내용·`down -v` | docker.txt 의 파일 목록·HTTP·정리 로그 확인, `docker ps/volume` 잔여 없음 | 수용 |
| G2-5 | 코드 검수 5항목 모두 severity none | "gspread 는 block_if_fixture 로 차단" — 실제는 설정값으로 끔. 근거 `fixture_server.py:350` — 파일 220여 줄 | **반려(검수 신뢰도 낮음)** |

Opus 가 자체 확인한 잔여 위험(G2 가 놓침): fixture 모드를 데이터 루트 없이 켜면 운영 `data/` 사용 → 설정 초기화에서 거부하도록 수정·테스트 추가.
`start.py` 직접 실행 시 Selenium 경로는 가드하지 않음(문서화).

## 교훈 → 런북 반영
1. 조사 작업에는 기계 인벤토리와 `--check-ids` 검증 수단을 같이 준다(G1 → G1b 품질 차이).
2. 실행 증거는 신뢰도가 높고, 서술형 판정은 틀릴 수 있다. 판정에는 근거 줄 + 재현 명령을 요구한다.
3. 지시한 도구(playwright-cli 세션)를 쓰지 않으면 도구 가동 증거가 되지 않는다 → Opus 가 직접 확인했다.

## 최종
- 기능 보존표는 Opus 가 인벤토리 100개 라우트 + 독립 조사(Claude Explore, 4개 템플릿 줄 단위)로 재작성: `docs/design/feature-matrix.csv`(118행, 라우트 100/100, 인용 id 전부 실재).
- Gemini 는 셸·파일·Python·Node·브라우저 자동화·로컬 서버·Docker 를 실제로 사용한 **공동 개발자**로 동작함을 확인했다. 다음 작업부터 구현 역할을 맡길 수 있다.
