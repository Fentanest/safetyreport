# Implementation Plan

## 1. 목적

이 문서는 `safetyreport` 프로젝트의 유지보수성, 가독성, 일관성, 그리고 일부 구조 개선을 통한 성능 향상을 목표로 한 리팩토링 실행 계획을 정리한다.

핵심 목표는 다음과 같다.

1. 불필요하게 얇거나 중복된 헬퍼 함수를 줄인다.
2. API 경로와 Selenium/legacy 크롤링 경로에서 사실상 같은 책임을 가진 코드를 공통화한다.
3. 라우터, 서비스, 데이터 접근 계층의 책임 경계를 명확하게 나눈다.
4. 현재 pandas 중심의 전체 로드 후 필터링 구조를 점진적으로 개선해 응답 성능과 메모리 사용량을 낮춘다.
5. 앞으로 기능 추가 시 한 곳만 수정하면 되도록 모듈 구조를 재정렬한다.


## 2. 현재 구조 요약

현재 프로젝트는 대략 다음 계층으로 구성되어 있다.

- `core/crawler`: 안전신문고 목록/상세 크롤링, Selenium 드라이버, 로그인, direct login
- `core/database`: 스키마, 병합 테이블 생성, DB upsert/merge
- `services`: 데이터 조회, 통계, WebSocket 관리, 별점, 백업, 외부 수집
- `web/routers`: 웹 UI 및 모바일/API 엔드포인트
- `core/utils`: 로거, export, scheduler, 보안, 템플릿, 업데이트 등
- `start.py`: 실제 크롤링 실행 진입점
- `main.py`: FastAPI 앱 진입점

문제는 이 구조가 계층상으로는 나뉘어 있어도, 실제 책임은 여러 파일에 흩어져 있다는 점이다.

- 크롤링 제어 로직이 `start.py`, `web/routers/crawl.py`, `web/routers/api_route.py`, `core/utils/scheduler.py` 에 분산되어 있다.
- API 크롤링과 legacy 크롤링이 fetch 방식만 다르고 후처리 흐름은 거의 같은데, 서로 별도 구현되어 있다.
- DB 엔진 생성, 파일 다운로드 정책, 로그 회전, export, 별점 배치 실행 등이 여러 위치에서 반복된다.
- `services/data_service.py` 가 너무 많은 책임을 가진 거대 모듈 역할을 하고 있다.


## 3. 리팩토링 원칙

이번 리팩토링은 다음 원칙을 따른다.

1. 기능 보존이 우선이다.
2. 얇은 래퍼는 제거하되, 의미 있는 추상화는 남긴다.
3. 공통화는 "이름" 기준이 아니라 "책임" 기준으로 한다.
4. 라우터는 요청/응답 처리만 담당하고, 실제 동작은 서비스 계층으로 이동한다.
5. 데이터 접근은 가능한 한 서비스 또는 저장소 계층으로 모은다.
6. pandas 후처리는 꼭 필요한 경우만 유지하고, 가능한 필터는 SQL로 내린다.
7. 한 번에 전부 뒤집지 않고, 저위험 공통화부터 단계적으로 진행한다.


## 4. 즉시 정리 가능한 헬퍼 함수

### 4.1 제거 또는 축소 후보

다음 함수는 단독 의미가 약하거나 사실상 얇은 래퍼라서 제거 또는 통합이 가능하다.

- `core/database/database.py`
  - `_get_all_title_ids`
  - `_get_initial_scan_ids`
  - 두 함수 모두 사실상 같은 쿼리를 수행한다. `get_cNo` 내부 분기로 합치거나 하나만 남긴다.

- `services/data_service.py`
  - `resolve_ids_for_rating`
  - 현재 `resolve_to_report_numbers` 를 그대로 감싸는 래퍼이며, 이름도 실제 반환값과 어긋난다. 호출부를 직접 `resolve_to_report_numbers` 또는 새 이름으로 바꾸고 제거한다.

- `web/routers/auth_route.py`
  - `_get_engine`

- `web/routers/devices_route.py`
  - `_get_engine`

- `web/routers/ws_route.py`
  - `_get_engine`
  - 위 세 함수는 전부 같은 책임의 중복 구현이다. 공용 DB 엔진 모듈로 통합한다.

### 4.2 공용 유틸로 이동할 후보

- `core/crawler/direct_login.py`
  - `_configured_attempts`

- `services/satisfaction_fetcher.py`
  - `_configured_attempts`
  - 동일 의미이므로 공용 retry/settings 유틸로 이동한다.

- `core/crawler/crawltitle_api.py`
  - `_ensure_browser_context`

- `core/crawler/crawldetail_api.py`
  - `_ensure_browser_context`
  - 같은 브라우저 fallback 준비 로직이므로 API 크롤링 공용 유틸로 옮긴다.

- `web/routers/api_route.py`
  - `_rotate_log`

- `services/crawl_manager.py`
  - `_rotate_log`

- `core/utils/scheduler.py`
  - `wait_and_rotate_log` 내부 회전 로직
  - 로그 회전 정책이 세 군데로 갈라져 있으므로 통합한다.

### 4.3 이름 정리가 필요한 함수

- `core/database/database.py`
  - `deatil_to_sql`
  - 오타이므로 `detail_to_sql` 로 수정한다.

- `core/database/database.py`
  - `get_cNo`
  - 의미가 너무 약하다. `get_pending_detail_ids` 같은 명확한 이름으로 바꾼다.

