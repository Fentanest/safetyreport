# 크롤링·파싱·처리 규칙

다루는 것: start.py 파이프라인, 파서 규칙, 카테고리 분류, 첨부 URL, 별점 API, 감시목록, 디버그 extractor.

## 상세 파서 단계 (EO R-11, 2026-10-05)
`services/parser.py` `parse_json_details` 는 반환 dict 를 그대로 두고 순수 단계로 나눴다: `_extract_body`(본문·메뉴·차량번호·발생일시·신고 내용) →
`_apply_completed_supplement`(공식 위치·좌표, 완료된 보완의 차량번호·일시·위치와 같은 보완 좌표) → `_attachments` → `_select_answer`(마지막 답변: 상태·기관·기관코드 원문·담당자·답변일·본문) →
`_violation_law`·`_penalty`·`_apply_penalty_corrections`·`_final_status`(canonical 상태 순서) → 취하 처분 제거 → `supplement_case_fine`(빈 처분/미확인만 보완) → `_supplement`·`_title_fields`. 입력 dict 를 바꾸지 않는다.
분리 전후 결과는 공용 파서 벡터와 그 변형 1만 1,600건에서 같았다(예외가 나던 입력은 같은 예외). 모바일 standalone_parser.dart 와 같은 벡터를 쓴다.

## 상세 원본 저장 (2026-10-17 명세 1절, 스키마 7)

- `core/crawler/crawldetail_api.py` 의 `crawl_details` 는 상세 API 응답 envelope(`data`)를 받은 직후
  `core/utils/raw_detail.py` 로 정규화(신고자 키 5개 제거 → 공백 없는 JSON → sha256)해
  `core/crawler/detail_pipeline.py` 의 `build_detail_result` 결과 튜플 10번째(`raw_info`)에 실는다.
  `raw_info` 의 `raw_source` 는 호출 경로가 넘긴다(초기화 `rebuild`·큐 지정 `notification`·일반 `crawl`).
- `core/storage/reports_repo.py` 의 `CrawledDetail.from_legacy_tuple` 은 10번째 원소를 읽고(없어도 됨),
  `_save_one` 의 같은 트랜잭션에서 `mysafety_report_detail_raw` 에 upsert 한다(초기화·일반·단건 모두).
  파서 입력은 지금처럼 `result` 이다. 파서는 신고자 키 5개를 쓰지 않는다(`tests/test_raw_detail_vectors.py`).
- 원본 파일 `<datapath>/raw_details/<ID>.json`(들여쓰기 2칸, 임시 파일→rename)은 DB 커밋 뒤에 쓰고,
  실패해도 DB 저장을 막지 않고 대체 진단 기록(`note_fallback`)만 남긴다. 파일 쓰기 호출 자체도 예외를 밖으로 내지 않게
  감싸, DB 는 저장됐는데 저장 실패로 보고되는 일이 없다. ID 파일 이름 규칙·경로 제한은 `core/utils/raw_detail.py`.
- 계약 벡터 `contracts/raw-detail-vectors.json`, 열 계약 `contracts/storage-contract.json`(`detail_raw`).

## DB 최적화 작업 (2026-10-17 명세 3절, 백그라운드, 취소 불가)

- `services/db_optimize.py`. DB 메타(`mysafety_sync_meta`) 키 `detail_raw_optimize` =
  `{"target": "<파서 버전>", "cursor": "<마지막 처리 ID>", "done": bool, "total": int, "stats": {...}}`.
  기동 때(`main.py`, 스키마 업그레이드 뒤) `target != 현재 파서 버전(pc-parser-13)`이거나 `done`이 아니면
  백그라운드 스레드로 시작/이어서 한다. 할 일 없으면 아무 일도 하지 않는다. 이번 업데이트는
  파서 버전을 `pc-parser-13`으로 올려 첫 실행에 한 번 돈다.
- 시작 전·신고 사이: 크롤이 돌고 있으면 끝난 뒤 시작하고, 초기화(rebuild)가 필요하거나 돌고 있으면
  초기화가 끝난 뒤 돈다. 크롤이 끼어들면(초기화 크롤) 기다렸다가 이어 간다.
