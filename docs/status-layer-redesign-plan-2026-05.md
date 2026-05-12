# 상태 계층 재설계 계획 (2026-05)

## 구현 메모 (2026-05-13)

- 서버/모바일 모두 `상태 = raw`, `처리상태 = canonical` 규칙으로 구현 완료
- 새로 저장되는 미종결 일반 상태는 `처리중`으로 통일하고, `진행중`은 신규 저장값에서 제거
- 서버 `get_pending_detail_ids()` 와 모바일 standalone 증분 동기화는 이제 `상태 != 처리상태` mismatch 가 아니라 `종결여부`, `보완_미응답`, detail 존재 여부를 기준으로 동작
- 서버 `upgrade_schema()` 와 모바일 local DB open 시 legacy row 를 `raw 상태 + 보완 flag` 기준으로 정규화하는 backfill 을 추가
- 검증 샘플
  - `59614484`: raw `진행`, canonical `보완요청`
  - `57822934`: raw `답변완료`, canonical `수용`
  - `40871819`: raw/canonical `취하`
  - `59216726`: raw `진행`, canonical `처리중`

## 목적

현재 서버/모바일은 아래 3가지를 완전히 분리하지 못하고 있다.

- 안전신문고 원본 진행상황
- 사용자에게 보여주는 처리상태
- 재크롤링/종결 판정용 lifecycle 상태

이 문서의 목표는 새 상태를 더 늘리는 것이 아니라, 기존 `상태`, `처리상태`, `종결여부`, `보완_미응답`의 역할을 엄밀하게 다시 정의하는 것이다.

핵심 원칙은 다음 한 줄이다.

- `상태`는 원본 상태
- `처리상태`는 canonical 표시 상태
- `종결여부`와 `보완_미응답`은 lifecycle 판정 상태

## 현재 진단

### 1. `상태`와 `처리상태` 의미가 일관되지 않다

- API 목록 크롤링은 `C_NOW=0 -> 진행`을 `title.상태`에 넣는다.
- 레거시 상세/API 상세 파서는 답변 파싱이 안 되면 `detail.처리상태=처리중` fallback을 넣는다.
- 그런데 일부 상세 파싱 경로는 `보완요청` 같은 추론 결과를 다시 `title.상태`에 덮어쓰기도 한다.

결과적으로 현재 `title.상태`는 어떤 건 raw, 어떤 건 inferred 상태가 된다.

### 2. 미종결 판정이 상태 mismatch에 과도하게 의존한다

- 현재 `get_pending_detail_ids()`는 `title.상태 != detail.처리상태`를 주요 신호로 쓴다.
- 하지만 `상태=진행`, `처리상태=처리중`은 설계상 자연스러운 차이일 수 있다.
- 즉 mismatch가 "변경"인지 "정상적인 계층 차이"인지 구분되지 않는다.

### 3. `진행`, `진행중`, `처리중`이 같은 bucket으로 취급되지만 저장은 제각각이다

- 조회/집계/모바일 색상은 이미 셋을 같은 의미로 본다.
- 반면 저장 단계에서 셋이 혼재해 UI와 DB가 모두 지저분해진다.

### 4. 모바일도 같은 문제를 복제한다

- mobile `reports.상태` / `reports.처리상태`가 서버와 같은 의미 분리를 하지 못한다.
- standalone parser도 `C_NOW=0 -> 진행`, fallback `처리중`을 함께 사용한다.

## 재설계 목표

### 목표

- `상태`와 `처리상태`를 의미적으로 분리
- `진행중` 완전 퇴출
- `진행`은 raw status로만 사용
- 미종결 재크롤링 기준에서 status mismatch 의존 제거
- 서버/모바일 import-export 시 두 계층 모두 보존

### 비목표

- 기존 UI에 새 상태 이름을 많이 추가하지 않음
- 1차에서는 대규모 스키마 추가를 하지 않음
- 1차에서는 과거 모든 row의 raw history를 완벽 복원하지 않음

## 권장 상태 모델

### 1. 원본 상태: `상태`

의미:
- 안전신문고 원본 진행상황
- 목록 API의 `C_NOW`, 레거시 페이지의 `진행상황` 텍스트, 또는 상세 페이지의 원문 진행상황에서 직접 온 값

허용값 예시:
- `진행`
- `보완요청`
- `답변완료`
- `일부수용`
- `불수용`
- `기타`
- `취하`
- `이송`
- `검토중`

규칙:
- `상태`에는 `처리중`을 저장하지 않는다.
- `상태`에는 보완요청 추론 결과를 임의로 덮어쓰지 않는다.
- 가능한 한 원본 source가 말한 값을 그대로 보존한다.

### 2. canonical 표시 상태: `처리상태`

의미:
- 검색, 통계, 대시보드, 모바일 카드, 알림에서 공통으로 쓰는 사용자-facing 상태

