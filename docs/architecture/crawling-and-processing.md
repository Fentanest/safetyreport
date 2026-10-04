# 크롤링·파싱·처리 규칙

다루는 것: start.py 파이프라인, 파서 규칙, 카테고리 분류, 첨부 URL, 별점 API, 감시목록, 디버그 extractor.

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
      → database.merge_final()
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