- 신고마다(ID 순, 커서로 이어서): 복원과 최적화는 동시에 돌지 않는다(상호 배제).
  DB 교체 경로(`exchange.restore`·카카오 계정 자료 비움·공식 계정 변경)는 장벽을 잡기 전에
  `db_optimize.pause_for_restore()`로 복원 진행 표시를 켜고 실행 중 최적화에 멈춤을 요청해
  현재 처리 중인 신고 1건(네트워크 재조회 중이면 그 저장을 하지 않고)을 끝내고 스레드가
  끝날 때까지 기다린다(60초 기본, 넘으면 복원을 명확한 오류로 거절하고 표시 해제).
  최적화 스레드는 매 신고 시작·저장 직전에 멈춤 요청을 확인한다. 표시가 켜져 있는 동안
  `maybe_start`는 시작하지 않으며, 멈춰 끝난 옛 스레드를 반환하지 않고 복원 뒤 새 DB로
  새 스레드를 시작한다. DB 정체(시작 때 기록한 community 데이터셋 id·장벽 세대) 확인은
  방어용으로 유지한다 — 다르면 아무 것도 쓰지 않고 멈춘다.
  복원이 끼면 현재 신고를 끝내고 멈춘다
  — 옛 ID 목록·커서·done·옛 신고 내용을 복원된 DB에 쓰지 않고, 복원된 DB의 메타를 새로 읽어 처음부터 판단한다.
  1. 원본 표에 행이 있고 sha가 맞으면 네트워크 없이 저장된 원본의 `result`로 다시 파싱한다.
     sha가 안 맞으면(손상) 로그를 남기고 없는 것과 같이 다시 받는다.
     `target`이 바뀌었으면 커서·통계·시도횟수를 버리고 처음부터 훑는다.
  2. 행이 없으면 `direct_login.make_authorized_session()` 세션으로 `_fetch_detail`을 다시 받아
     (크롤과 같은 요청 간격 0.3초·`request_with_retry` 재시도) 1절 규칙대로 원본 표(`source='backfill'`)·파일에
     저장하고 파싱한다. 세션을 못 만들면(로그인 필요) 작업을 멈추고 `stage='login_required'`로 보인 뒤
     주기적으로 재시도한다. 없는 신고·삭제(영구, 404 등)만 사유를 남기고 건너뛴다.
     일시 실패(네트워크·타임아웃·저장 실패·로그인 갱신 중 예외)는 메타 `attempts`에 신고별
     시도 횟수를 남기고 커서는 넘기되 이번 회차 끝·다음 기동에 다시 시도한다.
     3회 실패는 `skipped.retry_exceeded`로 건너뛴다. `done=true`는 전부 처리됐거나
     영구·재시도 초과 건너뜀일 때만 기록한다.
  3. `reports_repo.reparse_one` (공유 trigger `'reparse'`): 파서가 만드는 열만 갱신한다
     (상세 사이트 열·분류·본문 + 좌표 GEO 5열을 파싱 결과에서 일반 저장과 같은 함수·같은 규칙
     (`geocode_service.official_geo_payload(위반장소, 위도, 경도)`)으로 계산해 쓰기 잠금 안에서 씀,
     목록 크롤이 채우는 상태·신고번호·신고명·신고일·만족도·별점은 손대지 않음,
     사진 촬영 시각 3열만 읽지도 덮어쓰지도 않음 — 공유 캡처 입력에 쓸 사진 현재값은
     쓰기 잠금 안에서 읽음, 사용자 override는 화면용 표에 그대로 다시 얹음, effective 갱신).
     기존 저장 경로의 공유 캡처를 그대로 타서
     공유 payload가 바뀐 신고만 새 `completed_observation`이 나간다. 바뀌지 않으면 이벤트 없음.
     사용자가 지운 공유 관측(`deleted_by_user`)은 되살리지 않는다(보충 경로와 같은 검사).
     `resend_fine_estimate_updates`는 그대로 둔다.
  4. 원본 파일이 없으면 만든다.
  5. 진행 로그(시각+문구, 최근 50줄)와 done/total/stage를 갱신한다. 끝나면 `done=true`, 중복군 재생성(바뀐 게 있을 때만),
     대기열 뒤에 붙은 크롤을 자동 시작한다. 작업 예외(`error`)면 크롤 보류를 풀어 대기 중인
     크롤(번호·전체)을 정상 실행하고, 최적화는 크롤이 끝난 뒤·다음 기동에 다시 시도한다.