- `core/crawler/crawldetail.py`
  - `crawl_details(driver, list)`

- `core/crawler/crawldetail_api.py`
  - `crawl_details(driver=None, list=None, ...)`
  - `list` 는 파이썬 내장형 이름을 가리므로 `report_ids` 등으로 변경한다.


## 5. 중복 책임 분석

### 5.1 API/legacy 상세 크롤링 중복

다음 두 파일은 데이터 fetch 방식만 다르고, 이후 처리 흐름은 매우 유사하다.

- `core/crawler/crawldetail.py`
- `core/crawler/crawldetail_api.py`

공통점은 다음과 같다.

- detail 결과를 동일한 컬럼 구조의 DataFrame 으로 만든다.
- `entry_value` 로 category 를 계산한다.
- `title_fields` 를 구성해 title 테이블 업데이트에 사용한다.
- 만족도조사 점수/사유를 조회해 `참여 완료` 와 `참여 가능` 상태를 보정한다.
- 최종적으로 `(df, category, entry_value, progress_status, title_fields)` 형태를 yield 한다.

즉, fetch 단계와 normalize 단계가 섞여 있어서 중복이 커진 상태다.

### 5.2 API 목록/상세의 fallback 중복

다음 파일은 모두 `direct_login 세션` 과 `Selenium browser fallback` 을 선택하는 구조를 각각 다시 구현하고 있다.

- `core/crawler/crawltitle_api.py`
- `core/crawler/crawldetail_api.py`

중복 내용은 다음과 같다.

- `browser_fallback` 분기
- 브라우저 컨텍스트 보장
- `$.get(...)` 기반 브라우저 API 호출
- 세션 기반 API 호출
- 401 재로그인 후 재시도 패턴

이는 `API Client` 또는 `Fetcher Provider` 계층으로 모을 수 있다.

### 5.3 크롤링 제어 중복

다음 파일들은 사실상 같은 업무를 서로 조금씩 다르게 구현하고 있다.

- `start.py`
- `web/routers/crawl.py`
- `web/routers/api_route.py`
- `core/utils/scheduler.py`
- `services/crawl_manager.py`

중복 책임은 다음과 같다.

- 크롤링 명령행 구성
- queue 파일 생성
- 현재 로그 파일 초기화
- 로그 회전
- 크롤링 시작/중지/재개
- 종료 후 후처리 스레드 실행
- WebSocket 브로드캐스트

이 부분은 반드시 서비스로 분리해야 한다.

### 5.4 파일 브라우저 중복

다음 두 파일은 허용 경로는 다소 다르지만, 같은 파일 서비스 성격을 가진다.

- `web/routers/file_browser_route.py`
- `web/routers/api_route.py`

공통 요소는 다음과 같다.

- 허용된 루트 경로 검사
- 디렉터리/파일 존재 검사
- 파일 다운로드
- live log 보호 처리
- 파일 목록 생성

### 5.5 별점 배치 실행 중복

다음 두 파일은 별점 실행 전 준비 과정이 거의 같다.

- `web/routers/rating_route.py`
- `web/routers/api_route.py`

공통 요소는 다음과 같다.

- 대상 ID 또는 신고번호 정규화
- 크롤링 중 충돌 방지
- `current_rating.log` 회전
- 별점 서비스 실행 스레드 시작

### 5.6 데이터 가공/조회 중복

`services/data_service.py` 안에도 중복이 있다.

- `search_by_vehicle`
- `search_by_address`
- `get_all_watchlist`
- `get_unrated_records`
- `_get_records_from_table`

이 함수들은 모두 비슷한 패턴으로:

1. merge 테이블들을 읽고
2. watchlist 를 읽고
3. 카테고리 라벨을 붙이고
4. pandas 필터를 적용하고
5. 결과를 dict 로 반환한다.

동일 패턴이 반복되므로 공통 query helper 또는 query builder 구조가 필요하다.


## 6. 모듈 분리 제안

아래는 권장 분리안이다. 한 번에 모두 만들 필요는 없지만, 최종 구조의 목표점으로 삼는다.

### 6.1 DB 엔진 모듈

신규 파일 제안:

- `core/database/engine.py`

책임:

- 앱 전체 공용 `get_engine()`
- SQLite 옵션 관리
- 필요 시 lazy singleton 엔진 제공

효과:

- 각 라우터에서 `create_engine(...)` 반복 제거
- 엔진 생성 정책 일관화
- 나중에 connection/session 전략 변경 시 수정 지점 단일화

### 6.2 크롤링 제어 서비스

신규 파일 제안:

- `services/crawl_control.py`

책임:

- 크롤링 start/kill/resume/enqueue
- queue 파일 작성
- 명령행 구성
- 로그 파일 초기화 및 회전 호출
- 종료 후 후처리 연결
- Web/API/스케줄러 공용 인터페이스 제공

효과:

- `web/routers/crawl.py`, `web/routers/api_route.py`, `core/utils/scheduler.py` 의 중복 제거
- 크롤링 동작 정책을 한 곳에서 수정 가능

### 6.3 크롤러 API 클라이언트

신규 파일 제안:

- `core/crawler/api_client.py`

책임:

- direct_login 세션 생성
- browser fallback 준비
- 목록 API 호출
- 상세 API 호출
- 401 재로그인 후 재시도
- 브라우저 컨텍스트 공용 처리

효과:

- `crawltitle_api.py` 와 `crawldetail_api.py` 의 fetch 책임 분리
- 테스트 가능한 경계 생성

### 6.4 Title 변환 파이프라인

신규 파일 제안:

- `core/crawler/title_pipeline.py`

책임:

- API 응답 또는 Selenium row 를 표준 title row 로 변환
- 상태, 신고번호, 신고명, 신고일, 만족도조사여부 계산

효과:

- 목록 크롤링 결과 형식 일관화
- API/legacy 간 normalization 공통화

### 6.5 Detail 변환 파이프라인

신규 파일 제안:

- `core/crawler/detail_pipeline.py`

책임:

- API 상세 데이터 또는 HTML 파싱 결과를 표준 detail row 로 변환
- `title_fields` 구성
- `category_from_entry_value` 계산
- 만족도조사 보강 공용 처리

효과:

- `crawldetail.py` 와 `crawldetail_api.py` 공통화
- 테스트 포인트 명확화

### 6.6 Report Query Service

신규 파일 제안:

- `services/report_query_service.py`

책임:

- 목록 조회
- 차량번호 검색
- 주소 검색
- 감시목록 조회
- 미별점 대상 조회
- 신고번호/ID 해석

효과:

- `data_service.py` 의 조회 책임 축소
- 라우터와 통계 로직 분리

### 6.7 Report Stats Service

신규 파일 제안:

- `services/report_stats_service.py`

책임:

- 대시보드 통계
- 기관/담당자/법규 통계

효과:

- `data_service.py` 가 조회와 통계를 동시에 떠안는 구조 해소

### 6.8 Crawl State Store

신규 파일 제안:

- `services/crawl_state_store.py`

책임:

- `crawl_changes.json`
- `crawl_done.json`
- `crawl_done_ext.json`
  의 저장/조회/삭제

효과:

- 파일 기반 상태 저장 로직을 `data_service.py` 에서 분리
- 상태 저장 매체 변경 시 확장 용이

### 6.9 File Service

신규 파일 제안:

- `services/file_service.py`

책임:

- 허용 루트 계산
- 파일 목록 조회
- 다운로드 검증
- zip 다운로드
- live log snapshot 생성
- 보호 파일 삭제 제한

효과:

- 웹 파일 브라우저와 API 파일 브라우저 로직 공통화

### 6.10 Rating Service

신규 파일 제안:

- `services/rating_service.py`

책임:

- 별점 대상 해석
- 크롤링 충돌 체크
- 로그 파일 준비/회전
- 백그라운드 실행

효과:

- `rating_route.py` 와 `api_route.py` 중복 제거

### 6.11 Export Service

신규 파일 제안:

- `services/export_service.py`

책임:

- DB 결과 로드
- DataFrame 전처리
- Excel 저장
- Google Sheet 업로드

효과:

- `start.py` 와 `web/routers/crawl.py` 의 중복 제거


## 7. 성능 관점 개선 포인트

이번 구조 개선은 단순한 코드 정리에 그치지 않고, 몇몇 지점에서 실제 성능 개선 효과를 기대할 수 있다.

### 7.1 전체 테이블 로드 후 필터링 구조 축소

현재 `services/data_service.py` 의 여러 함수는 다음 흐름을 따른다.

1. merge 테이블 전체를 pandas 로 읽는다.
2. Python/pandas 에서 상태, 기관, 날짜, 법규 등을 필터링한다.
3. dict 로 변환해 반환한다.

대표 함수:

- `_get_records_from_table`
- `get_dashboard_stats`
- `get_agency_stats`
- `get_all_records`
- `resolve_to_report_numbers`

문제:

- 데이터가 커질수록 CPU와 메모리 낭비가 커진다.
- 목록 조회, 검색, 통계 페이지 응답이 늦어질 수 있다.

개선 방향:

- 상태, 기관, 담당자, 날짜, 법규, 신고번호, 차량번호 같은 필터는 SQL `WHERE` 로 우선 적용한다.
- pandas 는 최종 집계나 복잡한 후처리에만 사용한다.

예상 효과:

- 조회 성능 개선
- 메모리 사용량 감소
- 응답 지연 완화

### 7.2 `get_all_records` 의 Python 병합 최소화

현재:

- `traffic + parking + other` 를 각각 전부 읽은 뒤 Python list 로 합친다.

개선 방향:

- 장기적으로는 SQL `UNION ALL` 기반 조회 또는 통합 view 를 고려한다.

효과:

- 전체 목록 조회 비용 감소

### 7.3 신고번호/ID 해석의 전체 로드 제거

현재 `resolve_to_report_numbers` 는 3개 merge 테이블의 ID/신고번호를 다 읽어서 pandas 로 해석한다.

개선 방향:

- 입력된 값들만 기준으로 SQL 조회
- 신고번호/ID 각각에 대한 조건 조회로 축소

효과:

- 별점 시작, 감시목록 추가 등에서 불필요한 전체 스캔 감소

### 7.4 `merge_final` 의 전량 재생성 전략 개선

현재 `merge_final` 은 매번 merge 테이블 전체 delete 후 다시 insert 한다.

문제:

- 단순하고 안전하지만 데이터 규모가 커질수록 비효율적이다.

개선 방향:

- 단기: 현 구조 유지
- 장기: 변경 ID 기반 증분 merge 또는 merge view 검토

주의:

- 이 부분은 리스크가 커서 후순위로 둔다.

### 7.5 엔진/연결 생성 일원화

