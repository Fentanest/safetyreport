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

## 5. 후속 A — 모바일 주소 그룹 수를 서버와 같게 (2026-10-05 사용자 지시)
- 정의(서버 기존 정의가 정본): `address_groups` = 유효(effective) 좌표가 있는 신고의 서로 다른 (위도, 경도, 주소키) 조합 수. 주소키가 빈 신고도 (위도, 경도, '') 로 센다.
  서버 `services/stats/map.py` 의 `geocoded_df[['위도','경도','주소키']].drop_duplicates()` 와 같다. pin_basis=address 에서는 effective 좌표로 센다.
- 모바일 `computeReportMapStats` meta 의 `address_groups` 를 이 정의로 바꾼다(지금은 주소키만 센다).
- `contracts/map-pin-basis-vectors.json` 의 expected 에 `address_groups`(coords 6, address 4) 를 넣었다. 양쪽 테스트가 이 값을 확인한다.

## 6. 후속 B — 지도 묶음 원 이름 (2026-10-05 사용자 지시: "'영역 집계'라고 표시하면 어떡해")
- 서버 공간 칸 묶음 점(`cluster: true`)의 `region`/`address` 고정 문구 '영역 집계'/'이 영역의 신고' 를 없앤다.
  정본 규칙·벡터: `contracts/map-cluster-label-vectors.json`(두 레포 바이트 동일). 점에 `address_count` 를 추가(하위호환 추가 필드).
  - `region`(말풍선 제목) = 주소 0곳 '주소 정보 없음' / 1곳 대표 주소 / 2곳 이상 '{대표 주소} 외 {N-1}곳'.
  - `address` = 대표 주소(규칙은 벡터 description).
- 웹 말풍선(`web/static/ui/report-map.js` 툴팁·팝업)에서 묶음 점(`point.cluster`)은 제목만 보이고 주소 부제 줄은 보이지 않는다. 사용자가 '가까운 주소 N곳을 한 원으로 묶었습니다 · 확대하면 나뉩니다' 같은 안내 부제는 빼라고 했다.
  묶음 팝업 하단 문구 '확대하면 이 영역의 주소별 신고를 볼 수 있습니다.' 는 '확대하면 주소별 신고를 볼 수 있습니다.' 로('영역' 단어 제거).
  Leaflet 클라이언트 묶음 요약(`report-map-calc.js` summarizeClusterRegions)이 서버 묶음 점의 새 region('… 외 N곳')을 행정구역처럼 섞어 이상한 제목을 만들지 않는지 확인하고, 필요하면 서버 묶음 점은 region 대신 address 를 쓰게 한다.
- 모바일 Standalone 칸 점(`_MapCellAccumulator.toJson`)도 같은 규칙으로 `address`/`address_count`/`region` 를 낸다(지금은 MIN(위반장소)·COUNT(DISTINCT 위반장소)).
  모바일 지도 화면은 묶음 점을 누르면 확대하는 기존 동작과 마커 라벨('N건 묶음')을 그대로 둔다. 새 region 문자열이 마커 라벨(`mapMarkerRegionLabel`)로 새어 나가 라벨이 바뀌지 않는지 확인한다.

## 7. Sol 1차 검수 반영 기준 (2026-10-05)
- 주소키 정규화는 한 가지: 앞뒤 유니코드 공백 제거(Python `str.strip()`, Dart `String.trim()`). 모바일은 핀 기준·주소 그룹 수·묶음 이름·좌표 없는 목록 계산에서 SQLite `trim()` 결과를 주소키로 쓰지 않는다(탭·줄바꿈·NBSP 등 포함 시 서버와 달라짐).
- 문자열 동률 비교(대표 주소키, 대표 표시 문구)는 **유니코드 코드포인트 순서**. Python 기본 비교와 같다. Dart 는 `compareTo`(UTF-16) 대신 runes 비교를 쓴다.
- `address_groups` 는 숫자 좌표 그대로(실수 문자열 변환 없이) 서로 다른 (위도, 경도, 주소키) 수.
- 모바일 Standalone 은 기존부터 화면 칸(cell) 단위로 점을 묶는다(서버는 1200점 초과 때만). 공용 벡터 점 집합 비교는 **벡터 전체를 담는 bounds 하나·최대 확대(zoom 19)** 한 번의 조회로 한다. 이 조건에서 모바일 점 집합이 서버 점 집합과 같아야 한다(좁은 bounds 를 점마다 따로 조회하는 우회 금지).
- 모바일 묶음 판정(cluster)은 핀 기준 도입 전과 같은 의미를 지킨다: 한 칸 안에 서로 다른 좌표 또는 서로 다른 주소키/표시 문구가 섞이면 묶음. SQL 그룹을 주소키로 더 잘게 나눠도 칸 단위로 다시 합쳐 판정한다.
- Client 모드에서 주소 기준을 요청했는데 응답 `meta.pin_basis` 가 `address` 가 아니면(구 서버) 토글을 위도·경도로 되돌리고 "서버를 업데이트해야 주소 기준을 쓸 수 있습니다" 안내를 보인다.
- 모바일 주소 모드의 대표 좌표는 DB revision·조회 모집단(연도·분류·중복·철회 제외) 단위로 캐시하고, 화면 이동마다 전체를 다시 계산하지 않는다. OFFSET 순회 대신 키셋 순회.