- 크롤과의 순서: 작업이 도는 동안 수동 크롤(웹 `/crawl` 시작, `/api/v1/crawl/start`)과 알림·단건 크롤은 바로
  시작하지 않고 대기열 뒤에 붙인다. 번호 지정 요청은 `crawl_manager` 대기열에, 번호 없는 전체 크롤은
  메타 키 `detail_raw_optimize_deferred_full`에 하나로 합쳐 미뤄 둔다. 요청마다 크롤 창 로그(`current_crawl.log`)에
  "DB 최적화 중입니다. 최적화가 끝난 뒤 크롤링을 시작합니다." 한 줄을 남기고, 작업이 끝나면
  `request_pending_launch`·미뤄 둔 전체 크롤로 이어서 시작한다. 미뤄 둔 전체 크롤은 크롤 시작이
  성공한 뒤에만 DB 메타에서 지운다. 지우기 실패는 진단만 남긴다 — 드물게 보류가 남아 다음에
  한 번 더 시작될 수 있다(요청 유실보다 낫다). 시작 실패·서버 종료면 보류는 그대로 남아
  다음 훅·기동에서 시작한다. 크롤이 돌고 있으면 지우지 않고 다음에 다시 미룬다.
  최적화가 이미 done인데 서버가 끝났어도 재시작 때 기동 경로에서 꺼내 시작한다.
  모든 크롤 종료 경로(`crawl_manager.run_after_crawl`)에서도 보류 전체 크롤이 남아 있으면
  기존 pending/retry 장치로 시작한다(최적화 진행 중이면 계속 보류).
  복원 완료 지점(`core/storage/exchange.py::restore` 성공·실패 공통 종료 경로)에서는
  새 DB 메타로 `db_optimize.maybe_start`를 다시 호출해 다시 판단한다.
  수동 시작 API는 최적화 중일 때만 대기열 등록으로
  바뀌고 응답은 필드 추가만(`queued_reason: "db_optimize"`). 크롤 run 상태·진행/중지 버튼에는 넣지 않는다.
- 표시: `maintenance_service.status()` 작업 `db_optimize`(`label` "DB 최적화 중입니다…", done/total, stage,
  log 최근 50줄, `error` 때는 활성 작업과 구분해 '오류'와 로그 — '작업 완료'로 보이지 않음).
  스키마 업그레이드 단계(백업·표 생성·버전)도 같은 로그에 남겨 기동 뒤 보이게 한다
  (`database.migrate_schema` → `db_optimize.record_migration_event`, 실패해도 변환을 막지 않음).
  웹 하단 표시줄(`base.html #srJobBar`, `shell-polling.js`)에 라벨·진행률을 보이고, "단계 로그" 버튼
  (`aria-expanded`)으로 단계 로그를 펼친다. `/api/v1/maintenance/status`도 같은 추가 필드.

## 실행 접수·실패·초기화 작업의 수명