현재 여러 라우터가 개별적으로 `create_engine` 을 호출한다.

개선 방향:

- `core/database/engine.py` 로 일원화

효과:

- 초기화 비용 관리
- 설정 일관화
- 추후 DB 전환 또는 pool 정책 변경이 쉬워짐


## 8. 단계별 Implementation Plan

### Phase 0. 안전망 확보

목표:

- 구조를 건드리기 전에 현재 동작을 비교할 수 있는 최소 안전망을 만든다.

작업:

1. API 방식과 legacy 방식이 같은 입력에서 같은 표준 row 를 생성하는지 비교용 테스트 또는 스냅샷 준비
2. 크롤링 완료 후 `title`, `detail`, `merge` 결과의 핵심 필드 비교 포인트 정리
3. 주요 엔드포인트 수동 점검 체크리스트 작성

완료 기준:

- 리팩토링 전후 비교 대상이 최소한 정의되어 있어야 한다.

### Phase 1. 저위험 공용 유틸 통합

목표:

- 중복이 명확하고 기능 리스크가 낮은 부분부터 정리한다.

작업:

1. `core/database/engine.py` 추가
2. 라우터별 `_get_engine` 제거 및 공용 엔진 사용
3. retry 설정 유틸 통합
4. `_ensure_browser_context` 공용화
5. 로그 회전 유틸 공용화
6. 오타 함수명, 내장형 가리는 파라미터명 정리

완료 기준:

- 중복 유틸 함수가 눈에 띄게 줄고, 공용 함수 위치가 명확해야 한다.

### Phase 2. 서비스 계층 분리

목표:

- 라우터가 직접 로직을 들고 있는 구조를 정리한다.

작업:

1. `services/crawl_control.py` 추가
2. `services/rating_service.py` 추가
3. `services/file_service.py` 추가
4. `services/export_service.py` 추가
5. `web/routers/crawl.py` 와 `web/routers/api_route.py` 에서 서비스 호출 형태로 변경

완료 기준:

- 라우터는 요청 파싱과 응답 생성만 담당하고, 실제 동작은 서비스가 담당해야 한다.

### Phase 3. 크롤러 파이프라인 통합

목표:

- API/legacy 크롤러의 중복을 줄이고 구조를 provider + pipeline 형태로 바꾼다.

작업:

1. `core/crawler/api_client.py` 추가
2. `core/crawler/title_pipeline.py` 추가
3. `core/crawler/detail_pipeline.py` 추가
4. `crawltitle_api.py` 와 `crawldetail_api.py` 에서 fetch 책임만 남긴다
5. `crawldetail.py` 와 `crawldetail_api.py` 의 공통 detail row 생성 흐름 통합
6. 만족도조사 보강 로직을 공용 helper 로 이동

완료 기준:

- API/legacy 경로가 "데이터를 가져오는 방법" 만 다르고, 이후 변환 흐름은 공용화되어야 한다.

### Phase 4. `data_service.py` 분해

목표:

- 거대 서비스 모듈을 책임별로 분리한다.

작업:

1. `services/report_query_service.py` 로 조회/검색/감시목록 이동
2. `services/report_stats_service.py` 로 통계 이동
3. `services/crawl_state_store.py` 로 JSON 상태 파일 로직 이동
4. 남은 공용 포맷/정규화 함수 위치 재배치

완료 기준:

- `data_service.py` 가 더 이상 "만능 모듈" 이 아니어야 한다.

### Phase 5. SQL pushdown 및 성능 개선

목표:

- 구조가 정리된 후, 병목이 큰 조회 로직을 성능 기준으로 개선한다.

작업:

1. `_get_records_from_table` 필터를 SQL 조건식 중심으로 재작성
2. `search_by_vehicle`, `search_by_address`, `resolve_to_report_numbers` 최적화
3. `get_dashboard_stats`, `get_agency_stats` 중 일부 집계를 SQL 또는 hybrid 방식으로 전환
4. 필요 시 캐시 또는 precomputed summary 검토

완료 기준:

- 대시보드/목록/통계 조회 시 전체 DataFrame 로드 빈도가 줄어야 한다.

### Phase 6. 후속 구조 개선

목표:

- 데이터 규모 증가에 대비한 장기 개선 작업을 검토한다.

작업:

1. `merge_final` 증분화 가능성 검토
2. merge 테이블 대신 view 또는 materialized-like 전략 검토
3. 로깅, WS broadcast, background task 표준화

완료 기준:

- 대량 데이터 기준에서도 확장 가능한 구조 방향이 정리되어야 한다.


## 9. 우선순위

### 최우선

1. 공용 DB 엔진 모듈 도입
2. 크롤링 제어 서비스 분리
3. 로그 회전/후처리 공용화
4. rating/file/export 서비스 분리
5. API/legacy detail 공통 파이프라인 도입

### 중간 우선순위

1. `data_service.py` 조회/통계 분리
2. 파일 브라우저 웹/API 공통화
3. retry/fallback 유틸 정리
4. 함수명 및 파라미터명 정리

### 후순위

1. `merge_final` 증분화
2. 통계 캐시
3. 통합 query layer 고도화


## 10. 예상 파일 변경 범위

### 신규 파일

- `core/database/engine.py`
- `core/crawler/api_client.py`
- `core/crawler/title_pipeline.py`
- `core/crawler/detail_pipeline.py`
- `services/crawl_control.py`
- `services/rating_service.py`
- `services/file_service.py`
- `services/export_service.py`
- `services/report_query_service.py`
- `services/report_stats_service.py`
- `services/crawl_state_store.py`

