# 통계 "전체" 분류 탭 (2026-10-18)

## 배경·결정
사용자: 통계 법규 필터에서 `장애인·노인·임산부 등의 편의증진 보장에 관한 법률 제17조제4항`(불법주정차 1건)이 안 보였다.
원인은 법규 목록이 **선택한 분류 탭별**이고 기본 탭이 교통위반이어서다(설계대로). 사용자 결정(2026-10-18):
- 통계에 **전체** 분류 탭을 추가하고 **기본 탭으로** 한다(맨 앞). 마지막에 고른 탭을 기억하는 기존 동작(sessionStorage 등)은 유지한다.
- 서버 웹 + 모바일 앱 + 모바일 API 를 함께 바꾼다(서버·모바일 동작 1:1 규칙, `PROJECT_RULES.md`).
- 구현 Astra, 검수 Opus.

이미 있는 것(코드 확인):
- 요약(overview)은 이미 `all` 을 계산한다(`services/stats/overview.py` `payload["all"]`, `/api/v1/stats/overview`). 모바일 요약 모델도 확인하라.
- 지도는 `category=all` 을 지원한다(`/stats/map/points`, `services/stats/reads.py` `_load_map_records_frame`).
- 목록 이동 대상: 웹 `/data/all`(`web/routers/data.py`), 모바일 `FilteredListScreen` 의 `'all'`.

## 의미(두 레포 공통, 바꾸지 말 것)
- `all` = 5분류(taxonomy=3 이면 3분류) 프레임을 **대표건 projection·행 필터·취하 제외·법규 필터를 다른 탭과 똑같이 적용한 뒤** 합친 행 집합.
  신고는 정확히 한 분류에만 속하므로 **all 건수 = 분류별 건수의 합**이어야 한다.
- 기관별·담당자별·법규별 표: 합친 행으로 **다시 집계**한다(분류별 줄을 더해 붙이지 않는다). 같은 기관 키는 한 줄. 경찰/비경찰 부분집합 규칙 동일.
  불변식: all 표의 기관 키별 건수 = 각 분류 표의 같은 키 건수의 합. 금액(확정·추정 각각)·처분별 건수도 같은 방식.
- 분류에 따라 달라지는 계산(처분 분류, 추정 과태료, 처분 대상 판정 등)은 **행의 실제 category/entry_value** 로 판정한다. 탭 이름 `all` 로 판정하지 않는다.
  `_build_stats_tables(df, category)` 등 category 인자를 받는 함수를 전부 확인하고, `all` 에서 결과가 분류별 합과 어긋나지 않게 한다.
- 확정 과태료와 추정 과태료는 전체 탭에서도 **따로** 합산·표시한다(PROJECT_RULES 3-2). 합친 한 숫자를 만들지 않는다.
- `available_laws`(법규 목록)·`has_empty_law`: 합친 행 기준(= 분류별 목록의 합집합).
- 상태 raw/canonical, lifecycle, 중복군 축을 섞지 않는다(PROJECT_RULES 3).

## 서버
- `services/stats/agency.py` `_compute_agency_stats` 결과에 `all` 추가(위 의미). 캐시 키·직렬화(`_sanitize_jsonable`) 확인.
- `web/routers/stats.py`: 템플릿 컨텍스트에 `all` 표·법규 목록·has_empty_law 추가(기존 키 패턴을 따른다), `table_rows`/`pane_rows` 에 `all`.
- `web/templates/stats.html`: 분류 버튼 맨 앞에 **전체**(아이콘은 기존 Font Awesome 중 적당한 것, 예: `fa-layer-group`), 상세 표 pane 6개, `statsData.laws/hasEmptyLaw` 에 `all`.
  표 행 이동 `data-href` 는 `'/data/' ~ cat` 규칙대로 `/data/all` 이 되게. CSV 파일명 분류 라벨 '전체'.
