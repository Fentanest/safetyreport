# 2026-10-05 신고 지도 핀 기준(위도·경도/주소)·묶음 원 이름 — GPT-6.1-Sol 검수 기록
범위: safetyreport(PC)·safetyreport-mobile, 브랜치 `feat/map-pin-basis`. 명세: `docs/plans/2026-10-05-map-pin-basis.md`.

검토자: GPT-6.1-Sol(codex exec, read-only, effort high). 구현: 1차·후속은 Muse(OpenCode), Sol 1차 반영부터는 Opus 직접(사용자 지시 "수정 너가 해").
각 차수의 원문을 아래에 그대로 남긴다.

| 차수 | 대상 | 결과 | 반영 |
|---|---|---|---|
| 1차 | PC 0a8bc8e · 모바일 cdf93910 | 높음 0·중간 6(모두 모바일)·낮음 2 | PC 306d59f · 모바일 43228e06 |
| 2차 | 위 반영 | 1차 7건 해결·1건 부분, 새 높음 0·중간 4·낮음 1 | PC f62dadb · 모바일 a5f0f304 |
| 3차 | 위 반영 | 2차 3건 해결·2건 부분, 새 높음 0·중간 4·낮음 2 | 아래 표(모바일만) |

### 1차 반영

| 지적 | 처리 |
|---|---|
| 중간1 SQLite trim 이 탭·NBSP 를 남겨 주소 모드에서 신고 누락 | 모바일: 신고별 effective 표를 Dart trim 으로 만들고 ID 조인. 서버는 이미 `str.strip()`. 양쪽 경계 시험 추가 |
| 중간2 주소키로 SQL 그룹이 나뉘어 묶음 판정이 깨짐 | 모바일: 칸 전체 좌표 범위·장소 문구로 판정. 기존의 처리상태별 그룹 분리 누락도 함께 바로잡음 |
| 중간3 실수 좌표를 문자열로 세어 address_groups 과소 | 모바일: `SELECT DISTINCT lat, lng, addr_key` 숫자 비교. 양쪽 시험 |
| 중간4 구 서버가 쿼리를 무시해도 주소 모드 표시 | 모바일: 응답 `meta.pin_basis` 확인 → 토글 복원·업데이트 안내. 위젯 시험 |
| 중간5 점 집합 시험이 좁은 bounds 로 우회 | 명세 §7: 모바일 칸 표시는 기존 설계로 두고, 칸 직전 (위도,경도,주소키) 건수를 한 번에 읽어 비교 |
| 중간6 화면 이동마다 전체 모집단 재계산·OFFSET 순회 | 모바일: revision·모집단·기준별 표 캐시, 스냅샷 rowid 키셋 |
| 낮음1 UTF-16 vs 코드포인트 동률 | 모바일 `compareCodePoints`. 시험 U+F900 vs U+20000 |
| 낮음2 서버 경로 시험이 실제 전달을 검증 안 함 | 서버: 웹 3·API 3 경로 실제 호출로 전달 확인, 전달 제거 시 실패 확인 |

### 2차 반영

| 지적 | 처리 |
|---|---|
| 중간1 NULL·빈 ID 신고가 지도에서 빠짐(1차 반영 회귀) | 모바일: effective 표에 칸 집계 열을 함께 담고 이 표만 읽는다(ID 조인 없음). 시험 |
| 중간2 coords 조회가 전량 행·ID 맵을 메모리에 적재(1차 반영 회귀) | 모바일: 표 생성·대표 좌표를 모두 SQLite 안에서(CREATE AS SELECT, 건수 표 + NOT EXISTS, UPDATE) |
| 중간3 주소 모드 좌표 없는 목록이 내부 공백을 합쳐 다른 키 병합 | 모바일: 주소 모드 목록은 e.addr_key 로 묶음. 시험 |
| 중간4 Python strip 과 Dart trim 문자 집합 차이 | 정본 집합을 공용 벡터 `strip_code_points`·`key_cases` 로 명시, 모바일 SQLite·Dart 가 같은 집합. 조사 중 Dart UTF-8 디코더의 맨 앞 BOM 제거를 확인해 키 비교·재조회는 hex 로 |
| 낮음1 pin_basis 정규화 차이 | 모바일도 앞뒤 공백·대소문자 무시. 시험 |

