---
name: sr-statistics-semantics
description: Implement and verify safetyreport statistics (monthly trends, result distribution, agency comparison) with the existing aggregation rules and deterministic fixtures, never mockup numbers.
---

# 통계 의미·집계 검증

먼저 `docs/design/statistics-spec.md`, `services/report_stats_service.py`, `web/routers/stats.py`, `services/duplicate_group_service.py` 를 읽는다.

1. 지표마다 데이터셋·날짜 기준(신고일/답변일)·분모·유효표본·raw/canonical·취하 제외를 명시한다. 현재 "연도" 필터는 `답변일` 기준이다.
2. 월별 신고(신고일)와 월별 답변(답변일)을 다른 계열로 둔다. 현재 상태 snapshot 으로 "그 달 당시 상태" 추이를 만들지 않는다.
3. 중복차량을 네 번째 카테고리로 더하지 않고, Sunwi 전국 순위와 개인 기관 순위를 섞지 않는다.
4. 평균은 원자료 가중(기관 평균의 평균 금지), 미평가 별점은 0점이 아니다, 분모 0 은 "—", 미완결 월·미래 월을 0 으로 그리지 않는다.
5. 기대값은 `scripts/dev/fixture_server.py` 의 합성 데이터(24건)처럼 손으로 셀 수 있는 fixture 로 unittest 에 적는다. 시안 숫자·증감률 금지.
6. 차트·KPI·표·다운로드가 같은 집계 결과(같은 서버 함수 호출)를 쓰는지 테스트한다. 브라우저의 현재 페이지 50행으로 전체 통계를 계산하지 않는다.
7. 기존 상세표·열 선택(sessionStorage `stats_column_visibility`, 헤더 텍스트 키)·정렬·drilldown(`/data/<cat>?agency=..&agencyExact=true`)을 보존하고 API 는 추가형으로만 확장한다.
   모바일 `/api/v1/stats/overview`(feature 브랜치 진행 중)와 정의를 맞춘다.

산출: 지표 계약, fixture 기대값 테스트, 실제 차트·표 캡처, 기존 동작 대비 변경점.