### 주요 수정 파일

- `start.py`
- `main.py`
- `core/crawler/crawltitle.py`
- `core/crawler/crawltitle_api.py`
- `core/crawler/crawldetail.py`
- `core/crawler/crawldetail_api.py`
- `core/crawler/direct_login.py`
- `services/satisfaction_fetcher.py`
- `services/data_service.py`
- `services/crawl_manager.py`
- `core/database/database.py`
- `core/utils/scheduler.py`
- `web/routers/api_route.py`
- `web/routers/crawl.py`
- `web/routers/file_browser_route.py`
- `web/routers/rating_route.py`
- `web/routers/auth_route.py`
- `web/routers/devices_route.py`
- `web/routers/ws_route.py`


## 11. 리스크와 대응

### 11.1 크롤링 결과 형식 변경 리스크

설명:

- API/legacy 공통화 과정에서 title/detail row 형식이 미세하게 달라질 수 있다.

대응:

- 공통 파이프라인 도입 전후 결과 비교 테스트 수행

### 11.2 상태 저장 파일 호환성 리스크

설명:

- `crawl_changes.json`, `crawl_done.json`, `crawl_done_ext.json` 처리 위치가 바뀌면 모바일/확장 기능에 영향이 갈 수 있다.

대응:

- 파일 포맷은 유지하고, 저장 위치나 키를 바꾸지 않는다.

### 11.3 DB merge 로직 회귀 리스크

설명:

- `detail_to_sql`, `merge_final` 변경은 데이터 손상 가능성이 있다.

대응:

- 초기 단계에서는 동작을 유지하고 이름/구조만 정리
- 증분 merge 는 후순위로 분리

### 11.4 라우터 분리 후 호출 흐름 누락 리스크

설명:

- 서비스로 이동하면서 websocket broadcast 또는 로그 초기화가 빠질 수 있다.

대응:

- 크롤링 시작/종료/중지/재개 시나리오별 체크리스트 작성


## 12. 완료 기준

이번 Implementation Plan 의 완료 기준은 다음과 같다.

1. 라우터가 직접 비즈니스 로직을 거의 갖지 않는다.
2. API/legacy 크롤링 경로는 fetch 전략만 다르고 normalize 흐름은 공통화된다.
3. DB 엔진 생성과 로그 회전 정책이 한 곳으로 모인다.
4. `services/data_service.py` 의 책임이 조회/통계/상태 저장으로 분리된다.
5. 자주 호출되는 조회는 전체 pandas 로드 의존도가 줄어든다.
6. 기존 모바일/API/웹 기능이 동일하게 유지된다.


## 13. 1차 실행 권장 범위

실제 착수 시 가장 효율적인 1차 범위는 아래와 같다.

1. `core/database/engine.py` 도입
2. `services/crawl_control.py` 도입
3. `services/rating_service.py`, `services/file_service.py`, `services/export_service.py` 도입
4. `web/routers/crawl.py`, `web/routers/api_route.py`, `web/routers/rating_route.py`, `web/routers/file_browser_route.py` 를 서비스 호출형으로 변경
5. `core/crawler/detail_pipeline.py` 를 먼저 만들어 API/legacy 상세 크롤링 공통화 시작
6. 이후 `data_service.py` 분해와 SQL pushdown 으로 확장

이 순서는 리스크 대비 체감 효과가 가장 크다.


## 14. 모바일 Client 영향 범위

모바일 Client 모드는 별도 저장소 `../safetyreport-mobile` 에 존재하며, 이번 서버 리팩토링의 여러 항목과 직접 연결되어 있다.

즉, 이번 리팩토링은 서버 코드만 정리하면 끝나는 작업이 아니라, 모바일이 의존하는 서버 계약을 함께 관리해야 하는 작업이다.

### 14.1 직접 영향 받는 서버 계약

다음 항목은 모바일 앱이 직접 의존하는 계약이다.

#### 인증/공개 경로

- `main.py`
  - `/api/v1/`
  - `/ws/`
  - 세션 인증 우회가 유지되어야 한다.

중요성:

- 모바일은 관리자 세션이 아니라 API 키 기반으로 동작한다.
- 이 우회가 깨지면 모바일은 302 로그인 리다이렉트 또는 401/500 형태로 연쇄 실패할 수 있다.

#### API 키 인증 방식

- `web/routers/api_route.py`
  - `X-API-Key` 헤더 인증
  - 일부 다운로드 경로의 `?api_key=` 쿼리 파라미터 허용

중요성:

- 모바일 파일 다운로드와 DB 다운로드는 헤더/쿼리 방식 모두 영향을 받을 수 있다.
- 인증 로직 공용화 시 `flex` 인증 동작을 유지해야 한다.

#### WebSocket 계약

- `web/routers/ws_route.py`
- `services/ws_manager.py`

모바일 의존 요소:

- URL: `/ws/events?api_key=<key>`
- 이벤트 타입:
  - `connected`
  - `ping`
  - `crawl_started`
  - `crawl_finished`
  - `crawl_changes`
- payload 구조:
  - 최상위 `type`
  - 최상위 `data`
  - `crawl_changes` 의 `changes`

중요성:

- Android foreground service 가 이 이벤트 타입을 직접 파싱한다.
- 타입명, URL, query parameter 명, payload 키가 바뀌면 모바일 푸시/이력/카드 시트가 깨진다.