### 3차 반영

| 지적 | 처리 |
|---|---|
| 중간1 NULL ID 신고가 주소 모드 좌표 없는 목록에서 빠짐 | 고치지 않음: 실데이터에 NULL ID 없음(크롤러가 항상 채움, 사용자 확인), 기존 목록도 ID 조인 |
| 중간2 대표 좌표 UPDATE 가 신고마다 후보 재탐색(5만 건 미완료) | 주소별 승자를 GROUP BY 3단계로 한 번 계산 + 고유 인덱스 적용. 한 주소 5천 좌표 시험 |
| 중간3 주소 모드 '전체 신고 보기' 빈 목록(U+001C) | 첫 행으로 기존 목록 키 계산. 시험 |
| 중간4 빈 장소 먼저면 묶음 누락, BOM 문구 비교 | 장소 문구 hex 비교·빈 문구도 값으로. 시험 |
| 낮음1 Client 정규화 차이 | `map_pin_basis.dart` 공용 규칙을 Client·Standalone 이 사용 |
| 낮음2 BOM 키가 hex 로 노출 | 표시문구는 원문만 사용 |

실DB 대조(사용자 PC dev DB 사본, 외부 전송 없음): 서버·모바일 meta·신고별 effective 묶음 2,353 / 781개 전부 일치(3차 반영 뒤 재확인).

## 1차 원문

## 높음

없음.

## 중간

1. **주소 공백 처리 차이로 유효한 신고가 지도에서 누락됩니다.**  
   [모바일 local_db_service.dart:2773](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2773), [2958](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2958)  
   입력: A는 주소정규화 `"\t서울 중구\t"`·좌표 `(37.5,127)`, B는 `"서울 중구"`·좌표 NULL. 주소 모드에서 서버와 Dart 순수 계산은 둘 다 같은 핀에 표시합니다. 실제 모바일 SQL은 SQLite `trim()`이 탭을 제거하지 않아 A의 대표 좌표 조인에 실패합니다. 결과는 **좌표화 2건→1건, 미좌표 0건→1건**입니다. A는 원래 좌표가 있어 미좌표 목록에서도 빠집니다.  
   수정 방향: 대표 좌표 계산·SQL 조인·집계에서 동일한 주소키 정규화를 사용하고, 탭·줄바꿈·Unicode 공백을 실제 DB 테스트에 추가하십시오.

2. **주소키별 SQL 그룹 분리 후 묶음 판정이 깨집니다.**  
   [모바일 local_db_service.dart:3050](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:3050), [local_statistics.dart:316](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_statistics.dart:316)  
   입력: 같은 칸에 장소 문구는 둘 다 `"서울 중구"`, 주소정규화는 각각 A/B, 좌표는 `(37.5,127)`과 `(37.6,127.1)`, 나머지 집계 조건은 동일. 변경 전에는 한 SQL 그룹의 좌표 범위가 달라 `cluster=true`였습니다. 변경 후에는 주소키별 그룹의 개별 범위가 같고 장소 문구도 같아서 **`cluster=false`**가 됩니다. 서로 다른 위치의 평균점이 일반 핀으로 취급되고, `2건 묶음` 라벨과 확대 동작도 사라집니다. 기본 coords 모드에서도 발생합니다.  
   수정 방향: accumulator가 SQL 그룹 사이의 좌표 범위와 주소키 차이까지 합쳐 묶음 여부를 판정해야 합니다.

