# 통계 카드 → 목록 정확 일치(기관 키·담당자) (2026-10-18)

## 배경·결정
통계 표는 기관을 **기관 집계 키**(`agency_key`, registry: 현행 코드·`(구)` 분기·`src:-:이름` 폴백)로, 담당자를 **정확값**으로 묶는다.
그런데 카드/행 → 목록은 기관·담당자 **이름 부분 일치**로 연다(모바일 분류별·전체 탭, 웹 담당자). 그래서
- `김민수` 카드 목록에 `김민수(교통과)`, `강서경찰서` 카드에 `서울강서경찰서 교통과` 가 섞이고,
- 이름이 같고 코드가 다른 기관(합성: 코드 9999991 1건 / 9999992 200건)이 한 목록에 섞이고(카드 1건 → 목록 201건),
- `(구)옛기관` 카드는 표시명 `(구)옛기관` 으로 검색해 Standalone 에서 0건이 된다.
Astra 교차 확인(`.agent-runs/release-review-1018/astra-cross.md`, `astra-recheck.md`) 지적. 사용자 결정(2026-10-18): 검색 계약에
**기관 키·담당자 정확 일치**를 넣어 고치고(2번 안), 구현 Astra · 검수 Opus · 최종 감사 뒤 서버 3.4.1 / 앱 2.3.1 로 출시.

원칙: **카드/행의 건수 = 열린 목록의 건수**(같은 연도·법규·답변 완료·대표건 조건에서). 정확 일치는 COUNT·LIMIT(페이지 나눔) **이전**에 적용한다.
조회 뒤 Dart predicate 로 거르지 않는다.

## 계약 (서버·모바일 공통)
- 검색 필터에 새 키(하위호환 추가):
  - `agencyKey`(string): 행의 기관 집계 키(통계 표와 **같은 함수**로 계산: 서버 `agency_registry.resolve_stats_agency(code, name)[1]`, 모바일 `registryKeyedAgency(code, name).key`)가 같을 때만 일치.
    `agencyKey` 가 있으면 `agency`(이름) 조건은 보내지 않는다/무시한다(`(구)` 표시명 불일치 방지). 서버 `report_query_service` 의 기존 웹 `agencyKey` 처리와 같은 의미.
  - `managerExact`(bool): true 면 `manager` 를 부분 일치 대신 **정확 일치**(양쪽 앞뒤 공백 제거·기존 문자열 정규화 규칙과 같게, 대소문자 규칙도 기존 검색과 같게). `,`/`&` 분해 없이 한 값.
- 서버 capability: `/api/v1/app/config` 의 `capabilities` 에 `report_search_identity_v1` 추가(`law_search_v2` 와 같은 방식, `services/client_report_search.py` 상수).
  `validate_filter` 가 두 키를 받는다(타입 검사). 기존 키·`search_version` 은 그대로(구앱 영향 없음).
- 모바일: 서버가 `report_search_identity_v1` 를 알릴 때만 Client 검색에 두 키를 보낸다. 모르는 동안·구서버는 지금처럼 이름 부분 일치 조건으로 연다
  (`ReportProvider` 의 `law_search_v2` 처리 방식을 따른다). Standalone 로컬 검색은 항상 지원.

## 서버
- `services/client_report_search.py`: validate·compile_predicate 에 `agencyKey`·`managerExact`. `report_query_service.get_report_page` 의 search_filter 경로에서 COUNT·페이지 전에 적용되는지 확인.
- 웹 통계 drilldown(`web/templates/stats.html` 의 `drill_base`/행 링크, `web/routers/data.py` `_identity_filters`, `services/report_filter_spec.py`): 이미 `agencyKey` 를 보낸다.
  담당자 행 링크에 `personExact=true` 를 더하고 목록 필터가 담당자를 정확 일치로 거르게 한다(웹도 같은 원칙). 기존 링크 파라미터는 유지.
- 웹 통계 화면의 다른 목록 링크(요약 카드·법규 막대 등)는 바꾸지 않는다.

## 모바일
- `lib/models/report_filter.dart`: `agencyKey`(String, 기본 ''), `managerExact`(bool, 기본 false) 추가 — equals/hashCode/toJson/copyWith/isEmpty 등 기존 필드 패턴 전부.
  검색 시트·공용 필터 UI 에는 노출하지 않는다(드릴다운 전용). 필터 칩/제목 표시가 있으면 기관 표시명으로 보이게.
- 로컬 검색(`lib/services/local_db_service.dart` 의 목록·건수 조회, Astra 가 지적한 2374행 부근 기관 표시명 검색 포함): 두 조건을 COUNT·LIMIT 전에 적용.
  SQL 로 바로 못 하는 registry 키 판정은 기존 기관 표시명 검색이 쓰는 방식(후보 → 정확 판정) 중 **건수·페이지가 정확한 경로**를 쓰거나, 정확한 경로를 새로 만든다.
  "후보 N건 / 표시 M건" 처럼 건수가 어긋나는 경로로 보내지 않는다.
- Client 검색 요청(`ServerContract`, 검색 요청 생성부): capability 있을 때만 두 키 전송.
- 통계 카드 → 목록(`lib/screens/statistics_screen.dart` `_RowCard._openList`): 분류별 탭(`pushReportDrillDown`)과 전체 탭(`FilteredListScreen(category:'all')`) 모두
  `agencyKey: row.agencyKey`(있을 때), `managerExact: showPerson`, 기존 연도·법규·답변 완료 조건. 키가 없거나(구서버 응답) capability 가 없으면 지금처럼 이름 조건.
- 지도·대시보드 등 다른 드릴다운은 바꾸지 않는다.

## 테스트
서버: validate/compile(키·타입·알 수 없는 키 거절 유지), 같은 이름 다른 코드 기관(9999991 1건 / 9999992 200건) → agencyKey 목록 1건·1페이지,
`(구)` 기관 키 → 1건, `김민수` vs `김민수(교통과)` managerExact, 페이지 경계(>200 후보), capability 광고, 웹 담당자 행 링크 `personExact` 와 목록 건수 = 표 건수.
모바일: ReportFilter 직렬화·동등성, 로컬 검색 위 세 사례(건수·첫 페이지 정확), Client 요청에 capability 있을 때만 키 포함·없으면 이름 조건(구서버 응답 mock),
통계 카드(분류별·전체) → 목록 건수 = 카드 건수(기존 `test/widgets/stats_all_drilldown_count_test.dart`·`report_list_drilldown_test.dart` 패턴, 통계 위젯 시험은 파일당 하나 — 정적 대기열이 가짜 시간 영역을 못 넘는다),
서버↔모바일 검색 동등성 벡터가 있으면 두 키 사례 추가.
실행: 서버 `SAFETYREPORT_DATA_DIR=$(mktemp -d) /home/better0101/projects/safetyreport/.venv/bin/python -m unittest discover -s tests -p "test_*.py"`(이제 우회 보조 불필요),
모바일 `~/development/flutter-3.47.5/bin/flutter test`, `flutter analyze`. 샌드박스로 막히면 보고서에 적어라(Opus 가 실행).

## 문서
서버·모바일 `docs/architecture/data-contracts.md`(검색 계약·capability), 서버 `docs/architecture/web-ui.md`(통계 drilldown), 양쪽 `CHANGELOG.md`.
릴리스 노트(서버 `docs/releases/v3.4.1.md` 는 Opus 가 쓴다 — 건드리지 마라. 모바일 `docs/releases/unreleased.md` 에 한 줄 추가는 해도 된다).
