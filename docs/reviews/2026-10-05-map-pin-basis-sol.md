# 2026-10-05 신고 지도 핀 기준(위도·경도/주소)·묶음 원 이름 — GPT-6.1-Sol 검수 기록
범위: safetyreport(PC)·safetyreport-mobile, 브랜치 `feat/map-pin-basis`. 명세: `docs/plans/2026-10-05-map-pin-basis.md`.

검토자: GPT-6.1-Sol(codex exec, read-only, effort high). 구현: 1차·후속은 Muse(OpenCode), Sol 1차 반영부터는 Opus 직접(사용자 지시 "수정 너가 해").
각 차수의 원문을 아래에 그대로 남긴다.

| 차수 | 대상 | 결과 | 반영 |
|---|---|---|---|
| 1차 | PC 0a8bc8e · 모바일 cdf93910 | 높음 0·중간 6(모두 모바일)·낮음 2 | 아래 표 |

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