- `web/static/ui/stats.js`: `CAT_LABELS` 에 all, 기본 `currentCat` = `'all'`(저장값 없을 때), 저장값이 알 수 없는 값이면 `'all'`.
  요약 카드·추이·처분·유형·지도(`loadMap('all')`)가 overview.all·지도 all 을 쓰는지 확인. 교통 전용 문구(예: 과태료 배너)가 전체 탭에서 틀린 의미가 되지 않게.
- `/api/v1/stats`(`web/routers/api_route.py`): 응답 data 에 `all` 키를 **추가만** 한다(기존 키·필드 그대로). taxonomy=3 경로도 `all` 추가(3분류 합).
- 기존 계약: 웹 DOM id(`#statsLawSidebar` 등)·`/api/v1/**` 필드 제거·변형 금지.

## 모바일 (`/home/better0101/projects/safetyreport-mobile-wt-statsall`, 브랜치 stats-all-tab)
- `lib/screens/statistics_screen.dart`: `_cats` 맨 앞 `('all', '전체')`, 기본 `_cat = 'all'`. 분류 색(`context.sr.category`)에 all 이 없으면 중립 토큰 사용.
- 로컬 모드 통계 계산(`lib/services/local_db_service.dart` 의 통계 함수, 4581행 부근 `categories`)에 `all` 을 서버와 같은 규칙으로 추가.
- 클라이언트 모드(서버 API): 응답에 `all` 이 있으면 사용. **구버전 서버(응답에 all 없음)** 면 전체 칩을 숨기고 기본을 교통위반으로(오류·빈 화면 금지).
- 법규 목록·표·목록 이동(`FilteredListScreen` category 'all')·지도 열기(분류 all) 연결.
- 통계 요약은 이미 all 이 있으면 재사용.

## 테스트
서버(`tests/`): `all` 건수 = 분류 합, 기관·담당자·법규 표 키별 합 불변식, 확정·추정 금액 분리 합, available_laws 합집합(불법주정차에만 있는 법이 all 목록에 있음),
법규 필터 적용 시 all 도 같은 판정, raw/canonical 두 모드, taxonomy=3 API all, `/api/v1/stats` 기존 키 보존(추가만), 웹 렌더에 전체 버튼·pane·`/data/all` 링크·기본 탭.
모바일(`test/`): 로컬 계산 all 불변식, 서버↔모바일 통계 동등성(`test/tool/logic_parity_harness_test.dart` 등 기존 동등성 벡터)에 all 추가, 구버전 서버 응답(all 없음) 처리, 화면 위젯 테스트(전체 칩 기본 선택).
기존 테스트 기대값은 이 설계로 바뀌는 것만 고치고 이유를 보고서에 표로.

### 실행
- 서버: `PYTHONPATH="$PWD/.agent-runs/pipe-wakeup" SAFETYREPORT_DATA_DIR=$(mktemp -d) /home/better0101/projects/safetyreport/.venv/bin/python -m unittest discover -s tests -p "test_*.py"`
  (이 PC 의 Python 3.14 `_asyncio` 가 설치 불일치로 세그폴트한다. `pipe-wakeup/sitecustomize.py` 는 순수 Python asyncio 로 우회하는 시험 전용 보조이며 제품·시험 코드를 바꾸지 않는다.)
- 모바일: Flutter 는 `~/development/flutter-3.47.5/bin/flutter`(전역 flutter 금지). `flutter test`, `flutter analyze`. 샌드박스 때문에 실행이 막히면 보고서에 적어라(Opus 가 실행한다).

## 문서
- 서버 `docs/design/statistics-spec.md`(전체 탭 의미·불변식), `docs/architecture/data-contracts.md`(`/api/v1/stats` 의 `all` 추가), `CHANGELOG.md`.
- 모바일 `docs/architecture/data-contracts.md` 또는 해당 통계 문서, `CHANGELOG.md`. 기존 형식을 따른다.