#### 크롤링 상태 파일 계약

- `services/data_service.py`
  - `crawl_changes.json`
  - `crawl_done.json`
  - `crawl_done_ext.json`
- `services/crawl_manager.py`
- `web/routers/api_route.py`

모바일 의존 요소:

- `/api/v1/crawl/results`
- `/api/v1/crawl/done`
- `/api/v1/crawl/status`
- `/api/v1/crawl/start`
- `/api/v1/crawl/kill`
- `/api/v1/crawl/resume`
- `/api/v1/crawl/enqueue`

중요성:

- 서버의 내부 상태 저장 위치와 읽기/삭제 타이밍이 바뀌면 모바일 알림 중복, 누락, poll 실패가 발생할 수 있다.

#### 파일 브라우저 계약

- `web/routers/api_route.py`
  - `/api/v1/files`
  - `/api/v1/files/download`

중요성:

- 모바일 파일 탭은 경로 목록과 다운로드 응답 형식에 의존한다.
- 특히 `current_crawl.log`, `current_rating.log` 는 live snapshot 동작을 유지해야 한다.

#### 별점 API 계약

- `web/routers/api_route.py`
  - `/api/v1/rating/start`

중요성:

- 모바일은 웹 `/rating/start` 가 아니라 API 키 기반 `/api/v1/rating/start` 를 사용한다.
- 응답의 `status`, `message`, 오류 시 `detail` 해석 방식이 유지되어야 한다.

#### DB 백업/복원 계약

- `web/routers/api_route.py`
  - `/api/v1/settings/db`
  - `/api/v1/settings/db/upload`
- `services/db_backup.py`
  - `restore_from_mobile_db()`

중요성:

- 모바일은 서버 DB 다운로드뿐 아니라, 모바일 DB 형식을 서버로 업로드해 복원하는 흐름도 갖고 있다.
- DB 형식 감지, `kind` 값, `imported`, `backup` 응답 키가 유지되어야 한다.

#### 모바일 대시보드/리스트/통계 계약

- `web/routers/api_route.py`
  - `/api/v1/summary`
  - `/api/v1/reports/{category}`
  - `/api/v1/stats`
  - `/api/v1/watchlist`
  - `/api/v1/sunwi/payload`
  - `/api/v1/app/config`
  - `/api/v1/server/version`
- `services/data_service.py`

중요성:

- 모바일은 응답 JSON 구조와 일부 필드의 존재 여부에 직접 의존한다.
- 특히 최근 변경 작업으로 추가된 `category` 필드는 모바일 상세 링크/탭 복귀에 중요하다.

### 14.2 모바일이 직접 사용하는 파일

서버 리팩토링 시 함께 확인해야 하는 모바일 파일은 다음과 같다.

- `../safetyreport-mobile/lib/services/api_service.dart`
- `../safetyreport-mobile/lib/screens/setup_screen.dart`
- `../safetyreport-mobile/lib/screens/settings_screen.dart`
- `../safetyreport-mobile/lib/screens/file_browser_screen.dart`
- `../safetyreport-mobile/lib/providers/report_provider.dart`
- `../safetyreport-mobile/lib/models/report.dart`
- `../safetyreport-mobile/android/app/src/main/kotlin/com/fentanest/mysafetyreport/WsService.kt`
- `../safetyreport-mobile/android/app/src/main/kotlin/com/fentanest/mysafetyreport/NotificationService.kt`

### 14.3 상대적으로 영향이 적은 서버 변경

다음 변경은 모바일에 직접 노출되는 계약이 아니라서 영향이 비교적 적다.

- 내부 helper 이름 정리
  - `_get_all_title_ids`
  - `_get_initial_scan_ids`
  - `deatil_to_sql` → `detail_to_sql`
- crawler 내부 공통화
  - fetch/provider/pipeline 분리
- engine 생성 위치 통합

단, 내부 리팩토링이라도 응답 데이터나 상태 저장 타이밍이 바뀌면 간접 영향은 발생할 수 있다.


## 15. 모바일 병행 리팩토링 계획

서버 리팩토링과 별개가 아니라, 모바일은 아래 범위까지 함께 손보는 것을 기본 계획으로 한다.

### 15.1 서버 계약 상수화

모바일 저장소에서 다음 내용을 한 곳으로 모은다.

- 서버 API 경로 상수
- WebSocket 경로 상수
- API 키 헤더 이름
- `api_key` query parameter 이름
- 서버 응답 envelope (`status`, `data`, `detail`) 처리 규칙

권장 신규 파일:

- `../safetyreport-mobile/lib/services/server_contract.dart`

목표:

- `/api/v1/...` 하드코딩이 여러 화면/서비스에 흩어지지 않도록 정리
- 서버 경로 변경 또는 리팩토링 시 모바일 수정 범위를 최소화

### 15.2 Dart API 계층 정리

대상 파일:

- `../safetyreport-mobile/lib/services/api_service.dart`

작업:

1. URI 생성 로직 공통화
2. API 키 헤더 구성 공통화
3. 성공/실패 응답 파싱 공통화
4. `summary`, `reports`, `stats`, `watchlist`, `crawl`, `rating`, `files`, `settings/db`, `sunwi` 경로를 contract 기반으로 참조

목표:

- 서버 구조 변경 시 `ApiService` 가 모바일의 단일 적응 계층이 되도록 만든다.

### 15.3 연결 테스트/설정 화면 정리

