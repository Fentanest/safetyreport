---
name: sr-web-contract-audit
description: Audit safetyreport routes, Jinja templates, DataTables bindings and mobile API contracts before and after changing the web UI.
---

# 웹 기능·계약 대조

먼저 `AGENTS.md`, `PROJECT_RULES.md`, `docs/design/feature-matrix.csv`, `docs/design/pilot-dom-contracts.md` 를 읽는다.

1. 기계 인벤토리를 뽑는다: `python3 scripts/dev/web_contract_inventory.py > .agent-runs/<task>/inventory.json`
   (라우트 method/path/파일:줄, 템플릿별 element id·JS 참조 id·fetch URL·form name·JS 함수).
   변경 전후 두 번 뽑아 사라진 id·엔드포인트 호출을 diff 한다.
2. 기능표·보고서에 인용하는 id 는 `python3 scripts/dev/web_contract_inventory.py --check-ids <id...>` 로 존재를 확인한다(MISSING 이면 쓰지 않는다).
3. 관련 template·router·service 를 실제로 열어 method, form field, query, DOM id/data-*, DataTables 옵션, modal, WS/API 의존을 `파일:줄` 로 기록한다.
   파일 존재만으로 기능을 확인했다고 쓰지 않는다.
4. 서버가 적용하는 필터(`?status`, `?fine`, `?agencyExact`, `dedupe` — `services/report_query_service.py`)와 JS 가 읽는 쿼리
   (`agency/person/car/law/location/open`)를 구분한다.
5. 시안 요소는 existing / proposal / excluded 로 분류한다. 코드 근거 없는 '신고하기', 전역 검색, 알림 센터, 일반 업로드는 구현하지 않는다.
6. `/api/v1/**` 는 API 키(`_require_api_key`)로, 웹은 세션으로 보호된다. 세션 예외(`_PUBLIC_PREFIXES`)를 "인증 없음"으로 오판하지 않는다.
7. 계약 회귀는 `tests/`(Python) 또는 `tools/web-tests/specs`(Playwright)에 테스트로 남기고 fixture 서버로 실행한다.

산출: 갱신한 기능표 행, 코드 근거, 회귀 케이스와 실제 결과, 미검증 항목. Gemini 가 맡으면 테스트·연관 코드 수정까지 할 수 있다.
