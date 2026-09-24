# Gemini G12 검수 기록 — P2(통계 상단) 교차 검토

작업서: `safetyreport-gemini-g12/.agent-runs/g12/TASK.md`(role: review). 실행: agy 1.2.9 `gemini-3.1-pro-high`, 829초, status SUCCESS. worktree `safetyreport-gemini-g12`(d55efe3, detached).

## 사후 검사
- 추적 파일 변경 0, 새 파일은 `.agent-runs/g12/` 안(재현 spec 은 규칙대로 `repro/` 로 옮겨 둠). 자기 fixture 서버 종료함. 캡처 8장 모두 서로 다른 파일(다크 적용 확인).

## 주장 판정
| 주장 | 판정 | 근거 |
|---|---|---|
| 기존 Playwright 84 중 81 통과, `/api/v1/summary` 계약 3건(브라우저별) 실패 | **재현 안 됨** | 같은 커밋 d55efe3 에서 Opus 재실행 `fixture-smoke.spec.ts` 15 passed(3개 브라우저). Gemini 실행 환경(포트·fixture 키) 문제로 추정 |
| `data_table.html` 의 `__없음__` 제외가 목록 법규 검색을 깨뜨림 | **틀림** | 바뀐 줄(`data_table.html:248`)은 URL 인자로 검색칸을 미리 채우는 경우만. 사용자가 직접 입력하는 검색과 무관. `law=__없음__` 은 서버가 이미 거른다(`report_query_service.py:98`) |
| 나머지(탭 전환·법규 칩·열 선택·합계·폼·드릴다운·aria·CSS 범위) 문제 없음 | 동의 | 대부분 기존 spec 통과에 근거. 키보드·aria-expanded 는 "코드상 추정"으로 표시됨 |

## Gemini 가 놓치고 Opus 가 캡처에서 찾은 것
- 390px 에서 상세 조건 창(offcanvas, z-index 1045)을 열면 사이드바 메뉴 버튼(`.btn-sidebar-toggle`, 1046)이 창 제목 첫 글자를 가림(Gemini 캡처 `stats_dark_390_offcanvas.png` 에 보임, 보고서는 "문제없음"). 목록 페이지도 같음.
  수정: `base.html` offcanvas 열림/닫힘 때 떠 있는 검색 버튼과 함께 메뉴 버튼도 숨김. 테스트 `theme-pilot.spec.ts` "on phones the menu button does not cover an open search panel".