- 일반/선택/초기화 크롤은 완료 watcher를 먼저 준비하고 프로세스를 인계한다. 실행 실패도 watcher에 None을 전달해 대기를 풀며, 알림 실패가 이미 실행한 프로세스를 실패 접수로 바꾸지 않는다. 로그용 Popen 부모 fd는 닫는다.
- 프로세스 종료부터 완료 마커·로그 회전·커뮤니티 후처리까지 예약을 유지한다. 관리 실행은 부모가 return code와 자식의 실행 기록으로 succeeded/failed/cancelled/unknown을 기록하며 완료 처리는 한 번만 인계한다. 목록/상세의 수집 실패를 정상 동기화 성공으로 저장하지 않는다.
- 봇의 개인 자료 명령은 설정 chat와 허용 사용자 검사를 거친다. 그룹은 `TELEGRAM.allowed_user_ids`에 명시한 사용자만 허용한다. 서버 lifespan의 managed bot만 공통 coordinator로 크롤 쓰기 작업을 접수한다.
- 별점은 크롤/복원과 같은 coordinator 예약을 사용한다. 요청을 보내기 전에 submitting을 영속 기록하고, 응답 유실·불명확한 오류는 unknown으로 두어 다음 실행에서 GET 확인만 한다. 작업자 stop은 레코드 사이/재시도 대기에서 협력 취소하며 진행 중 네트워크 요청이 즉시 취소됐다고 하지 않는다.
- 초기화 HTTP start/resume는 영속 job 생성 후 관리 작업자에게 백업/manifest/실행을 맡긴다. supervisor는 자기 작업자/자식의 lease를 갱신하고 만료된 job을 다시 점검한다. 목록 등록·항목 저장·완료/오류 콜백은 dataset/account/run attempt와 running 상태를 확인한다. 시도 ID는 서버 로컬 community meta이며 교환 DB schema 변경이 아니다.
- pause는 먼저 상태를 fenced하고 자기 child를 정지시킨다. child가 계속 살아 있으면 running/pause_stop_pending을 반환한다. 진행 중 읽기 전용 백업은 checkpoint에서 멈추므로 paused 응답이 백업 스레드의 즉시 종료를 뜻하지 않는다. 소유 lease는 작업자 정리 뒤 해제한다.
- 새 작업의 게이트는 refresh 이후 최근 중앙 검증을 요구한다. 탐색 화면의 기존 캐시 허용과 구분한다. preflush는 업로더와 같은 project/contributor/connection/grant/control scope의 sendable count를 쓴다.
- 로그인은 false/exception 모두 유한 횟수·총 deadline 안에 재시도한다. 목록은 첫 페이지를 재사용하고 전체 수·페이지별 수·ID 유일성을 검사하며 total=0인 정상 빈 결과를 허용한다. manifest는 cursor 순환·페이지 수·deadline·lease/account scope를 확인한 뒤 snapshot을 교체한다.

2026-09-24 에 기존 루트 `CLAUDE.md`(725줄)에서 옮겼다. 아래 '이관 원문' 은 문구를 바꾸지 않고 옮긴 것이며, 원본 전체는 [legacy-claude-reference.md](legacy-claude-reference.md)에 있다.

## 코드 대조 정정 (기준 17df6cb)

| 원문 절 | 현재 코드 | 조치 |
|---|---|---|
| 전체 | 이번 이관에서는 크롤러 경로를 실행하지 않았다(실계정·외부 요청 금지). 코드 대조는 문서 절과 심볼 존재 수준만 확인했다. | 미검증 표시 |
| 중복 원천 읽기 실패 | `duplicate_group_service`는 세 merge 표·entry_value·raw_content의 필수 조회 실패를 `DuplicateInventoryError`로 전달한다. 신고가 0건이어도 모든 원천의 조회 성공을 확인한 후에만 그룹/멤버를 재작성한다. 실패 시 transaction rollback으로 기존 판단·수동 대표·생성 시각을 보존한다. | 정상 빈 원천과 읽기 오류를 구분; 판단 표는 정상 재생성에서도 유지 |
| 위반법규 파싱 (2026-09-27) | `services/parser.py`는 최신 처리내용에서 `도로교통법 제N조`와 `「자동차관리법」제29조`처럼 꺾쇠 안에 법 이름이 있는 조문을 읽는다. 법 이름·조·항 사이 공백을 허용하고 저장값은 공백을 정리한다. 모바일 `standalone_parser.dart`와 합성 입력 계약을 공유한다. | `contracts/parser-vectors.json` |
| 커뮤니티 자동 업로드 (2026-09-27) | 상세 1건의 개인 DB 저장 완료 뒤 `community_uploader.wake()`를 호출한다. 모든 크롤링의 부모 프로세스 완료 훅(`CrawlManager.run_after_crawl`)에서도 깨워 자식 프로세스에서 만들어진 대기 전송을 처리한다. | `services/community_capture.py`, `services/crawl_manager.py` |
| 캡처·재전송·사진 보충의 동시 저장 | 네트워크 prefetch 뒤 `BEGIN IMMEDIATE`로 실시간 캡처부터 개인 저장 커밋까지 직렬화한다. 재전송은 같은 잠금 안에서 추정 입력을 재조회한다. 사진 보충은 UPDATE 잠금 안에서 재캡처하며, 크롤링 prefetch 중 채워진 사진값도 보존한다. 두 보충 경로는 journal 최신 revision payload를 기준으로 `fine_estimate`만 바꾸고 금액 등 나머지 원문을 보존한다. 개인 저장 상태로 차단하지 않는다. | `reports_repo._save_one`, `community_capture.resend_fine_estimate_updates`, `photo_capture_time.fill_one`; 복원 장벽은 캡처 연결이 반환될 때까지 대기 |