권장 허용값:
- `처리중`
- `보완요청`
- `수용`
- `일부수용`
- `불수용`
- `기타`
- `답변완료`
- `취하`
- `이송`

규칙:
- `진행`, `진행중`은 저장하지 않는다.
- 일반 미종결 상태는 모두 `처리중`으로 canonicalize한다.
- `보완_미응답='Y'`이면 `처리상태=보완요청`이 우선한다.

### 3. lifecycle 상태: `종결여부`, `보완_미응답`

의미:
- 재크롤링 대상 선정과 열린 보완 요청 여부 판정

규칙:
- `종결여부='Y'`이면 일반적으로 추가 상세 크롤링 대상에서 제외
- 단 `보완_미응답='Y'`이면 다음 크롤링 대상에 계속 포함
- status mismatch는 lifecycle의 주신호로 쓰지 않는다

## API 파서 계획

대상 파일:
- `services/parser.py`
- `core/crawler/crawltitle_api.py`

### API 목록(`title.상태`)

현행:
- `C_NOW=0 -> 진행`

유지:
- 목록 단계에서는 raw mapping을 유지한다.
- 즉 `C_NOW=0`은 계속 `상태=진행`

### API 상세(`detail.처리상태`)

새 규칙:

1. raw status 계산
- `raw_status = C_NOW 매핑값`
- 예: `0 -> 진행`, `10 -> 답변완료`

2. supplement open 판정
- `SPLMNT_FNSH_YN`, `SPLMNT_CMPTN_YN`, `SPLMNT_DMND_NO`, `answers` 조합으로 열려 있는 보완요청 여부 판정

3. canonical status 계산
- 열린 보완요청이면 `보완요청`
- 최종 답변/처분이 확정되었으면 `수용/일부수용/불수용/기타/답변완료/취하`
- 그 외는 `처리중`

4. title update
- `title_fields['상태']`에는 canonical이 아니라 raw status를 넣는다
- 즉 `C_NOW=0 + 보완 open`이어도
  - `상태 = 진행`
  - `처리상태 = 보완요청`

예시:
- `59614484`
  - raw: `진행`
  - canonical: `보완요청`
- 일반 미종결 `C_NOW=0`
  - raw: `진행`
  - canonical: `처리중`
- 답변 완료 후 수용
  - raw: `답변완료`
  - canonical: `수용`

## 레거시 파서 계획

대상 파일:
- `services/parser.py`
- `core/crawler/crawldetail.py`
- `core/crawler/crawltitle.py`

### 레거시 목록(`title.상태`)

- 페이지 목록에 보이는 상태 텍스트를 raw status로 유지한다.
- 즉 `진행`, `보완요청`, `취하` 등 현재 화면의 진행상황을 그대로 `상태`에 저장한다.

### 레거시 상세(`detail.처리상태`)

새 규칙:

1. raw status 계산
- 신고 기본정보의 `진행상황` 텍스트를 raw status로 읽는다

2. supplement open 판정
- `splmntDivBody` 파싱 결과에서 완료일 없는 마지막 round 존재 여부로 판정

3. result table 상태 계산
- 결과 테이블의 `처리상태`가 최종값이면 canonical로 채택

4. canonical status 계산
- 열린 보완요청이면 `보완요청`
- 결과 테이블이 최종상태면 그 상태
- `취하`면 `취하`
- 그 외는 `처리중`

예시:
- `진행상황=진행`, 결과표 없음
  - `상태 = 진행`
  - `처리상태 = 처리중`
- `진행상황=보완요청`, open supplement 존재
  - `상태 = 보완요청`
  - `처리상태 = 보완요청`

## 크롤링 대상 선정 재설계

대상 파일:
- `core/database/database.py`

현재 문제:
- `title.상태 != detail.처리상태`는 앞으로 정상적인 계층 차이일 수 있다.
- 따라서 이것을 "변경 감지"의 핵심 기준으로 계속 쓰면 안 된다.

새 기준:

1. detail row가 없음
2. `종결여부 != 'Y'`
3. `보완_미응답 = 'Y'`

선택적 추가 기준:
- 최근 동기화 실패 건
- 별점/만족도 follow-up 대상

제거할 것:
- `상태 != 처리상태`를 primary signal로 쓰는 로직

## 검색/통계/UI 계획

대상 파일:
- `services/report_query_service.py`
- `services/report_stats_service.py`
- `web/templates/data_table.html`
- 모바일 `lib/providers/report_provider.dart`
- 모바일 `lib/widgets/search_filter_sheet.dart`

규칙:
- 검색/통계는 `처리상태` 기준으로만 동작
- `처리중` 검색은 legacy row 호환을 위해 당분간 `진행`, `진행중`, `처리중` 모두 매칭
- 하지만 새로 저장되는 값은 `처리중`만 허용
- UI 옵션에는 `진행`, `진행중`을 노출하지 않음