3. **`address_groups`가 서로 다른 실수 좌표를 하나로 셉니다.**  
   [모바일 local_db_service.dart:2975](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2975)  
   입력: 동일 주소키, 경도 127, 위도 각각 `37.5`와 `37.50000000000001`. 서버는 서로 다른 triple **2개**, 모바일은 **1개**를 반환합니다. SQLite에서 실수를 문자열로 연결하면서 두 위도가 모두 `"37.5"`가 됩니다.  
   수정 방향: 문자열 연결 대신 숫자 좌표와 주소키를 그대로 `SELECT DISTINCT`/`GROUP BY`한 결과의 행 수를 세십시오.

4. **구 서버가 쿼리를 무시해도 주소 모드로 표시합니다.**  
   [모바일 api_service.dart:633](/home/better0101/projects/wt-mobile-pinbasis/lib/services/api_service.dart:633), [report_map_screen.dart:246](/home/better0101/projects/wt-mobile-pinbasis/lib/screens/report_map_screen.dart:246), [564](/home/better0101/projects/wt-mobile-pinbasis/lib/screens/report_map_screen.dart:564)  
   입력: 구 서버에 연결해 주소 토글 선택 → 서버는 `pin_basis`를 무시하고 기존 coords 응답을 반환. 모델의 `meta.pinBasis`는 coords가 되지만 화면은 이를 확인하지 않아 **주소 토글·주소 집계 안내와 실제 핀·미좌표 목록이 불일치**합니다.  
   수정 방향: 주소 요청에 대한 응답 기준을 확인하고, 미지원 서버에는 업데이트 안내와 함께 토글을 복원하거나 비활성화하십시오.

5. **점 집합 테스트가 정본의 전체 입력 비교를 우회합니다.**  
   [모바일 map_pin_basis_test.dart:177](/home/better0101/projects/wt-mobile-pinbasis/test/map_pin_basis_test.dart:177), [local_db_service.dart:3032](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:3032)  
   입력: 공용 벡터 전체를 bounds 없이 한 번 조회. 서버는 coords **6점**, address **4점**인데 모바일의 기본 32×32 셀 집계는 각각 **1점**으로 합칩니다. 테스트는 기대 점마다 좁은 bounds로 별도 조회해서 통과하며, 기대 밖의 점이나 전체 조회 불일치를 검출하지 못합니다. 기존 셀 집계 차이가 이번 정본 검증에도 남아 있습니다.  
   수정 방향: 동일한 조회 조건으로 전체 점 집합을 비교하십시오. 셀 집계 차이를 허용하려면 명세와 공용 벡터에 그 조건을 명시해야 합니다.

6. **주소 모드에서 viewport마다 전체 모집단을 다시 읽고 대표 좌표를 재계산합니다.**  
   [모바일 local_db_service.dart:2861](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2861), [2953](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2953)  
   입력: 신고 5만 건, 주소 모드에서 새로운 viewport로 이동. meta 캐시가 있어도 전체 행 수집·대표 좌표 계산·TEMP 테이블 삽입을 반복합니다. `LIMIT 2000 OFFSET` 순회는 26회 조회하며, 메모리 SQLite 재현에서 단일 조회보다 VM 연산이 약 **4.1배**였습니다.  
   수정 방향: DB revision·조회 모집단별 대표 좌표를 캐시하고, OFFSET 순회는 키셋 또는 스냅샷 커서로 바꾸십시오.

## 낮음

1. **Unicode 동률 주소의 대표 이름이 서버·모바일에서 다릅니다.**  
   [서버 map.py:56](/home/better0101/projects/wt-sr-pinbasis/services/stats/map.py:56), [모바일 local_db_service.dart:2755](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2755)  
   입력: 같은 건수의 주소키 `"서울 \uF900"`과 `"서울 \u{20000}"`. Python은 코드포인트 순서로 전자를, Dart `compareTo()`는 UTF-16 순서로 후자를 선택합니다. 대표 주소와 `외 1곳` 제목이 달라집니다.  
   수정 방향: 공통 정렬 기준을 명시하고, Dart의 주소키·표시문구 비교도 그 기준으로 통일하십시오.