## 이관 원문

<!-- legacy CLAUDE.md 372-412 -->
## 크롤링 파이프라인 (start.py)

```
main()
  → 로그인 (2026-09-25부터 API 방식·회원 로그인만 — 레거시 Selenium HTML 크롤링·비회원 수동 로그인·최소 크롤링 제거)
      → direct_login 시도
          → 성공       → driver 없이 API 호출
          → 실패       → Selenium 로그인 후 브라우저 API fallback(같은 API 를 브라우저 세션으로 부르는 비상 경로, 유지)
  → _run_crawling_process(driver, engine, args, api_browser_fallback)
      → crawltitle_api.crawl_titles(browser_fallback 여부 반영)
      → crawldetail_api.crawl_details(browser_fallback 여부 반영)
      → database.title_to_sql()
      → (큐 모드) extract_ids_from_queue()
          → missing_rnums 있으면 최대 100페이지 단건 크롤링으로 탐색
          → 발견 즉시 detaillist 추가, 모두 찾으면 조기 종료
      → database.detail_to_sql()   # 기존 deatil_to_sql alias 유지
  → _process_and_save_results()
      → database.rebuild_effective()  (스키마 6: 화면용 표 mysafety_report_effective 재생성 + 중복군)
      → crawl_state_store.save_crawl_done()    # crawl_done.json 저장 → 모바일 폴링용
      → crawl_state_store.save_crawl_changes() # 변경 목록 저장
      → export_service.export_results()        # auto_export_* 설정 반영
```

- `start.py`는 FastAPI와 **별도 서브프로세스**로 실행 → ws_manager 싱글톤에 직접 접근 불가
- 크롤링 완료 후 로그 회전/완료 마커 기록/WS 브로드캐스트는 `crawl_manager.run_after_crawl()` 경로로 수렴
- `login.py`는 Selenium 회원 로그인 담당, `direct_login.py`는 API용 직접 로그인 담당으로 역할을 분리한다.
- 최근 3일 답변 목록은 `답변일` 필터 후 `synced_at DESC`, 동순위 `신고번호 DESC` 로 정렬한다.
  `synced_at` 가 없는 과거 데이터는 `답변일 DESC`, `신고번호 DESC` fallback 이 필요하다.
- 모바일 Client용 `crawl_changes` 일반 신고 payload도 `synced_at` 을 포함해야 한다.
  서버가 내부적으로 category별 merge 테이블에서 결과를 모으므로, 저장 전 한 번 더
  `synced_at DESC` / `답변일 DESC` / `신고번호 DESC` 로 재정렬해 줘야 모바일 알림 히스토리와
  최근 답변 재계산이 서버 대시보드와 같은 기준을 쓴다.

---


<!-- legacy CLAUDE.md 502-515 -->
## 파싱 규칙 (services/parser.py)

### 과태료 자동 파싱
- 신고 유형이 "버스전용차로 위반", "쓰레기, 폐기물", "불법주정차신고" 이고 `처리상태 == "수용"` → `범칙금_과태료 = "과태료"`
- **예외**: `process_status == "취하"` 이면 과태료 설정 안 함. 취하 확정 시 `penalty_amount`, `penalty_points` 초기화.

### 불수용 키워드 강제 교정
`['부득이하게', '종결합니다', '처벌이 어려운 점', '처분이 불가']` 포함 시 → 불수용 + 범칙금 초기화

### 경고 키워드
`['교통질서 안내장', '훈방권', '12대 중과실', ...]` — 범칙금 없을 때 '경고' 설정

---


<!-- legacy CLAUDE.md 584-594 -->
### API 기반 크롤러 (crawltitle_api)
`page_size=200` 으로 수천 건을 수 초 내 스캔. Selenium DOM 파싱 대비 ~10배 속도 향상.

