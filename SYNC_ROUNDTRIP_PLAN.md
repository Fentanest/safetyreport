# Sync Roundtrip Improvement Plan

## 1. 목적

이 문서는 서버 앱과 모바일 앱 사이의 DB 변환 과정에서 발생하는 메타데이터 유실 문제를 정리하고, 이를 단계적으로 해소하기 위한 개선 계획을 정의한다.

이번 계획의 직접적인 목표는 다음과 같다.

1. 모바일 `reports.synced_at` 의 의미를 서버에서도 보존한다.
2. 서버 대시보드의 `최근 3일 내 답변 완료된 건` 정렬을 `가장 최신 기록 순`, 동순위일 경우 `신고번호 역순`으로 바꾼다.
3. 모바일 `raw_content` 가 서버를 거치면서 유실되는 문제를 해소한다.
4. 모바일이 서버 DB 를 import 할 때 `entry_value` 를 `''` 로 넣는 문제를 해결한다.
5. 서버-모바일 round-trip 이후에도 `category`, `entry_value`, `synced_at`, `raw_content` 의 의미가 최대한 보존되도록 구조를 정리한다.


## 2. 현재 상태 요약

### 2.1 모바일 DB

모바일은 단일 `reports` 테이블 구조를 사용한다.

- `category`
- `entry_value`
- `raw_content`
- `synced_at`

이 네 값은 모바일 로컬 DB 에 실제 컬럼으로 존재한다.

### 2.2 서버 DB

서버는 `mysafety` / `mysafetydetail_*` / `mysafetymerge_*` 구조를 사용한다.

- `category` 는 컬럼이 아니라 어떤 detail/merge 테이블에 속하느냐로 표현된다.
- `entry_value` 는 `mysafety_entry_value` 별도 테이블에 저장된다.
- `synced_at` 는 현재 서버 스키마에 없다.
- `raw_content` 는 현재 서버 스키마에 없다.

### 2.3 현재 유실 패턴

현재 구현 기준으로 데이터 보존 상태는 다음과 같다.

- 서버 -> 모바일
  - `entry_value`: 유실
  - `raw_content`: 유실
  - `synced_at`: 유실 후 import 시점의 `now` 로 재생성

- 모바일 -> 서버
  - `entry_value`: 보존
  - `raw_content`: 유실
  - `synced_at`: 유실

즉 현재는 서버를 한 번 거치면 `raw_content` 는 비가역적으로 사라지고, `entry_value` 와 `synced_at` 도 완전하게 round-trip 되지 않는다.


## 3. 직접 해결해야 할 문제

### 3.1 최근 답변 정렬 문제

현재 서버의 최근 답변 목록은 최근 3일 필터 후 `답변일 DESC` 로만 정렬된다. 이 방식은 같은 날짜 내에서 실제로 더 최근에 반영된 기록을 위로 올릴 수 없다.

원하는 정렬 규칙은 다음과 같다.

1. 최근 3일 이내 `답변일` 인 건만 대상으로 한다.
2. `synced_at DESC`
3. `synced_at` 이 같으면 `신고번호 DESC`

### 3.2 단건 재크롤 왜곡 문제

`synced_at` 를 도입하더라도, 변화 없는 단건 재크롤이 `synced_at = now` 를 다시 찍으면 최근 답변 순서가 왜곡된다.

따라서 `synced_at` 는 단순 "조회 시각" 이 아니라 "실제 신규/변경이 로컬 DB 에 반영된 시각" 으로 정의해야 한다.

### 3.3 모바일 import 시 `entry_value=''`

모바일 `importFromServerDb()` 는 서버의 `mysafetymerge_*` 만 읽고 `reports.entry_value` 를 빈 문자열로 저장한다. 서버에는 이미 `mysafety_entry_value` 가 있으므로, 이 정보는 복원 가능함에도 현재 손실되고 있다.

### 3.4 `raw_content` 의 저장 위치

`raw_content` 는 원본 payload 보존용이고 용량이 클 수 있다. 이를 서버의 주 조회 테이블에 그대로 넣으면 대시보드/API/엑셀 흐름을 불필요하게 무겁게 만든다.

모바일도 같은 이유로 `reports` 본문 테이블에 `raw_content` 를 계속 직접 들고 있을지 검토가 필요하다.


## 4. 설계 원칙

이번 계획은 다음 원칙을 따른다.

1. 모바일의 단일 `reports` 중심 구조는 유지한다.
2. 서버의 `title/detail/merge` 구조도 유지한다.
3. 조회에 자주 쓰는 값과 원본 보존용 대용량 값을 분리한다.
4. `synced_at` 는 "실제 반영 시각" 이어야 하며, 단순 재조회 시각이 되면 안 된다.
5. round-trip 이후에도 데이터 의미가 바뀌지 않도록 서버/모바일 변환 규칙을 명시적으로 맞춘다.


