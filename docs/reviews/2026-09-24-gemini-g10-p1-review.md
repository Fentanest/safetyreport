# Gemini G10 검수 기록 — 시범 P1(신고 목록 + 상세 모달)

작업서: `safetyreport-gemini-g10/.agent-runs/g10/TASK.md`(role: implementation). 실행: agy 1.2.9 `gemini-3.1-pro-high`, 대화 `084660de-…`,
worktree `safetyreport-gemini-g10`, 브랜치 `gemini/p1-list`(dev `f7a5051` 에서 분기).

## 1회차 (2207초, status SUCCESS) — 반려
- 커밋 `902e622`: 5파일 +275/−118. `data_table.html` 인라인 `<style>` → 새 `web/static/ui/list.css`, 상태 배지를 `.sr-badge-*`(토큰 `--sr-status-*`)로 교체(base.html 상세 모달 포함), `theme_head.html` 에서 `list.css` 로드, 새 spec `list-interactions.spec.ts`.
- **보고 불일치**: "Playwright 모두 통과"라 보고했으나 Opus 재실행(chromium) 결과 `4 failed / 19 passed`.
  자체 테스트 2건(다중선택 선택자가 수용·일부수용·불수용 3개에 걸림, 복사 줄 수 기대 오류)은 실제 실패. `stats-layout.spec.ts` 2건은 Opus 테스트의 경쟁 조건(아래 정정).
- ~~**회귀**: 전역 선택자 때문에 통계 표가 다시 가로로 넘침~~ — **Opus 판정 오류(정정)**. `stats-layout.spec.ts` 실패는 Opus 가 쓴 테스트의 경쟁 조건(DataTables 가 한국어 파일을 비동기로 받은 뒤 표를 감싸는데 그 전에 잼)이었다.
  테스트를 고친 뒤 1회차 커밋 `902e622` 을 임시 작업트리에서 다시 돌리면 2 passed. 통계 페이지의 더 구체적인 규칙이 이겨 실제 회귀는 없었다.
  다만 목록 전용 규칙(`th`, `.dataTables_scrollBody` 등)을 모든 페이지에 불러오는 구조는 다른 화면에 번질 위험이 있어 범위 제한 요구는 유지한다.
- **범위 미달**: 캡처가 P0 와 사실상 같음. 참고 시안(01/02/06/07/04)의 목록·모달 재구성 없음. 배지 토큰화만 유효.
- 추적 안 된 캡처 스크립트 3개 방치. 작업 뒤 자기 fixture 서버를 남겨 agy 가 90분 대기(Opus 가 해당 PID 만 종료해 마무리).
- 금지 파일 위반 없음(서비스·코어·통계 템플릿 무변경).

## 2회차
지시서 `.agent-runs/g10/ROUND2.md`: CSS 범위 제한(배지만 공통 `badges.css`), 실제 리뉴얼 항목(작업 바, 표 머리글·행·정렬, 신고일 잘림, 알약형 배지, 빈 결과, 모달 2열 그리드), 테스트 수정·전체 재실행 출력 첨부, 캡처 재제출.
결과는 아래에 추가한다.

## 2회차 (중단)
- 2829초 뒤 `RESOURCE_EXHAUSTED (429): Individual quota reached … Resets in 1h41m` 로 멈춤(12:07 기준, 13:48 무렵 해제). 보고서·커밋 없음.
- 멈추기 전 변경(미커밋): `web/static/ui/list.css`, `web/templates/components/theme_head.html`, `web/templates/data_table.html`, 새 `web/static/ui/badges.css`. 작업트리를 그대로 두고 한도 해제 뒤 같은 대화(`084660de-…`)로 이어서 진행한다.

## 2회차 재개 (13:46~14:02, agy status ERROR, exit 0) — 조건부 수용, Opus 통합 수정 후 dev 합침
- 커밋 `00630cd`(7파일): 목록 전용 CSS 는 `data_table.html` 에서만 로드, 배지는 공통 `badges.css`(알약형), 작업 바 `.sr-list-toolbar`(주요/보조 동작 구분, 390px 정돈), 날짜·상태 열 너비와 `tabular-nums`(신고일 잘림 해소), 빈 결과 문구,
  상세 모달 머리글 배지 + 핵심 필드 2열(390px 1열) + 신고내용·보완 요청 카드. 자체 테스트 2건 수정(정확 일치 선택자, 복사 줄 수).
- 사후 검사: 다른 worktree·메인 체크아웃 변경 0, 추적 안 된 파일은 `.agent-runs/g10/` 안뿐, fixture 서버 종료함.
- **보고 불일치**
  - "20종 캡처"라고 했으나 파일은 8장, 요구한 상세검색 열림·첨부 모달·빈 결과 캡처 없음.
  - 캡처가 틀림: `*_dark_*` 가 같은 크기의 `*_light_*` 와 바이트까지 같고(다크 미적용), `detail_*_1440` 은 상세 모달이 아니라 목록 화면.
  - unittest "50 tests" — 분기 시점 기준이라 틀린 건 아니나 현재 dev(96)와 다름.
  - webkit `accessibility baseline` ENOENT 실패는 Opus 재실행에서 재현되지 않음(통과).
- **Opus 재실행(G10 작업트리, 3개 브라우저):** 56 passed, 1 failed(webkit `stats-layout` 1280 — 이 브랜치에 dev `ac31131` 의 `<wbr>` 수정이 없어서. 예상대로).
- **Opus 캡처(두 테마 × 1440·390 × 목록·상세·첨부·빈 결과·상세검색, chromium):** 목록·상세·첨부 모달은 두 테마 모두 정상 렌더.
  문제 2건 발견:
  1. 빈 결과 안내가 **보이지 않음** — 문구는 있으나 scrollX 표의 3938px 폭 칸 가운데(화면 1118px 밖)에 놓임.
  2. 390px 에서 DataTables 검색칸이 카드 밖으로 25px 나감 — dev 에도 있던 기존 문제(회귀 아님).
- **Opus 통합 수정(dev):** 빈 결과 안내를 스크롤 영역 왼쪽에 고정하고 보이는 폭에 맞춤(`pinEmptyState`, `drawCallback`·resize), 좁은 화면 검색칸 flex. 회귀 테스트 `tools/web-tests/specs/list-empty-state.spec.ts`.
- **통합 뒤 dev:** Playwright 66 passed(chromium·firefox·webkit), unittest 96 OK.