2. **경로 전달 테스트가 실제 전달을 검증하지 않습니다.**  
   [서버 test_map_pin_basis.py:188](/home/better0101/projects/wt-sr-pinbasis/tests/test_map_pin_basis.py:188)  
   `pin_basis` 매개변수만 남기고 서비스 호출에서 전달을 제거해도 테스트는 통과합니다. 주소 요청이 coords로 처리되는 회귀를 잡지 못합니다.  
   수정 방향: 실제 경로 호출 후 서비스 인자 또는 응답의 effective 좌표·건수를 검증하십시오.

파일 변경은 없습니다. 양쪽 순수 함수의 기본 벡터, 메모리 SQLite 재현, JS 계산 검사를 실행했습니다. 전체 unittest·Flutter·브라우저 테스트는 실행하지 않았습니다.

## 2차 원문

**1차 지적 처리**

| 지적 | 판정 | 근거 |
|---|---|---|
| 중간1: 주소 공백 때문에 신고 누락 | **부분** | 탭·줄바꿈·NBSP 원 입력은 해결. 다만 §7의 동일 주소키 규칙은 목록·Unicode 경계에서 불완전함. 아래 중간 3·4 참조. |
| 중간2: 주소키별 분리로 묶음 판정 누락 | **해결** | [local_statistics.dart:319](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_statistics.dart:319)에서 칸 전체 좌표 범위를 누적. 처리상태가 다른 그룹도 묶음으로 판정함. |
| 중간3: 실수 좌표 그룹 과소 집계 | **해결** | [local_db_service.dart:3069](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:3069)의 숫자 `DISTINCT lat,lng,addr_key`. 근접 실수 시험 통과. |
| 중간4: 구 서버에서도 주소 토글 표시 | **해결** | [report_map_screen.dart:249](/home/better0101/projects/wt-mobile-pinbasis/lib/screens/report_map_screen.dart:249)의 sequence/epoch 검사 후 응답 기준 확인·토글 복원. 위젯 시험 통과. |
| 중간5: 좁은 bounds로 점 집합 시험 우회 | **해결** | [map_pin_basis_test.dart:179](/home/better0101/projects/wt-mobile-pinbasis/test/map_pin_basis_test.dart:179)에서 전체 effective 표를 한 번 조회하고, 전체 칸 집계의 건수 합도 검증함. §7 변경 기준 충족. |
| 중간6: viewport마다 전량 재계산·OFFSET 순회 | **해결** | [local_db_service.dart:2863](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2863)의 모집단·revision 캐시와 rowid 키셋. 다만 기본 coords의 메모리 회귀는 아래 별도 지적. |
| 낮음1: UTF-16 동률 순서 | **해결** | [local_db_service.dart:2713](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2713)의 코드포인트 비교. U+F900/U+20000 시험 통과. |
| 낮음2: 서버 경로 전달 시험 부족 | **해결** | [test_map_pin_basis.py:263](/home/better0101/projects/wt-sr-pinbasis/tests/test_map_pin_basis.py:263)에서 실제 경로 함수를 호출하고 서비스 호출 인자를 검사함. mock은 해당 전달 코드를 우회하지 않음. HTTP 라우팅·인증 통합시험은 아님. |

## 높음

새 높음 없음.

## 중간