## 5. 권장 데이터 구조

## 5.1 서버

### 5.1.1 `synced_at`

`synced_at` 는 다음 테이블에 추가한다.

- `mysafetydetail_traffic`
- `mysafetydetail_parking`
- `mysafetydetail_other`
- `mysafetymerge_traffic`
- `mysafetymerge_parking`
- `mysafetymerge_other`

권장 타입은 `INTEGER` 이며, 값 형식은 모바일과 동일한 Unix epoch milliseconds 로 맞춘다.

`mysafety` title 테이블에는 넣지 않는다.

이유는 다음과 같다.

- 최근 답변/처리결과의 의미는 title 보다 detail 쪽에 가깝다.
- 서버의 대시보드/API 는 실질적으로 `merge_*` 를 읽는다.
- title 테이블은 상태/제목/신고일 등 요약 정보 중심이라 `synced_at` 의 의미가 약하다.

### 5.1.2 `raw_content`

`raw_content` 는 별도 테이블로 분리한다.

권장 테이블:

- `mysafety_raw_content`

권장 컬럼:

- `ID TEXT PRIMARY KEY`
- `raw_content TEXT NOT NULL DEFAULT ''`
- `raw_type TEXT NOT NULL DEFAULT ''`
- `saved_at INTEGER`

`raw_content` 를 `detail_*` / `merge_*` 안에 넣지 않는 이유는 다음과 같다.

- 원본 payload 는 크기가 크고 목록 조회에서 거의 사용되지 않는다.
- `merge_*` 에 넣으면 대시보드/API/export 흐름이 무거워진다.
- 필요 시 `ID` 기준 join 으로 조회하는 편이 훨씬 안전하다.

### 5.1.3 `entry_value`

현재처럼 `mysafety_entry_value` 별도 테이블을 유지한다. 이 구조는 이미 충분히 적절하다.


## 5.2 모바일

### 5.2.1 전체 구조

모바일은 서버처럼 `title/detail/merge` 로 나누지 않는다.

이유는 다음과 같다.

- 모바일의 대부분 조회가 단일 `reports` 테이블 전제를 사용한다.
- 오프라인 로컬 DB 에서 대규모 join 구조를 도입하면 코드 복잡도만 커진다.
- 모바일은 서버보다 "빠른 단순 조회" 와 "동기화 직후 화면 반영" 이 더 중요하다.

### 5.2.2 `reports`

다음 값은 계속 `reports` 본 테이블에 둔다.

- `category`
- `entry_value`
- `synced_at`

이 값들은 작고, 필터/정렬/화면 표시와 직접 연결되므로 본 테이블 유지가 맞다.

### 5.2.3 `raw_content`

모바일도 `raw_content` 는 별도 테이블 분리를 권장한다.

권장 테이블:

- `report_raw`

권장 컬럼:

- `ID TEXT PRIMARY KEY`
- `raw_content TEXT NOT NULL DEFAULT ''`
- `raw_type TEXT NOT NULL DEFAULT ''`
- `saved_at INTEGER`

이렇게 하면 `reports` 는 가벼운 조회용 테이블로 남고, 원본 payload 는 필요할 때만 읽으면 된다.


## 6. `synced_at` 의미 정의

`synced_at` 는 다음 의미로 통일한다.

- "이 신고의 현재 레코드 내용이 마지막으로 반영된 시각"

이 값은 다음 규칙으로 갱신한다.

1. 신규 insert
   - `synced_at = now`
2. 기존 row 이지만 내용 변경 있음
   - `synced_at = now`
3. 기존 row 이고 내용 변경 없음
   - 기존 `synced_at` 유지

이 규칙은 특히 단건 크롤링에서 중요하다. 변화 없는 재크롤까지 `synced_at` 를 갱신하면 최근 답변 정렬이 재조회 순서로 오염된다.


## 7. 최근 답변 정렬 규칙

서버와 모바일 모두 최근 답변 목록을 다음 기준으로 맞춘다.

### 7.1 필터

- `답변일` 이 오늘 기준 최근 3일 이내
- 필요 시 `취하` 제외 옵션 반영

### 7.2 정렬

1. `synced_at DESC`
2. `신고번호 DESC`

### 7.3 예외 처리

기존 데이터 중 `synced_at` 가 비어 있는 레코드는 fallback 정렬이 필요하다.

권장 fallback:

1. `synced_at DESC NULLS LAST`
2. `답변일 DESC`
3. `신고번호 DESC`