## 모바일 계획

대상 파일:
- `lib/services/standalone_parser.dart`
- `lib/models/report.dart`
- `lib/services/local_db_service.dart`
- `lib/providers/report_provider.dart`
- `lib/screens/dashboard_screen.dart`
- `lib/widgets/report_detail_sheet.dart`

원칙:
- `reports.상태` = raw status
- `reports.처리상태` = canonical status

처리:
- standalone parser도 서버와 동일하게
  - raw: `C_NOW -> 진행`
  - canonical: `처리중` 또는 `보완요청` 또는 최종상태
- 대시보드/검색/색상/알림은 모두 canonical만 사용
- 상세 화면에서는 필요 시 raw status를 추가 메타로 보여줄 수 있지만 기본 노출은 선택사항

현재 이미 유지되는 점:
- 모바일 상세는 마지막 보완요청 내용, 요청자, 요청 일시, 완료 일시, 신고자 의견을 표시한다

## 마이그레이션 계획

### 1차: 저장 의미 정규화

신규 크롤링부터 아래 규칙 적용:
- `처리상태='진행'` 저장 금지
- `처리상태='진행중'` 저장 금지
- 일반 미종결은 `처리중`

### 2차: 기존 데이터 보정

서버 detail/merge, 모바일 reports 대상:
- `처리상태 IN ('진행', '진행중') -> 처리중`
- `보완_미응답='Y'` 이고 최종상태가 아니면 `처리상태='보완요청'`

주의:
- `상태`는 원본 보존 컬럼이므로 일괄 `처리중` 치환 대상이 아니다
- 과거 API 보완건 중 이미 `상태=보완요청`으로 들어간 row는 raw/inferred가 섞여 있을 수 있다
- 따라서 `상태`는 무차별 backfill하지 말고, 앞으로 생성되는 데이터부터 semantics를 엄격히 맞추는 것이 안전하다

### 3차: import-export 호환

서버↔모바일 round-trip 시 아래 의미를 유지:
- `상태`는 raw
- `처리상태`는 canonical
- `종결여부`, `보완_미응답`은 lifecycle

현행 스키마는 이미 두 컬럼을 모두 가지고 있으므로, 1차 설계에서는 추가 컬럼 없이도 round-trip 가능하다.

## 구현 순서

### P0. 계획 문서 확정

- 이 문서 리뷰
- canonical status 허용 집합 최종 확정

### P1. 파서 정규화

- 서버 `parse_json_details()`, `parse_details()`에서 raw/canonical 분리
- 모바일 `standalone_parser.dart` 동일 반영
- one-off helper 남발 금지
  - 단순 wrapper 대신 각 파서 함수 안에서 straight-line 규칙으로 구현
  - 서버/모바일 양쪽에서 같은 규칙이 필요하면 그때만 공통 함수 고려

### P2. 재크롤링 기준 변경

- `get_pending_detail_ids()`에서 mismatch 의존 제거
- `종결여부`, `보완_미응답` 중심으로 전환

### P3. UI/검색 정리

- 웹/모바일 처리상태 옵션을 canonical 기준으로 통일
- `진행`, `진행중`은 UI 옵션에서 제거

### P4. 데이터 backfill

- 서버 DB 일괄 보정
- 모바일 import 시 legacy 값 자동 canonicalize

### P5. 검증

테스트 fixture 권장:
- `59614484`: raw `진행`, canonical `보완요청`
- 일반 `C_NOW=0` 미종결 건: raw `진행`, canonical `처리중`
- `57822934`: raw `답변완료`, canonical `수용`
- 레거시 `진행상황=진행` + 결과표 없음: raw `진행`, canonical `처리중`
- 취하 건: raw `취하`, canonical `취하`

## 선택적 2차 확장

1차를 끝낸 뒤에도 raw source 추적이 더 필요하면 아래를 고려할 수 있다.

- `원본상태코드` INTEGER
  - API `C_NOW` 원본 숫자 저장
- `상태판정버전` TEXT
  - canonical rule 변경 시 backfill 추적용

하지만 이는 1차 필수는 아니다.

권장 우선순위는:
- 의미 정규화
- 재크롤링 기준 분리
- UI 정리
- 이후 필요 시 디버그용 컬럼 추가

## 최종 권고

가장 안전한 1차안은 다음이다.

- `상태`는 raw 상태로 고정
- `처리상태`는 canonical 상태로 고정
- `진행중`은 저장 금지
- `진행`은 raw에서만 허용
- 일반 미종결 canonical은 `처리중`
- 보완요청 canonical은 `보완요청`
- 재크롤링은 `종결여부`, `보완_미응답` 중심으로 판정

이렇게 하면 스키마를 크게 늘리지 않고도 서버와 모바일 모두에서 상태 의미가 훨씬 명확해진다.