1. **[이번 반영 회귀] NULL·빈 ID 신고가 기본 coords 지도에서도 조용히 사라집니다.**  
   [local_db_service.dart:2904](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2904), [3138](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:3138)  
   입력: 유효 좌표가 있는 신고 3건의 ID가 각각 `NULL`, `NULL`, `''`. 현재 `reports(ID TEXT PRIMARY KEY)` 스키마는 이 입력을 허용합니다. 새 effective 표는 세 행을 모두 건너뜁니다. **기존 coords 집계 3건 → total/geocoded/지도 점 모두 0건**이 됩니다. 순수 함수 재현에서도 effective 결과가 빈 맵입니다.  
   수정 방향: 행 식별자를 보존해 집계하거나, 허용하지 않는 ID를 명확한 오류로 처리하십시오. 조용히 제외하면 안 됩니다. 비어 있지 않은 ID 중복은 기존 PK·서버 가져오기 preflight가 차단합니다.

2. **[이번 반영 회귀] 기본 coords 조회가 전량 행·ID 맵을 UI isolate에 적재합니다.**  
   [local_db_service.dart:2873](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2873), [2888](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2888), [2892](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2892)  
   입력: 신고 5만 건, 첫 coords 조회 또는 revision 변경 뒤 조회. 2,000행씩 읽지만 전부 `rows`에 누적하고, `keys`·`ownValid`·`effective` 맵까지 만듭니다. **기존 페이지 단위 소비가 전량 보관으로 바뀌었습니다.** 해당 함수를 추출한 데스크톱 Dart 재현에서 행·맵 생성의 RSS 증가 약 **41.5MiB**, 동기 계산 **80ms**를 확인했습니다. 앱 전체·실기기 측정치는 아닙니다.  
   수정 방향: coords는 페이지를 바로 TEMP 표에 넣고 버리십시오. address도 좌표 빈도 집계·대표 좌표 적용을 SQLite에서 수행해 전량 ID 맵 보관을 제거하십시오.

3. **[§7 잔여] 주소 모드의 좌표 없는 목록이 내부 공백을 합쳐 다른 주소키를 병합합니다.**  
   [local_db_service.dart:3275](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:3275), [3349](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:3349), [geocode_utils.dart:20](/home/better0101/projects/wt-mobile-pinbasis/lib/services/geocode_utils.dart:20)  
   입력: 좌표 없는 두 신고의 주소정규화가 `"서울  중구"`와 `"서울 중구"`. 핀 기준 계산은 trim만 하므로 서로 다른 키지만, 목록은 `normalizeGeocodeAddress()`로 내부 공백을 합칩니다. **서버 2그룹·2건 → 모바일 address 목록 1그룹·2건**입니다. 새 effective 표와 조인한 뒤에도 그룹 기준은 `m.address_key`입니다.  
   수정 방향: address 목록은 `e.addr_key`로 그룹화·조회하십시오. coords 목록의 기존 동작은 별도로 유지할 수 있습니다.

4. **[§7 잔여] Python `strip()`과 Dart `trim()`의 문자 집합 차이로 대표 좌표가 달라집니다.**  
   [local_db_service.dart:2706](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2706), [reads.py:245](/home/better0101/projects/wt-sr-pinbasis/services/stats/reads.py:245)  
   입력: A의 주소키는 `"\uFEFF서울 중구"`·좌표 `(37.5,127)`, B는 `"서울 중구"`·좌표 없음. **서버는 별도 키로 처리해 좌표화 1건·미좌표 1건**, 모바일은 BOM을 제거해 **좌표화 2건·미좌표 0건**입니다. 반대로 앞의 U+001C는 Python만 제거하여 결과가 뒤집힙니다. 양쪽 런타임에서 재현했습니다.  
   수정 방향: 제거할 문자 집합을 명시하고 양쪽에 동일하게 구현하십시오. BOM·U+001C를 공용 경계 벡터에 추가해야 합니다.

## 낮음

1. **[전체 diff 잔여] 핀 기준 값 정규화가 양쪽에서 다릅니다.**  
   [map.py:11](/home/better0101/projects/wt-sr-pinbasis/services/stats/map.py:11), [local_db_service.dart:2701](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2701)  
   입력 `" ADDRESS "` → 서버는 `address`, 모바일은 `coords`. 현재 UI는 상수만 전달하므로 직접 영향은 작습니다.  
   수정 방향: 대소문자·앞뒤 공백 처리 기준을 통일하십시오.