SQLite/Pandas 구현에서는 `synced_at` 가 없는 값은 최소값 취급 후 `답변일` 과 `신고번호` 를 보조 정렬키로 사용하면 된다.


## 8. 서버-모바일 변환 규칙

## 8.1 모바일 -> 서버

### 8.1.1 보존 대상

- `category`
  - 서버에서는 테이블 선택으로 보존
- `entry_value`
  - `mysafety_entry_value` 로 저장
- `synced_at`
  - `detail_*` 및 `merge_*` 로 보존
- `raw_content`
  - `mysafety_raw_content` 로 저장

### 8.1.2 구현 포인트

- `restore_from_mobile_db()` 에서 `reports.synced_at` 를 읽어 서버 detail row 에 포함한다.
- `merge_final()` 에서 detail 의 `synced_at` 를 merge 로 전파한다.
- `reports.raw_content` 는 별도 수집해 `mysafety_raw_content` 로 upsert 한다.

## 8.2 서버 -> 모바일

### 8.2.1 보존 대상

- `category`
  - source table 명으로 복원
- `entry_value`
  - `mysafety_entry_value` 를 읽어 `reports.entry_value` 로 넣는다
- `synced_at`
  - `merge_*` 의 값을 그대로 `reports.synced_at` 로 넣는다
- `raw_content`
  - `mysafety_raw_content` 를 읽어 별도 raw 테이블 또는 `reports` 로 복원한다

### 8.2.2 구현 포인트

- 모바일 `importFromServerDb()` 는 `mysafetymerge_*` 뿐 아니라 `mysafety_entry_value` 도 함께 읽어야 한다.
- 현재의 `entry_value: ''` 하드코딩을 제거한다.
- `raw_content: ''` 하드코딩도 제거하고, 서버 raw table 과 join 해 복원한다.
- `synced_at` 는 `now` 로 재발급하지 말고 서버 저장값을 우선 사용한다.


## 9. 세부 구현 계획

## 9.1 서버 스키마 변경

1. `core/database/models.py`
   - `detail_*` 컬럼에 `synced_at INTEGER` 추가
   - `merge_*` 컬럼에 `synced_at INTEGER` 추가
   - `mysafety_raw_content` 테이블 정의 추가

2. `core/database/database.py`
   - `upgrade_schema()` 가 신규 컬럼/테이블 생성하도록 확장
   - `detail_to_sql()` 에서 기존 row 와 비교 후 `synced_at` 보존 또는 갱신
   - `merge_final()` / `_merge_for_table()` 에서 detail 의 `synced_at` 를 merge 로 전파

## 9.2 서버 크롤링 저장 로직

1. detail upsert 직전 기존 row 조회 결과를 활용
2. `is_new or is_changed` 일 때만 `synced_at = now`
3. 변화가 없으면 기존 `synced_at` 를 그대로 사용
4. API/legacy 공통 파이프라인이 이 규칙을 모두 타도록 유지

## 9.3 서버 대시보드/통계

1. `services/report_stats_service.py`
   - 최근 답변 목록 정렬 키를 `synced_at DESC`, `신고번호 DESC` 로 변경
2. 필요 시 반환 payload 에 `synced_at` 도 포함해 모바일/웹 디버깅에 활용

## 9.4 서버 백업/복원

1. `services/db_backup.py`
   - 모바일 -> 서버 복원 시 `synced_at` 수집 및 저장
   - 모바일 -> 서버 복원 시 `raw_content` 별도 테이블 저장
   - 서버 -> 모바일 복원에 필요한 raw table / entry_value table 보존

## 9.5 모바일 스키마 변경

1. 기존 `reports.raw_content` 유지 여부를 선택해야 한다.
2. 권장안은 `report_raw` 추가 후 점진 이관이다.
3. 호환성 초기 단계에서는 다음 둘 중 하나를 선택한다.

- 단계 A: 우선 `reports.raw_content` 유지, 서버 round-trip 문제만 먼저 해결
- 단계 B: 이후 마이그레이션에서 `report_raw` 로 분리

실행 리스크를 낮추려면 `A -> B` 2단계가 더 안전하다.

## 9.6 모바일 import/export

1. `importFromServerDb()`
   - `mysafety_entry_value` join 반영
   - `synced_at` 복원
   - `raw_content` 복원

2. 서버 업로드/export 경로
   - 모바일 DB 업로드 시 `raw_content`, `synced_at` 보존

## 9.7 모바일 최근 답변 정렬

`LocalDbService.computeSummary()` 의 최근 답변 쿼리를 다음과 같이 바꾼다.

- 기존: `답변일 DESC`
- 변경: `synced_at DESC, 신고번호 DESC`

단, `synced_at` 가 없는 과거 행 대응을 위해 fallback 정렬을 같이 둔다.