### 별점 2단계 API
1. GET으로 이미 참여 완료 여부 확인 → 완료건 스킵
2. POST로 별점 제출
API 차단 대비 Selenium 백업 코드 주석 보존.

### Watchlist 독립 테이블
메인 테이블 스키마 변경 없이 감시 기능 분리. Join으로 효율적 추적.


<!-- legacy CLAUDE.md 616-637 -->
### 주정차위반 카테고리 (crawldetail_api.py)
크롤링 시 `entry_value` 기준으로 3분류:
- `"자동차·교통위반"` in entry_value → `traffic`
- `"불법주정차신고"` in entry_value → `parking`
- 그 외 → `other`

entry_value는 "본 신고는 안전신문고 앱의 **(entry_value)** 메뉴로 접수된 신고입니다" 패턴에서 추출.
주정차위반 예시: `불법주정차신고-기타 불법주정차`

주정차위반 파싱 규칙:
- 수용 시 과태료: parser.py의 `"불법주정차신고"` in entry_value 조건 그대로 적용
- 취하 시: penalty 초기화 (기타위반과 동일)
- `"미확인"` penalty 로직은 `"자동차·교통위반"` 조건에만 해당 → 주정차에 미적용 (정상)

### 파서 첨부파일 URL (services/parser.py)
`FILE_URL`이 상대경로(`/fileDown/singo/...`)로 오는 경우 `https://www.safetyreport.go.kr` 프리픽스 자동 추가.
`STTEMNT_IMAGE_URL`도 동일하게 처리. (텔레그램 첨부 URL 깨짐 버그 수정)

### 첨부사진/파일 URL 구분자
DB에는 `\n` 으로 구분 저장됨. 웹 `data_table.html`의 `renderAttach()`는 `d.split('\n')`으로 파싱.
모바일 측 파싱은 `safetyreport-mobile` 레포 참조.


<!-- legacy CLAUDE.md 683-709 -->
## 디버그 extractor (`scripts/debug/extractor.py`)

### 사용법
```bash
python scripts/debug/extractor.py SPP-2604-1234567   # 신고번호
python scripts/debug/extractor.py 59216726 40871819  # 내부 ID 다중
```

### 기능 (2026-09-25 API 전용으로 축소)
- 신고번호(SPP-xxx) → DB 조회로 내부 ID 자동 변환, 다중 ID 순차 처리
- direct_login 세션(브라우저 없음)으로 상세 API 를 불러 저장:
  - `{id}_api_raw.json` — API 원시 응답
  - `{id}_api_parsed.txt` — `parse_json_details` 결과
- **DB 갱신 없음** — 순수 테스터
- 예전의 API vs Selenium HTML 비교(`_legacy_raw.html`, `_diff.txt`)는 레거시 크롤러 제거와 함께 없앴다.

---

## 요청 예산과 종료 소유권

Sunwi adapter retry는 0이다. 지역별 6회/90초 `RequestBudget`을 최초 수집과 실패 지역 재수집이 공유하고, 한 수집의 전역 기본 예산은 1800초다. connect/read timeout은 남은 시간을 나누어 제한한다. 취소 가능한 backoff와 Session finally close를 사용하며, 성공 지역의 전체 분류·건수와 실패 지역 목록을 유지한다. Requests의 DNS 및 slow-drip 응답은 socket timeout만으로 엄격한 wall deadline을 보장하지 못한다. stop은 5초 join 후 실제 생존을 반환하고 살아 있는 worker reference를 유지하여 중복 시작을 막는다.

관리 crawl은 shutdown 접수 fence를 먼저 닫고 자기 retry timer와 child만 정리한다. 준비 중인 작업이 뒤늦게 Popen을 실행하지 못하게 접수 경계를 다시 확인한다. 대기 큐는 보존한다. 다운로드 임시는 데이터 루트의 private process 디렉터리에 만들고, 다음 시작에서 죽은 PID의 정규 소유 파일만 회수한다. 살아 있는 프로세스·link/junction·다른 파일은 정리하지 않는다.