대상 파일:

- `../safetyreport-mobile/lib/screens/setup_screen.dart`
- `../safetyreport-mobile/lib/screens/settings_screen.dart`

작업:

1. 연결 테스트용 `/api/v1/summary` 하드코딩 제거
2. 서버 버전 확인 `/api/v1/server/version` 하드코딩 제거
3. API 키 헤더 이름 중복 제거
4. 리다이렉트/401/HTML 응답 탐지 메시지는 유지

목표:

- 초기 연결/설정 흐름도 contract helper 를 사용하도록 일관화

### 15.4 파일 다운로드 경로 정리

대상 파일:

- `../safetyreport-mobile/lib/screens/file_browser_screen.dart`

작업:

1. `/api/v1/files/download` 경로 하드코딩 제거
2. API 키 헤더 이름 공통화
3. 라이브 로그 다운로드가 snapshot 전제를 가진다는 점을 주석으로 명시

목표:

- 서버 파일 서비스 리팩토링 시 모바일 파일 화면 수정 범위를 최소화

### 15.5 Android 네이티브 WebSocket/알림 서비스 정리

대상 파일:

- `../safetyreport-mobile/android/app/src/main/kotlin/com/fentanest/mysafetyreport/WsService.kt`
- `../safetyreport-mobile/android/app/src/main/kotlin/com/fentanest/mysafetyreport/NotificationService.kt`

작업:

1. `/ws/events`
2. `api_key`
3. `/api/v1/crawl/enqueue`
4. 이벤트 타입 문자열
   - `crawl_started`
   - `crawl_finished`
   - `crawl_changes`
   - `ping`
   - `connected`
   를 상수화

목표:

- 서버 이벤트 경로나 타입을 정리할 때 네이티브 코드 추적을 쉽게 한다.

### 15.6 모바일 모델/파서 보호

대상 파일:

- `../safetyreport-mobile/lib/models/report.dart`
- `../safetyreport-mobile/lib/providers/report_provider.dart`

확인 항목:

- `category`
- `watchlist`
- `recent_answers`
- `changed_count`
- `changes`
- `avg_rating`
- `rating_count`

목표:

- 서버 응답 필드가 일부 누락되더라도 가능한 범위에서 fallback 이 동작하도록 보호


## 16. 서버-모바일 통합 실행 순서

실제 작업은 아래 순서로 진행하는 것을 권장한다.

1. 서버에서 계약 변경이 없는 내부 리팩토링부터 수행한다.
   - engine 통합
   - helper 정리
   - crawler 내부 공통화

2. 모바일에서 서버 계약 상수화 작업을 먼저 수행한다.
   - `server_contract.dart`
   - `ApiService` 경로 정리
   - setup/settings/file browser 경로 정리

3. 서버에서 `crawl_control`, `file_service`, `rating_service`, `crawl_state_store` 분리를 수행한다.
   - 이 단계에서는 URL, 응답 키, 이벤트 타입을 유지한다.

4. Android 네이티브 `WsService.kt`, `NotificationService.kt` 도 상수 기반으로 정리한다.

5. 서버와 모바일을 함께 연동 테스트한다.


## 17. 모바일 호환성 체크리스트

서버 리팩토링 후 최소 다음 항목을 확인한다.

1. 모바일 설정 화면에서 `/api/v1/summary` 연결 테스트가 성공한다.
2. 앱 시작 후 대시보드 `/api/v1/summary` 로딩이 정상 동작한다.
3. `/ws/events?api_key=` 연결이 유지되고 `connected`, `ping` 처리가 정상 동작한다.
4. 크롤링 시작/완료/변경 이벤트가 푸시 알림과 히스토리에 정상 반영된다.
5. `/api/v1/crawl/start`, `/kill`, `/resume`, `/status`, `/results`, `/done` 이 정상 동작한다.
6. `/api/v1/watchlist` 조회/추가/삭제가 정상 동작한다.
7. `/api/v1/rating/start` 호출과 `current_rating.log` 조회가 정상 동작한다.
8. `/api/v1/files` 목록과 `/api/v1/files/download` 다운로드가 정상 동작한다.
9. `/api/v1/settings/db` 다운로드와 `/api/v1/settings/db/upload` 복원이 정상 동작한다.
10. `/api/v1/stats`, `/api/v1/sunwi/payload`, `/api/v1/app/config`, `/api/v1/server/version` 이 정상 동작한다.


## 18. Definition of Done 확장

이번 리팩토링의 완료 기준은 서버 단독 완료가 아니라, 아래 조건까지 포함한다.

1. 서버 웹 UI 기능이 유지된다.
2. 모바일 Client 모드의 API/WS/파일/별점/크롤링/DB 복원 기능이 유지된다.
3. 모바일 저장소에서 서버 계약 경로가 과도하게 분산되어 있지 않다.
4. 서버와 모바일 모두에서 경로/이벤트/응답 계약이 문서화되어 있다.


## 19. 서버 릴리즈 체크리스트

### 19.1 배포 전

1. `python3 -m compileall core services web scripts/debug main.py start.py bot.py` 가 통과한다.
2. `core/database/engine.py` 기반 공용 엔진 전환 후에도 서버가 정상 부팅된다.
3. `services/crawl_control.py`, `services/crawl_state_store.py`, `services/export_service.py`, `services/file_service.py`, `services/rating_service.py` 가 import 오류 없이 로드된다.
4. `services/report_query_service.py`, `services/report_stats_service.py` 분리 후 기존 `services/data_service.py` import 경로가 깨지지 않는다.
5. 현재 운영 DB의 백업본이 있다.
6. `data/backups/` 와 `data/logs/` 쓰기 권한이 보장된다.