## 10. 권장 실행 순서

1. 서버에 `synced_at` 와 `mysafety_raw_content` 스키마를 추가한다.
2. 서버 `detail_to_sql()` 에 no-change 보존 규칙을 넣는다.
3. 서버 `merge_final()` 에 `synced_at` 전파를 넣는다.
4. 서버 최근 답변 정렬을 `synced_at` 기준으로 바꾼다.
5. 서버 `restore_from_mobile_db()` 에 `synced_at` / `raw_content` 보존을 넣는다.
6. 모바일 `importFromServerDb()` 에 `entry_value` 복원을 넣는다.
7. 모바일 `importFromServerDb()` 에 `synced_at` / `raw_content` 복원을 넣는다.
8. 모바일 최근 답변 정렬을 서버와 동일하게 맞춘다.
9. 안정화 후 `raw_content` 를 모바일 별도 테이블로 분리할지 결정한다.


## 11. 테스트 계획

### 11.1 서버 단위 테스트

1. 신규 insert 시 `synced_at` 가 생성되는지
2. 동일 내용 재크롤 시 `synced_at` 가 유지되는지
3. 실제 변경 재크롤 시 `synced_at` 가 갱신되는지
4. `merge_final()` 이후 merge row 에 `synced_at` 가 유지되는지
5. 최근 답변 정렬이 `synced_at DESC`, `신고번호 DESC` 로 되는지

### 11.2 변환 테스트

1. 모바일 -> 서버 복원 후 `entry_value` 유지 확인
2. 모바일 -> 서버 복원 후 `raw_content` 유지 확인
3. 모바일 -> 서버 복원 후 `synced_at` 유지 확인
4. 서버 -> 모바일 import 후 `entry_value` 가 `''` 가 아닌지 확인
5. 서버 -> 모바일 import 후 `raw_content` 복원 확인
6. 서버 -> 모바일 import 후 `synced_at` 가 `now` 로 재생성되지 않고 원값을 따르는지 확인

### 11.3 round-trip 테스트

다음 흐름을 fixture 로 검증한다.

1. 모바일 DB 생성
2. 서버로 import
3. 서버 DB 를 다시 모바일로 import
4. 원본과 비교

비교 필드:

- `category`
- `entry_value`
- `synced_at`
- `raw_content`
- `신고번호`
- `처리상태`
- `답변일`


## 12. 리스크와 대응

### 12.1 스키마 증가

`merge_*` 에 `synced_at` 가 추가되면 export/API payload 에도 값이 노출될 수 있다. 필요 시 UI 노출과 내부 저장은 분리해야 한다.

### 12.2 `raw_content` 용량 증가

원본 payload 는 크기가 커질 수 있으므로, 서버 raw table 은 필요한 경우 압축/청소 정책을 둘 수 있다.

### 12.3 모바일 마이그레이션 비용

모바일에서 `raw_content` 를 별도 테이블로 분리하면 기존 사용자 DB 마이그레이션이 필요하다. 이 때문에 1차 릴리즈에서는 round-trip 보존만 먼저 해결하고, 테이블 분리는 2차로 미루는 것이 안전하다.


## 13. 최종 권장안

이번 개선의 최종 권장안은 다음과 같다.

### 서버

- `detail_*` + `merge_*` 에 `synced_at INTEGER`
- `mysafety_entry_value` 유지
- `mysafety_raw_content` 신설

### 모바일

- `reports` 는 유지
- `category`, `entry_value`, `synced_at` 는 계속 `reports` 에 둠
- `raw_content` 는 1차에는 유지 가능, 2차에는 `report_raw` 분리 권장

### 변환

- 서버 -> 모바일 import 시 `entry_value=''` 하드코딩 제거
- `raw_content=''` 하드코딩 제거
- `synced_at=now` 재발급 제거

### 최근 답변 정렬

- 최근 3일 필터는 `답변일`
- 정렬은 `synced_at DESC`, `신고번호 DESC`
- no-change 재크롤은 `synced_at` 유지


## 14. 완료 기준

다음 조건이 모두 만족되면 이번 계획이 완료된 것으로 본다.

1. 서버와 모바일 모두 최근 답변이 같은 기준으로 정렬된다.
2. 변화 없는 단건 재크롤은 최근 답변 순서를 흔들지 않는다.
3. 서버 -> 모바일 import 후 `entry_value` 가 빈 문자열로 바뀌지 않는다.
4. 모바일 -> 서버 -> 모바일 round-trip 후 `raw_content` 가 유지된다.
5. 모바일 -> 서버 -> 모바일 round-trip 후 `synced_at` 의미가 유지된다.
6. 서버/모바일 양쪽에서 관련 회귀 테스트가 추가된다.
