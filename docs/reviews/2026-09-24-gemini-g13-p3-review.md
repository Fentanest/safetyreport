# Gemini G13 검수 기록 — P3 대시보드 리뉴얼

작업서: `safetyreport-gemini-g13/.agent-runs/g13/TASK.md`(role: implementation). 브랜치 `gemini/p3-dashboard`(dev 9a06a06 에서 분기).
실행: agy 1.2.9 `gemini-3.1-pro-high`. 1차 130초 만에 429(할당량), 8분 뒤 같은 대화로 재개 → 1594초, status ERROR(보고·커밋은 남음). Gemini 커밋 `d933d0e`.

## 사후 검사
- 다른 worktree 에 Gemini 가 쓴 파일 없음(같은 시각 `safetyreport-mobile-stats` 변경은 Opus 의 병합). 서버 종료함.
- 캡처용 spec `tools/web-tests/specs/dashboard-screenshots.spec.ts` 를 테스트 폴더에 남김(전체 테스트에 섞임) → Opus 가 `.agent-runs/g13/` 로 옮김.

## 판정: 조건부 수용(Opus 통합 수정 뒤), dev 병합은 사용자 시범 승인 후
- 좋음: `index.html` 을 토큰 카드 그리드로, `dashboard.css` 는 대시보드에서만 로드. DOM 계약(`#sunwi*` 9종, `.progress-bar[data-width]` 11, `a.report-detail-link` 2) 전부 유지, 하드코딩 색(`bg-white` 3, `bg-light` 5, `text-dark` 11, `#212529` 2) 0 으로. 카드·표 링크 대상 전후 동일(추출 비교).
- **보고 불일치**: "npx playwright test 3개 브라우저 15개 모두 통과" — 실제로는 새 spec 하나만 실행(전체 약 100개). Opus 가 전체를 돌리자 `fixture-smoke` 3건 실패.
- **회귀**: 마지막 크롤링 시각(`last_crawl_time`) 표시가 사라짐(기존 `fixture-smoke` 가 잡음).
- Opus 캡처에서 추가 발견: 390px 에서 카드 1열(12장 세로), 감시목록·최근 답변 표가 `table-layout: fixed; width:100%` 로 찌그러짐 → 줄바꿈 방지를 넣자 칸이 겹치고, 최소 폭을 주자 `grid-template-columns: 1fr` 격자가 늘어나 페이지 가로 넘침.
- 비율 바가 절반만 찬 캡처는 애니메이션 도중 촬영(2.5초 뒤 측정: 칸 합 = 막대 폭) — 결함 아님.

## Opus 통합 수정(같은 브랜치, `4443cb1`, `537252d`)
마지막 크롤링 시각 복원, 카드 키보드 접근(role=link·tabindex·Enter/Space, 포커스 테두리), 휴대폰 2열 카드, 표는 최소 폭 + 가로 스크롤(격자 `minmax(0, 1fr)`), 신고번호 한 줄.
회귀 테스트 `dashboard-cards.spec.ts` 에 "마지막 크롤링 시각 + 키보드로 카드 이동" 추가.

## 검증
브랜치에서 Playwright 104 passed + 1 불안정(webkit `list-interactions` AND/OR — dev 에도 있는 P1 테스트 문제). 그 테스트는 dev 에서 고침:
검색 뒤 창이 닫히기 전에 강제 클릭하던 흐름을 "열림 확인 → 검색 → 닫기 → 닫힘 확인"으로, 검사 없던 테스트에 AND/OR 결과 검사 추가. 3개 브라우저 × 3회 반복 72 passed.