검증: 공용 벡터 두 파일은 바이트 동일. 모바일 지정 시험 **30건**, 추가 snapshot·coords 의미·마커 라벨 시험 **22건 통과**. 서버 원 명령은 읽기 전용 임시파일 제한으로 blocked였고, 파일 DB fixture만 메모리 SQLite로 바꾼 실행은 **14건 통과**했습니다. 캐시의 닫기·DB 교체·롤백·동시 조회·취소와 토글 경합은 코드 대조했으며, 전용 실행 검증을 모두 수행한 것은 아닙니다. 저장소 파일 변경 없음.

## 3차 원문

**2차 지적 처리**

| 지적 | 판정 | 근거 |
|---|---|---|
| 중간1: NULL·빈 ID 신고 누락 | **부분** | 지도 건수·점은 effective 표에서 직접 집계해 해결. 주소 모드의 좌표 없는 목록에는 ID 조인이 남아 NULL ID가 빠짐. |
| 중간2: coords 전량 행·ID 맵 적재 | **해결** | `CREATE AS SELECT`로 SQLite 안에서 표를 생성함. 다만 새 대표 좌표 UPDATE에 별도 성능 결함이 있음. |
| 중간3: 주소 목록의 내부 공백 병합 | **해결** | address 목록을 `e.addr_key`로 묶어 `"서울  중구"`와 `"서울 중구"`를 구분함. |
| 중간4: Python/Dart 공백 집합 차이 | **해결** | 주소키 계산에 정본 29자 집합을 적용. BOM·U+001C 경계 시험 통과. 후속 목록 조회·문구 비교에는 아래 잔여 결함이 있음. |
| 낮음1: pin_basis 정규화 차이 | **부분** | Standalone의 `" ADDRESS "`는 해결. Client 요청과 Unicode 경계는 아직 불일치함. |

## 높음

새 높음 없음.

## 중간

**1. NULL ID 신고가 주소 모드의 좌표 없는 목록에서 누락됩니다.**  
[local_db_service.dart:3331](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:3331)

좌표가 없고 주소가 `"서울 중구"`인 신고 3건의 ID를 `NULL`, `NULL`, `''`로 입력하면, effective 표·지도 미좌표 건수는 **3건**이지만 목록은 **1건**입니다. `e.ID=r.ID` 조인이 NULL 행을 제외합니다. SQLite 재현으로 확인했습니다.

고칠 방향: effective 표에 원본 행 식별자와 목록에 필요한 열을 보존해 ID 조인을 제거하십시오.

**2. 대표 좌표 UPDATE가 같은 주소의 좌표 후보를 신고마다 반복 검색합니다.**  
[local_db_service.dart:2867](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2867), [2875](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2875)

같은 주소키에 `(37+i×0.000001, 127)` 좌표를 가진 신고를 입력하면, 각 신고의 lat·lng 갱신마다 `NOT EXISTS`가 후보들을 다시 탐색합니다. 동일 SQL을 메모리 SQLite에서 실행한 결과 **1천 건 0.53초 → 2천 건 2.09초**였고, **5만 건은 5초 제한에서 미완료**였습니다. 5만 건의 건수 표·인덱스 생성은 약 0.13초였으므로 병목은 UPDATE입니다.

취소 확인도 UPDATE 뒤에 있어 빠른 토글·닫기·동기화가 SQL 완료까지 기다립니다. 고칠 방향: 주소별 승자를 한 번만 계산해 인덱스를 만들고, 신고에는 그 결과를 적용하십시오.

**3. 주소 모드 카드의 ‘전체 신고 보기’가 빈 목록을 엽니다.**  
[local_db_service.dart:3263](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:3263), [3357](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:3357), [1773](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:1773)