### 19.2 배포 직전 수동 점검

1. 웹 대시보드 `/` 가 열린다.
2. `/api/v1/summary` 가 200을 반환한다.
3. `/api/v1/server/version` 이 200을 반환한다.
4. `/api/v1/app/config` 가 200을 반환한다.
5. `/api/v1/files` 와 `/api/v1/files/download` 가 동작한다.
6. `/api/v1/watchlist` 조회/추가/삭제가 동작한다.
7. `/api/v1/rating/start` 호출 시 로그 파일 회전과 작업 시작이 정상 동작한다.
8. `/api/v1/crawl/start`, `/api/v1/crawl/kill`, `/api/v1/crawl/resume`, `/api/v1/crawl/status` 가 동작한다.
9. `/ws/events` 연결 후 `connected`, `ping`, `crawl_started`, `crawl_finished`, `crawl_changes` 이벤트가 정상 전송된다.
10. 통계 화면 `/stats` 에서 연도, 날짜 범위, 기관, 경찰 제외/포함, 법규 필터가 정상 동작한다.

### 19.3 배포 후 확인

1. 최근 크롤링 1회를 실제로 수행해 `crawl_done.json`, `crawl_done_ext.json`, `crawl_changes.json` 흐름이 정상인지 확인한다.
2. 결과 엑셀 저장과 구글 시트 업로드가 기존과 동일하게 동작하는지 확인한다.
3. `current_crawl.log`, `current_rating.log` live 다운로드가 snapshot 기반으로 정상 동작하는지 확인한다.
4. 운영 로그에 `ImportError`, `AttributeError`, `KeyError` 가 없는지 확인한다.
5. 통계 페이지 응답 시간이 리팩토링 전보다 악화되지 않았는지 확인한다.


## 20. 모바일 릴리즈 체크리스트

### 20.1 배포 전

1. `dart format` 이 적용되어 있다.
2. `dart analyze` 에서 새로 추가된 오류가 없다.
3. `lib/services/server_contract.dart` 와 Android `ServerContract.kt` 가 같은 경로/이벤트 값을 사용한다.
4. `ApiService`, `SetupScreen`, `SettingsScreen`, `FileBrowserScreen` 이 contract helper 를 사용하도록 정리되어 있다.
5. `WsService.kt`, `NotificationService.kt` 가 하드코딩 경로 대신 contract 상수를 사용한다.

### 20.2 Client 모드 점검

1. 설정 화면에서 서버 URL + API Key 연결 테스트가 성공한다.
2. 대시보드가 `/api/v1/summary` 로 정상 로드된다.
3. 통계 화면이 `/api/v1/stats` 로 정상 로드된다.
4. 감시목록 조회/추가/삭제가 정상 동작한다.
5. 파일 브라우저 목록과 다운로드가 정상 동작한다.
6. 크롤링 시작/중지/재개/상태 조회가 정상 동작한다.
7. 별점 일괄 시작과 `current_rating.log` 조회가 정상 동작한다.
8. 서버 버전 확인이 정상 동작한다.
9. DB 다운로드와 업로드가 정상 동작한다.

### 20.3 백그라운드/네이티브 점검

1. `WsService` 가 앱 백그라운드 상태에서도 `/ws/events` 연결을 유지한다.
2. `connected`, `ping` 이벤트 처리 후 연결이 끊기지 않는다.
3. `crawl_started`, `crawl_finished`, `crawl_changes` 가 푸시 알림으로 정상 표시된다.
4. 외부 앱 알림 감지 후 `NotificationService` 가 `/api/v1/crawl/enqueue` 를 정상 호출한다.
5. auto enqueue 시 `crawl_started` / `crawl_finished` 억제 로직이 기존처럼 동작한다.
6. `pending_crawl_changes` 누적과 앱 내 카드/히스토리 반영이 정상 동작한다.


## 21. 배포 순서와 롤백 기준

### 21.1 권장 배포 순서

1. 서버를 먼저 배포한다.
2. 서버에서 API/WS 계약이 유지되는지 수동 점검한다.
3. 모바일 Client 모드로 실제 연동 테스트를 수행한다.
4. Android 빌드를 새 contract helper 기준으로 배포한다.

### 21.2 롤백 기준

다음 중 하나라도 발생하면 서버 롤백을 우선 검토한다.

1. `/api/v1/summary` 또는 `/api/v1/app/config` 가 500을 반환한다.
2. `/ws/events` 연결이 되지만 `connected` 또는 `ping` 처리에서 바로 끊긴다.
3. 크롤링 완료 후 모바일에 `crawl_finished` 가 도착하지 않는다.
4. `crawl_changes.json` 이 비워지지 않거나 이전 변경사항이 재전송된다.
5. 파일 다운로드나 DB 다운로드가 live snapshot 대신 사용 중 파일 잠금 문제를 일으킨다.

### 21.3 롤백 시 보존 항목

1. 현재 `data.db` 와 `data/backups/` 는 유지한다.
2. `data/logs/` 는 원인 분석 전 삭제하지 않는다.
3. 모바일에서는 기존 API 경로 계약이 유지되므로, 서버만 롤백해도 Client 모드가 다시 붙을 수 있어야 한다.
