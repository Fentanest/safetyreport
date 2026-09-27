# 2026-09-28 통계 화면 개편 검수 기록

대상: 서버 `feat/stats-renewal` f5e4e35(+후속 수정), 모바일 `feat/stats-renewal` 1063ef7e(+후속 수정). 구현 Opus.

## Gemini(agy) — blocked
- 작업서 `.agent-runs/stats-review/TASK.md`(로컬), `agy 1.2.11 --model gemini-3.1-pro-high` 실제 실행.
- 결과: `RESOURCE_EXHAUSTED (code 429): Individual quota reached … Resets in 56h42m` — 검수 산출물 없음. 사후 파일 검사에서 두 worktree 변경 없음. 재시도 중이던 프로세스는 종료했다.

## 대체 독립 검수: Codex(openai-codex 플러그인, 읽기 전용)
| # | 심각도 | 발견 | 판정 | 조치 |
|---|---|---|---|---|
| 1 | High | `/stats?dedupe=raw` 일 때 작은 지도·전체 지도는 설정 기본값 모드를 써서 요약과 모집단이 다름 | 수용(재현 가능) | `/stats/map`·`/stats/map/points`·`/stats/map/missing` 이 `dedupe` 를 받고, 통계 화면이 주소의 `dedupe` 를 지도로 넘긴다. Playwright `a dedupe choice … carries over to the map population` |
| 2 | Medium | 지도 팝업 '리스트 보기'가 연도·날짜 조건을 잃음 | 수용 | 전체 지도·작은 지도 모두 법규·답변 연도(→답변일 범위)·상세 날짜/신고명 조건을 목록 주소에 붙인다 |
| 3 | Medium | 모바일에서 조건을 바꾸면 새 요약이 올 때까지 이전 조건 요약이 보임 | 수용 | 로드 시작 때 요약을 비우고 '불러오는 중' 안내 |
| 4 | Low | 지도 마커·클러스터·팝업 강조색이 고정 hex | 수용 | 과태료 비율 구간을 `--sr-status-accept/partial/reject` 토큰(color-mix)으로, 클러스터는 `--sr-primary-fill` |
Codex 가 확인한 항목: 요약·차트가 서버 결과만 사용, 보기 전환이 요약을 바꾸지 않음, 확정/추정/금액 미확인 분리(화면·CSV), CSV 전체 행·수식 방지, 답변월 기준·처리율 없음·미래 달 없음, 도넛 없음, 이름 출력 이스케이프, 응답 역전 방지. 서버 단위 테스트는 Codex 샌드박스(읽기 전용)에서 실행 불가 — Opus 가 실행(아래).

## Opus 실행 증거
- 서버 단위 테스트 전체 통과, 신규 `tests/test_stats_overview_vectors.py`. `scripts/dev/logic_parity_check.py --mobile-repo ../safetyreport-mobile-wt-stats` 48개 조합 diff 0.
- Playwright(검수용 로컬 서버 — 커뮤니티 게이트 판정만 통과시킴, 관리자 로그인은 fixture 계정): 통계·지도·대시보드 스펙 Chromium·Firefox·WebKit 각 35건 통과(수정 뒤 Chromium 41건 재통과).
- 모바일: 비골든 전체 통과, 실제 폰트 전체 화면 렌더 Standalone·Client 각 8개 시나리오 렌더 예외 0.