좌표 없는 신고의 주소정규화를 `"\u001C서울 중구"`로 입력하면 주소 모드 카드는 **1건**입니다. 하지만 반환한 조회 키는 `"서울 중구"`이고, 기존 목록 표의 키는 `"\u001C서울 중구"`라서 ‘전체 신고 보기’는 **0건**입니다. Dart 공백 처리와 SQLite 조회로 재현했습니다.

고칠 방향: effective 주소키에서 기존 목록 키를 다시 만들지 말고, 해당 그룹의 실제 기존 목록 키를 전달하십시오. 재조회에는 hex 키를 사용하고, coords 목록의 기존 정규화는 유지해야 합니다.

**4. 빈 장소가 먼저 들어오면 기존 coords 묶음 판정이 깨집니다.**  
[local_statistics.dart:332](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_statistics.dart:332), [local_db_service.dart:3128](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:3128)

같은 좌표·처리상태인 두 신고의 장소를 `''`, `"서울 중구"`로 입력하면, 변경 전에는 장소 종류가 2개여서 **cluster=true**였습니다. 변경 후 주소키별 SQL 그룹은 각각 종류가 1개이고, accumulator가 빈 문자열을 초기 상태로 취급해 **cluster=false**가 됩니다. 따라서 `2건 묶음` 라벨과 탭 확대가 사라집니다.

또한 장소가 `"\uFEFF서울 중구"`와 `"서울 중구"`이고 처리상태가 다르면, Dart로 읽은 문구 비교에서 BOM이 사라져 묶음이 누락됩니다. 문구 비교에 hex가 적용되지 않은 경로입니다.

고칠 방향: 초기화 여부를 별도로 관리하고, 칸 전체 장소 종류도 SQL/hex 기준으로 판정하십시오.

## 낮음

**1. pin_basis 정규화가 호출 경로·공백 문자에 따라 다릅니다.**  
[api_service.dart:615](/home/better0101/projects/wt-mobile-pinbasis/lib/services/api_service.dart:615), [670](/home/better0101/projects/wt-mobile-pinbasis/lib/services/api_service.dart:670), [local_db_service.dart:2702](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2702)

`" ADDRESS "`는 서버·Standalone에서 address지만 Client에서는 쿼리가 생략돼 coords입니다. `"\u001CADDRESS\u001F"`는 서버 address·Standalone coords이고, `"\uFEFFADDRESS"`는 반대입니다.

고칠 방향: 정본 공백 집합을 사용하는 정규화 함수를 Client와 Standalone 모두에 적용하십시오.

**2. BOM만 있는 주소키가 화면에 hex 문자열로 노출됩니다.**  
[local_statistics.dart:397](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_statistics.dart:397), [local_db_service.dart:2799](/home/better0101/projects/wt-mobile-pinbasis/lib/services/local_db_service.dart:2799)

주소정규화 `"\uFEFF"`, 장소 `''`, 유효 좌표를 입력하면 주소키 hex는 `EFBBBF`입니다. 표시 문자열은 디코딩 중 빈 문자열이 되고, 라벨 함수가 비교용 키로 fallback하여 **address·region에 `"EFBBBF"`**를 반환합니다. 실제 함수 코드 실행으로 확인했습니다.

고칠 방향: hex 비교키와 표시용 원문을 분리하고, 표시문구 fallback에 hex를 사용하지 마십시오.

검증: 공용 벡터 두 파일은 바이트 동일. 모바일 지정 36건·추가 20건 통과. 서버 원 명령은 임시파일 제한으로 blocked였고, fixture 저장소만 메모리 SQLite로 바꾼 실행은 16건 통과했습니다. 서버 경로 mock은 전달 코드를 우회하지 않습니다. 닫기·DB 교체·롤백·동시 조회·늦은 토글 응답은 코드 대조했으며 전체 전용 실행 검증은 수행하지 않았습니다. 저장소 파일을 수정하지 않았습니다.