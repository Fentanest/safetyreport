---
name: sr-browser-visual-qa
description: Run real browser checks and visual regressions for safetyreport tables, modals, filters, light/dark themes and error states against the fixture server.
---

# 실제 브라우저 UI 검증

먼저 `docs/testing/web-ui-test-plan.md` 와 `docs/development/runtime-and-packaging.md` 의 fixture 기동 명령을 확인한다.

1. 자기 전용 fixture 서버를 띄운다: `.venv/bin/python scripts/dev/fixture_server.py serve --data-dir .agent-runs/fixture/<name> --port <port> --reset`
   (로그인 fixture-admin / fixture-pass-1234). 운영 `data/`, 크롤러 Chrome(9222), 개인 브라우저 프로필에 붙지 않는다.
2. 탐색은 `npx playwright-cli -s=<세션명> open http://127.0.0.1:<port>/login` (세션명은 에이전트별로 다르게), 진단은 chrome-devtools MCP(`--isolated`).
   공유 머신에서 `close-all`/`kill-all` 을 쓰지 않는다.
3. 준비 판단은 명시적 assertion(로그인 후 URL, 표 첫 행, 통계 표 값). 전역 `networkidle`·고정 sleep 에 의존하지 않는다(Sunwi 폴링·버전 확인 요청이 계속 있다).
4. 목록 검색·상세검색(AND/OR)·정렬·페이지·선택·번호복사·CSV·상세/첨부 모달·offcanvas·테마 전환을 실제 DOM 으로 조작한다.
5. 두 테마, 긴 한글, 1280/1440/1920 폭과 390 폭, 200% 확대, 키보드/focus, 빈/로딩/오류 상태를 점검한다. 콘솔·실패 요청을 저장한다.
   (기존 결함: http 접속 시 DataTables ko.json CORS 실패 — baseline 문서 참고. 신규 회귀와 구분한다.)
6. 비교 기준은 사용자가 승인한 실제 앱 렌더다. 생성형 보드를 픽셀 정답으로 쓰지 않는다.
7. healer 제안의 skip·assert 제거·스냅샷 일괄 갱신으로 결함을 숨기지 않는다.

산출: SHA/환경/fixture/명령/캡처/trace/콘솔, passed·failed·blocked·not-run 을 구분한 review report(`docs/templates/review-report.md`).
직접 수정하면 implementation 역할 전환을 알리고, 자신의 변경은 다른 실행자가 재검수한다.
