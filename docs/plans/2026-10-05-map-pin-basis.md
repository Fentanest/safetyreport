# 신고 지도 핀 기준 스위치(위도·경도 / 주소) — 2026-10-05

사용자 요구: "신고 지도 탭에 토글 메뉴 하나 놓고 '위도경도/주소 기반' 스위치를 놓고 지도에 핀을 위도경도로 찍거나 주소로 찍거나를 선택".
모바일도 함께(서버·모바일 동작 일치). 좌표는 이미 안전신문고 상세에서 신고별로 수집한 **공식 좌표만** 쓴다. 외부 주소 변환(카카오 등)·지오코딩 캐시는 쓰지 않는다.

## 1. 의미
- 주소키 = `trim(주소정규화)`, 비면 `trim(위반장소)` (기존 `services/stats/reads.py` 의 `주소키`, 모바일 `COALESCE(NULLIF(trim(주소정규화),''),trim(위반장소))` 와 같음).
- `pin_basis=coords`(기본): 지금 동작 그대로. 각 신고의 공식 위도·경도.
- `pin_basis=address`: 주소키가 있는 신고는 **같은 주소키 신고들(현재 조회 모집단: 연도·분류·중복 모드·필터 적용 뒤) 의 유효 공식 좌표 중 가장 많이 나온 (위도,경도) 쌍**에 찍는다.
  동률이면 위도가 작은 쌍, 그다음 경도가 작은 쌍. 자기 좌표가 없어도 같은 주소키에 유효 좌표가 하나라도 있으면 그 좌표에 찍는다.
  주소키가 빈 신고는 자기 좌표 그대로. 같은 주소키에 유효 좌표가 하나도 없으면 좌표 없음.
- "유효 좌표" 판정은 각 플랫폼의 기존 판정을 그대로 쓴다(바꾸지 않는다).
- 이 계산은 **표시용**이다. DB 의 위도·경도·지오코딩상태, DB 교환·백업·CSV·커뮤니티 업로드는 바꾸지 않는다.
- 유효 좌표 판정 뒤의 모든 단계(geocoded_reports, missing_reports, 화면 범위 bounds 거르기, 점/클러스터 묶기, 좌표 없는 신고 요약·목록)는 **유효(effective) 좌표 기준**으로 한다.
  그래서 주소 모드에서는 좌표 없는 신고 목록에 "같은 주소 어디에도 좌표가 없는" 신고만 남는다.
- 정본 벡터: `contracts/map-pin-basis-vectors.json`(서버·모바일 바이트 동일). 양쪽 테스트가 이 파일로 effective 좌표, geocoded/missing 건수, 점 집합(lat,lng,total), 좌표 없는 그룹 수·건수를 확인한다.

## 2. 서버(safetyreport)
- 순수 함수 하나로 frame 의 위도·경도·유효좌표를 effective 로 바꾼다(예: `services/stats/` 안 `apply_pin_basis(frame, basis)`). `_load_map_records_frame` 뒤 지도 3함수
  (`get_report_map_stats`, `get_report_map_missing_summary`, `get_report_map_missing_groups`)가 `pin_basis` 키워드(기본 `"coords"`)를 받아 이 함수를 거친다. 값 정규화: `address` 외 모두 `coords`.
- `get_report_map_stats` meta 에 `pin_basis` 추가(하위호환 추가 필드).
- 경로(모두 선택 쿼리 `pin_basis`, 없으면 coords — 기존 응답과 동일):
  웹 `/stats/map`, `/stats/map/points`, `/stats/map/missing`; API v1 `/api/v1/stats/map`, `/api/v1/stats/map/points`, `/api/v1/stats/map/missing`.
  `@cached` 키는 kwargs 를 포함하므로 모드별로 따로 캐시된다.
- 웹 화면 `web/templates/report_map.html`: 기존 분류 버튼 묶음(`#mapCategoryGroup`)과 같은 모양의 2버튼 묶음 `#mapPinBasisGroup`
  ("핀 기준" 라벨, 버튼 "위도·경도"(data-pin-basis=coords) / "주소"(data-pin-basis=address), `aria-pressed`). 누르면 기존 `updateQuery` 로 `pin_basis` 쿼리를 바꿔 다시 읽는다
  (coords 면 쿼리에서 빼기). 지도 정보 칩에 현재 기준 표시. viewport 갱신 URL·좌표 없는 신고 모달 fetch 는 이미 `window.location.search` 를 붙이므로 따라온다.
  주소 모드일 때 짧은 안내 문구: "같은 주소의 신고를 한 핀으로 묶고, 그 주소에서 가장 많이 신고된 공식 좌표에 표시합니다." 테마 토큰·기존 CSS 를 쓰고 새 색을 만들지 않는다.
  통계 화면의 작은 지도(`/stats`)는 바꾸지 않는다(기본 coords).
- 테스트: `tests/test_map_pin_basis.py` — 벡터로 함수 단위 + fixture DB 로 `get_report_map_stats`/missing 2함수(양 모드), 웹·API 경로가 `pin_basis` 를 전달하는지, 쿼리 없으면 기존과 같은지.
- 문서: `docs/architecture/data-contracts.md`(API 쿼리·meta 필드), `docs/architecture/web-ui.md`(지도 스위치), `contracts/selfhost-compat/README.md`(points 예시 옆에 pin_basis 한 줄), `CHANGELOG.md`.

## 3. 모바일(safetyreport-mobile)
- `LocalDbService.computeReportMapStats` / `computeReportMapMissingGroups` 에 `pinBasis`(기본 coords). 주소 모드 계산은 서버와 같은 규칙(벡터로 검증). 캐시 키(metaKey/cacheKey)에 pinBasis 포함.
  SQL 로 하든 Dart 로 하든 결과가 벡터와 같아야 한다. 위도·경도 원값은 바꾸지 않는다.
- Client 모드: `ApiService.getReportMapStats`/`getReportMapMissingGroups` 에 `pin_basis` 쿼리 추가(coords 면 생략해도 됨). 서버 경로 상수는 그대로.
- 지도 화면 `report_map_screen.dart`: 기존 연도·분류 선택과 같은 방식·위치에 "핀 기준" 토글(위도·경도 / 주소). 바꾸면 지도·좌표 없는 신고 목록을 다시 읽는다.
  유지 방식은 화면의 기존 분류 선택과 같은 방식을 따른다. 위젯 테스트가 있으면 토글 하나 추가.
- `contracts/map-pin-basis-vectors.json` 을 서버와 바이트 동일하게 복사하고 테스트(`test/map_pin_basis_test.dart` 등)로 Standalone 계산을 검증.
- 문서: 모바일 `docs/architecture/data-contracts.md`(있으면), `CHANGELOG.md`.

## 4. 범위 밖 / 금지
- 외부 주소 변환, `mysafety_geocode_cache` 사용, DB 스키마 변경, 저장 좌표 수정, VERSION·push·태그. 기존 address_groups 정의 차이(서버 triple vs 모바일 주소키)는 이번에 고치지 않는다(보고만).