미디어의 URL lock은 대기자가 강한 참조를 보유하는 동안만 공유한다. prefetch는 32개로 제한하고 오류/완료 상태는 256개 및 활성 reader 보호 범위로 제한한다. 이전 generation과 열린 reader의 pin 계약은 유지한다.

Uploader와 direct-login keepalive의 stop은 실제 worker가 끝났는지 반환한다. 대기 예산 뒤에도 살아 있으면 참조를 유지하고 start가 새 worker로 교체하지 못하게 lock으로 보호한다. Community auth는 취소해 활성 목록에서 빠진 poll도 retiring 목록에서 추적하고, shutdown 때 활성/retiring 전체가 하나의 deadline을 공유한다. shutdown 후 새 poll 접수는 거절한다.

main lifespan은 WS/crawl 정리 단계의 실패가 나머지 worker 정리를 건너뛰지 않게 각각 처리한다. thread join에는 총 60초 공유 대기 예산을 배분한다(기본 worker5초, child15초, bot10초 상한). 이 값은 SQLite checkpoint·DNS·협력하지 않는 취소까지 포함한 프로세스 전체 hard wall이 아니다. bot은 updater/application/shutdown phase가 공유 deadline을 쓰며 한 phase가 실패해도 다른 cleanup을 시도한 뒤 실패를 알린다.

### 코드 대조 정정 — 종료와 임시 소유권

| 이전 설명 | 현행 코드 | 경계 |
|---|---|---|
| stop 요청 뒤 worker 참조를 비우면 종료 완료 | uploader/direct-login/Sunwi 실제 is_alive 검사·참조 유지, auth retiring 추적 | stop 반환 False는 아직 살아 있는 소유 작업이 있다는 뜻 |
| worker마다 같은 join 시간이면 총 종료도 그 시간 | auth 공유 deadline·main 전체 join 예산 | OS/transport가 협력하지 않는 작업의 강제 완료를 주장하지 않음 |
| 다운로드 finally가 process death까지 보장 | private PID artifact와 다음 startup 회수 | 정규 소유 파일·죽은 PID만 대상으로 제한 |

## 2026-10-04 기술일지 반영
- 직접 로그인 토큰(`data/auth_token.json`)에는 로그인한 아이디(`username`)를 함께 저장한다. 지금 설정 아이디와 다르거나 기록이 없는 토큰은 무효이고,
  설정에서 아이디·비밀번호를 바꾸면 토큰을 지운다. 로그인을 기다리는 동안 아이디가 바뀌면 저장하지 않는다(A2-01). 모바일 `StandaloneAuthService` 도
  `standaloneTokenUsername` 으로 같은 규칙을 쓰고, 백그라운드 재로그인은 저장 직전에 디스크의 아이디를 다시 확인한다.
- 만족도 점수 조회: `result` 키가 있고 값이 null/빈 객체일 때만 '미참여 확정'. 객체가 아니거나 `result` 가 없거나 `error` 가 있으면 확인 실패로 보고
  저장된 별점·사유를 지우지 않는다(`satisfaction_fetcher._classify_score_payload`, 모바일 `classifyScorePayload` — 같은 벡터 시험, A2-06).
- Sunwi 지역 통계: `result` 가 목록이 아니면 정상 0건이 아니라 실패로 재시도한 뒤 실패 지역으로 남긴다. 명시적인 빈 목록만 0건(A2-08, 모바일 같음).
- 사진 촬영 시각 백필은 읽을 때의 첨부 값과 저장 때 값이 같을 때만 쓴다(복원·재크롤링으로 첨부가 바뀌면 버림, A2-02).
- 대기 큐 자동 크롤은 수동 크롤과 같이 감시 스레드를 자식 프로세스보다 먼저 준비한다(A2-04).
- Selenium 세션을 만든 뒤 창 최대화·UA 확인에서 예외가 나면 그 자리에서 `quit()` 한다. UA 확인 실패는 로그인 진행을 막지 않는다(A2-09).
- 커뮤니티 업로드 실행 중 연결·동의 문맥이 바뀌면 남은 배치를 되돌리고 실행을 끝낸다(`context_changed`, A2-03).
- 구글 시트 내보내기는 0건 분류도 머리글만 보내 이전 행을 비운다(A2-07).
