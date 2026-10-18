# 데이터·외부 계약

다루는 것: 설정 키, DB 테이블/컬럼, 서비스 반환 키, category 전파, 모바일 API, WebSocket, 완료 마커 파일, 크롬 확장 API.

## 리팩터링의 입력·읽기·작업 기록 경계

- 필수 merge/entry/raw 읽기 오류는 정상 빈 자료와 다르다. 중복 재생성은 오류를 전파하고 기존 그룹·멤버·사용자 판단을 보존한다. 목록·통계 원천 오류도 빈 성공 응답으로 바꾸지 않는다.
- 목록의 여러 카테고리, 통계·지도 원천과 중복 projection은 같은 연결의 명시 read transaction에서 읽는다. 외부 WAL commit 뒤에도 해당 응답은 하나의 snapshot을 유지한다.
- 취하 제외 조건은 NULL 처리상태를 포함한다. 날짜 종료값은 그 날짜 전체, 분 단위 시각 종료값은 해당 분 전체를 포함한다. 저장한 원문 날짜/NULL/빈 문자열을 변환하지 않는다.
- JSON 객체 mutation은 최대 1MiB를 읽고 malformed/non-object/잘못된 필드 타입은 400, 초과 크기는 413이다. API 키·protocol·게이트 확인이 앞선다. session 관리자 mutation에는 CSRF 토큰 및 Origin/Fetch-Site 검증을 적용한다. URL-encoded token fallback은 최대 64KiB이며 읽은 body를 후속 form parser에 재생한다.
- 파일 API는 logs/results의 명시 root만 허용한다. ZIP은 완성한 임시 파일로 제공하며 entry 이름은 `logs/상대경로`, `results/상대경로`다. 선택 파일을 읽지 못하면 일부 파일만 들어간 성공 ZIP을 만들지 않는다. POSIX에서는 검증한 fd와 O_NOFOLLOW/dirfd로 경로 교체를 방어한다. Windows는 `windows_file_handle`에서 CreateFileW로 연 같은 핸들의 final path·reparse·hardlink를 검사하고 그 핸들로 읽거나 삭제한다. 실제 Win32 ABI/filesystem 동등성은 Windows runner 결과가 필요하다.
- `crawl_runs/*.json`(실행·return code), `rating_operations/*.json`(제출/확인 결과), `export_runs/*.json`, `notification_runs/*.json`은 서버 로컬 작업 기록이다. 교환 DB의 테이블·컬럼·schema version에 추가하지 않는다. 별점 unknown/submitting 기록은 다음 실행에서 조회로 확인하며 자동 재POST하지 않는다.
- `/api/v1/crawl/status`의 `last_attempt`와 `crawl_finished.data`의 `outcome`/`run_id`는 추가형 정보다. 기존 필드·완료 마커 소비 경로는 유지한다. Android `CrawlOutcome`은 failed/cancelled/unknown/partial을 구분하며 outcome 없는 구버전의 기본 의미를 유지한다. terminal replay와 native cursor의 상세는 아래 WS 절을 따른다.

### 코드 대조 정정

| 설명 | 현행 코드 근거 | 적용 경계 |
|---|---|---|
| 원천 읽기 실패를 빈 목록으로 간주해도 됨 | duplicate/query/stats 필수 read에서 오류를 전파 | 정상 empty와 자료 손실을 유발하는 read failure를 분리 |
| inode signature/probe 교체만으로 pool도 새 DB를 읽음 | `report_cache.cached`가 engine별 inode를 관찰하고 idle pool dispose 후 계산 | checked-out read는 자신의 기존 snapshot을 완료; cache generation 재검증 별도 |
| 별점 timeout은 다음 실행에서 재제출 가능 | `rating_operation_state`와 `star_rating_service`의 조회 확인 경계 | 실제 원격 결과가 미확정이면 자동 재제출하지 않음 |
| WS 종료 알림은 현재 연결에만 전달되며 모든 client send를 함께 기다림 | `ws_event_store` terminal256·`ws_manager` 연결별 writer/queue/deadline·optional after replay | 교환 DB와 별도 private sidecar; native history/cursor 원자 저장. terminal 순서 lock의 지연 한계는 별도 |
| 파일 임시는 response finally만으로 강제 종료까지 정리됨 | `download_artifacts` private PID 디렉터리·startup dead-PID recovery | 살아 있는 PID·불명확한 소유 파일·link/junction을 정리하지 않음 |
| Android commit 실패면 cursor 메모리도 자동으로 되돌아감 | 모바일 `PrefsInbox.writeHistory` 실패 시 이번 history/trim/cursor만 rollback한 뒤 WsService1013 | 정상 history+cursor 단일 commit; 별도 Flutter 읽기와 OS 알림 표시까지 exactly-once로 보장하지 않음 |

## 현행 self-host 통신 계약

정본은 [contracts/selfhost-compat](../../contracts/selfhost-compat/README.md)와 테스트 벡터다.
서버 제품 major ≥3, 클라이언트 protocol=3을 각각 검사한다. 모바일 실제 제품 버전 2.0.0+31도 protocol 3이면 허용한다.
HTTP의 Client/Version/Protocol 헤더, WS의 client_type/client_version/client_protocol 쿼리가 필수다.
유효 API 키라도 protocol 없는 요청은 데이터·다운로드·완료 마커·알림을 받지 못한다(409/4406).
인증된 `/api/v1/server/version`만 최소 호환성 확인을 허용한다. 관리자 세션은 `/api/v1` 예외가 아니다.
과거 "구앱 호환" 설명은 응답 필드 보존을 뜻하며 구형 클라이언트 접근 허용을 뜻하지 않는다.
추가형 페이지 조회·범위별 지도 조회와 통계 추가 필드의 실제 JSON도 위 정본을 따른다.

### 복원 거절 오류

업로드 원본을 읽기 전용으로 검증하고 형식·무결성·스키마·주인을 확인한 후에만 복원을 시작한다.
`DB_FILE_INVALID`(SQLite 아님), `DB_CORRUPT`, `DB_KIND_UNSUPPORTED`, `DB_LEGACY_UNSUPPORTED`,
`DB_SCHEMA_UNSUPPORTED`(미래 스키마), `DB_ACCOUNT_MISMATCH`를 구분한다.
복원 거절은 HTTP 409 `{status:"error",code,detail,message}`이며 detail/message를 화면에 계속 표시한다.
지원 범위(서버 6~7, 모바일 앱 17~18)의 예전 DB 는 같은 업그레이드 로직으로 올려서 받는다(원본 표는 비어 있음).
더 새 버전은 `DB_SCHEMA_UNSUPPORTED`, 더 오래된 버전은 `DB_LEGACY_UNSUPPORTED` 로 거절한다.
거절 전에 현재 DB 교체·dataset 회전·소유자·초기화 상태 변경은 없다. 임시 업로드만 정리한다.

## 공식 신고 위치 (현재 dev)

- 안전신문고 상세의 `C_A_W`가 위도, `C_A_E`가 경도다. 완료된 보완의 `SPLMNT_C_A_W/E`가 유효하면 보완 좌표를 쓴다. 보완 주소가 바뀌고 좌표 쌍이 유효하지 않으면 기존 위치를 새 주소에 붙이지 않는다.
- 위도 32~39.5, 경도 124~132의 유한한 쌍만 기존 상세·병합 표의 `위도`·`경도`에 저장한다. 반올림·소수점 절사를 하지 않는다. 값이 없으면 두 열 모두 NULL이다.
- `주소정규화`는 공식 주소의 공백 정리값, `행정구역`은 빈 문자열, `지오코딩상태`는 좌표가 있으면 `ok`, 주소만 있으면 `not_found`다. 이 열 이름은 기존 DB 교환 계약을 유지하기 위해 남았다.
- 재크롤링은 같은 주소여도 좌표를 덮어쓴다. 개인 주소 수정값은 화면의 주소만 바꾸며 공식 좌표는 그대로 둔다. 서버·모바일 DB 교환은 이 기존 열을 양방향 모두 보존한다.
- 커뮤니티 지도에는 해당 좌표를 보내고, wire `location.source="geocode"`는 기존 입력 계약의 좌표 있음 표시값으로 유지한다. 보완으로 바뀐 좌표는 재수집 시 새 `completed_observation`으로 전달된다.

2026-09-24 에 기존 루트 `CLAUDE.md`(725줄)에서 옮겼다. 아래 '이관 원문' 은 문구를 바꾸지 않고 옮긴 것이며, 원본 전체는 [legacy-claude-reference.md](legacy-claude-reference.md)에 있다.

## 2026-10-17 상세 원본 저장 — 스키마 7 / 모바일 18 (현재 기준)

이 절이 아래 모든 표 설명보다 우선한다. 명세: `docs/plans/2026-10-17-raw-detail-reparse.md` 1·2절(3절 최적화·4절 원본 보기는 별도 절, 5절 검색 필터는 아직 하지 않음).

- **정규 직렬화**(서버 `core/utils/raw_detail.py`, 모바일 대응): 응답 envelope 전체에서 `result` 객체의 신고자 키 5개
  (`C_NAME` `C_EMAIL` `C_PHONE` `C_PHONE2` `C_ID`)만 지운다. 다른 위치의 같은 이름은 건드리지 않고, 키 순서를 유지한다.
  공백 없는 JSON(UTF-8, 비ASCII 그대로)의 sha256(`raw_sha256`)을 구하고, 그 바이트를 zlib(레벨 6) 압축한 BLOB 을 저장한다.
  읽을 때는 해제 뒤 sha 가 맞는지 확인한다. 안 맞으면 그 행은 "손상"으로 보고 원본 보기·재파싱에서 쓰지 않는다(로그 남김).
  정본: `contracts/raw-detail-vectors.json`(서버·모바일 바이트 동일).
- **새 표**: 서버 `mysafety_report_detail_raw`, 모바일 `report_detail_raw`. 열은 같다:
  `ID TEXT PRIMARY KEY`, `raw_zlib BLOB NOT NULL`, `raw_sha256 TEXT NOT NULL`, `raw_size INTEGER NOT NULL`(압축 전 바이트 수),
  `fetched_at TEXT NOT NULL`(UTC ISO8601, 밀리초, `Z`), `source TEXT NOT NULL`(받은 경로).
  신고 행과 1:1, 같은 ID 로 다시 받으면 덮어쓴다(upsert). 신고 행을 지우는 경로(reset·로그아웃 비우기·모바일 복원)는 원본 행도 같이 지운다.
- **저장 시점**: `crawldetail_api.crawl_details` 가 `data` 를 받은 직후 정규화해 결과 튜플(`CrawledDetail` 10번째 `raw_info`)에 실고,
  `reports_repo._save_one` 의 같은 트랜잭션에서 원본 표에 upsert 한다(초기화·일반·단건 모두). 원본 표 `source` 열은 받은 경로를
  그대로 기록한다: 초기화 크롤(`start.py --rebuild`)은 `rebuild`, 큐 지정 크롤(알림·단건·웹 선택)은 `notification`, 일반 크롤은 `crawl`.
  파서 입력은 지금처럼 `result` 이다
  (파서는 신고자 키 5개를 쓰지 않음을 시험으로 고정). 원본 파일 `<datapath>/raw_details/<ID>.json`(들여쓰기 2칸, 임시 파일→rename)은
  DB 저장과 별도로 쓰고, 실패하면 DB 저장을 막지 않고 대체 진단 기록만 남긴다. 파일 이름에 쓰는 ID 는 영숫자·`_`·`-`(64자 이내)만
  허용하고, 정규화한 최종 경로가 `raw_details` 를 벗어나면 쓰지 않고 진단만 기록한다(`core/utils/raw_detail.py`).
- **업그레이드**: 6 → 7 은 추가만 하는 제자리 변환(업그레이드 전 백업 `before_schema_v7_*.db` → 새 표 생성 → 버전 기록, 실패하면 DB 무변경).
  체인: 5 → 6 → 7. 5 미만 규칙(백업 뒤 비우기) 유지. 각 단계는 원자적이다: 5→6 커밋 뒤 6→7 이 실패하면 DB 는 버전 6 에 머물고
  다음 기동 때 6→7 을 다시 시도하며, 실패는 오류 로그에 명확히 남긴다. 크롤러 단독 실행은 서버가 먼저 올리기 전에는 멈춘다.
- **교환·복원**: 지원 범위(서버 6~7, 모바일 앱 17~18)면 올려서 받고, 더 새 버전·더 오래된 버전은 기존처럼 거절한다.
  서버 6·모바일 17 DB 를 올려 받은 뒤 원본 표는 비어 있다(최적화 작업이 채움). 원본 표는 BLOB 바이트 그대로 양방향 교환한다(재압축 금지).
  왕복 시험에서 원본 표의 모든 열 값이 바이트까지 같아야 한다. 계약 열 목록은 이 문서 한 곳.
- **스키마 버전**: 서버 `PRAGMA user_version` = `core/database/database.py SCHEMA_VERSION`(현재 7),
  모바일 `LocalDbService.dbVersion`(현재 18). 계약 `contracts/storage-contract.json`(현재 `contract_version` 4).

## 2026-10-17 DB 최적화 — 메타·API 추가 필드 (명세 3절)

- **메타 키** `mysafety_sync_meta.detail_raw_optimize`(서버·모바일 같은 키·같은 의미, Opus P1) =
  `{"target": "pc-parser-13", "cursor": "<마지막 처리 ID>", "done": bool, "total": int,
    "stats": {"reused", "refetched", "changed", "events", "forwarded", "skipped": {사유: 건수}},
    "attempts": {"<ID>": 이번 회차 시도횟수}, "holds": ["<ID>", ...], "round": 회차(0에서 시작, 한 바퀴 끝날 때마다 +1),
    "next_retry_at": "다음 재시도 UTC ISO8601(없으면 생략)"}`.
  구 메타(`next_retry_at` epoch 숫자·모바일 구 `pending` 키)도 읽는다(하위호환).
  `target`이 서버·모바일 현행 버전과 다르면 커서·보류·통계·시도횟수를 버리고 처음부터 다시 훑는다.
  같은 세대 target의 partial 메타를 가져오면 `holds`만 이어서 처리한다.
  일시 실패(네트워크·5xx·타임아웃·저장 실패)는 `attempts`에 신고별 횟수를 남기고 커서는 넘기되
  같은 회차 끝·다음 기동에 다시 시도한다. 3번째 실패에서 보류 목록(`holds`)에 남기고 이번 회차는 건너뛴다(영구 건너뜀이 아님),
  `done=true`는 보류 목록이 비었을 때만 기록한다.
  보류만 남은 채 한 바퀴를 끝내면 `done` 없이 `state='partial'`(`paused` 아님, 실행 중 아님)로 끝나고,
  다음 기동이나 크롤 성공 뒤 보류 목록만 다시 시도한다(회차마다 시도 횟수 초기화,
  회차 사이 간격은 `next_retry_at` 지수 백오프·최대 1시간, 크롤 성공 직후 재시는 간격 무시).
  영구 실패는 404·410만, 403·401·로그인 화면은 로그인 문제(`auth`)로 본다.
  연속 5건 일시 실패·로그인 문제는 신고별 횟수 없이 작업 전체를 쉰다(`outage`/`login_required`,
  모두 밑값 60초→2배→최대 1시간 지수 백오프, 크롤 성공 직후 깨움). 실패해도 요청 간격은 지킨다.
  저장 직전 답변일·처리내용·상태가 바뀌면 처리 완료로 세지 않고 시도 횟수 증가 없이 `holds`로 넘긴다(Opus P6).
  신고번호 없는 행의 새 활동은 저장(원본·파서 열·파일)·대기열 없이
  로그 '새 답변 — 다음 일반 크롤에서 처리(신고번호 없음)' 뒤 처리 완료로 센다(Opus P5).
  최적화가 어떤 경로로 끝나거나 쉬든(완료·partial·outage·login_required·오류) 보류 크롤을 배출한다(Opus P4).
  직접 로그인 실패 때 토큰 파일 세션 재사용은 파일 계정(`username`)이 현재 설정 계정과 같을 때만 한다(Opus P7).
  쉬는 동안 이 크롤 게이트(`db_optimize.is_running()`)는 False — 실제로 신고를 처리 중일 때만 True다.
  번호 없는 수동 크롤 미루기는 `detail_raw_optimize_deferred_full`(합쳐진 단일 전체 크롤 요청 1건)에 둔다.
  보류 저장 실패는 예외를 올려 요청자(웹·`/api/v1/crawl/start`)가 기존 오류 형태로 받는다(하위호환).
  등록 직후 최적화가 이미 끝났으면 바로 배출한다(등록·완료 경합).
  시작 직전에 `detail_raw_optimize_deferred_full_started`(시각·run 식별)를 먼저 기록하고 시작한다.
  시작 성공 뒤에만 지우고, 삭제 실패로 남아도 다음 기동·크롤 종료 훅에서 started가 있으면 다시 시작하지 않고
  정리만 한다. 시작 실패 시 started를 되돌린다. 실행 중이면 지우지 않고 다음 기동·다음 완료 때 시작한다(유실 금지).
  모든 크롤 종료 경로(`run_after_crawl`)에서도 보류가 남아 있으면 pending/retry 장치로 시작한다(최적화 처리 중이면 보류 유지).
  크롤 성공 종료 뒤에는 쉬는·멈춘 최적화를 다시 판단한다(`on_crawl_success`, 보류만 재시도·백오프 무시).
- **정비 상태**(`services/maintenance_service.status()` → `/maintenance/status`,
  `/api/v1/maintenance/status`): 작업 `db_optimize` 추가(기존 필드·의미 불변, 추가만).
  `{"key": "db_optimize", "label": "DB 최적화 중입니다…"(부분 완료·쉼 상태에서는
    "DB 최적화 부분 완료 — 보류가 있습니다"/"DB 최적화 쉬는 중입니다…"와 보류 건수·재시도 안내),
    "state": idle|running|paused|partial|completed|error,
    "total", "done", "stage": running|waiting_crawl|waiting_rebuild|login_required|outage|partial|done|error,
    "message", "log": ["시각 문구" 최대 50줄], "finished_at"}`.
  `partial`은 실행 중이 아니다: 정비 상태 `active=false`, 표시줄은 '보류 N건, 다음 재시도 시각'만 조용히 보인다(Opus P3).
  새 활동 판정 계약 `contracts/reparse-new-activity-vectors.json`(읽기 전용, 수정 금지):
  상세 `C_NOW`→진행 단계 표(`services/parser.py` `_C_NOW_STATUS` 그대로)와 저장된 목록 상태·답변일 비교 규칙.
- **크롤 시작 API 추가 필드**(기존 필드 유지, 하위호환): 최적화 때문에 대기열에 붙을 때
  `queued_reason: "db_optimize"`. 대상: `POST /api/v1/crawl/start`(실행 중이면 예외이던 경로가 최적화 중일 때만
  대기열 등록으로 바뀜), `POST /api/v1/crawl/enqueue`(단건), `POST /api/v1/crawl/notification`(알림),
  웹 `POST /crawl/start`·`POST /crawl/enqueue-selected`.

## 2026-10-17 원본 보기 — 웹·모바일 API·파일 탐색기 (명세 4절)

- **읽기 공용**(`services/raw_detail_service.py`, 웹·모바일이 같은 함수 사용):
  `read(engine, id)` → `{id, fetched_at, source, raw_sha256, json}`.
  `json` 은 신고자 키 5개가 이미 빠진 저장 원본 그대로(객체)다. 행 없음·ID 검증
  실패(`core/utils/raw_detail.is_safe_report_id` — 경로 이탈 시도를 구분해 알리지 않음)는
  `RawDetailMissing`, zlib 해제·sha256·JSON 복원 실패는 `RawDetailCorrupt`(대체 진단 기록 + 경고 로그,
  `services/db_optimize._stored_result` 와 같은 규칙). `rewrite_file(engine, id)` 은 DB 원본으로
  `<datapath>/raw_details/<ID>.json` 을 다시 쓴다(실패면 `RuntimeError`).
- **웹**(관리자 세션, 추가만): `GET /reports/{id}/raw-detail` → 위 객체 그대로.
  없음 404 `{error: "raw_detail_missing"}`, 손상 409 `{error: "raw_detail_corrupt"}`.
  인증은 전역 `auth_middleware`(비로그인 302/`session_expired` 401)가 맡는다.
  `POST /reports/{id}/raw-detail/rewrite` → `{status: "success", id, filename}`(없음 404·손상 409).
  JSON POST 라 `core/utils/csrf.py verify_json_post` 로 CSRF 를 확인한다(전역 버전·CSRF 미들웨어와 같은 방식).
- **모바일 API**(API 키 인증, 추가만, 기존 응답·경로 불변):
  `GET /api/v1/reports/{id}/raw-detail` → `{id, fetched_at, source, raw_sha256, json}`.
  없음 404 `{error: "raw_detail_missing"}`, 손상 409 `{error: "raw_detail_corrupt"}`.
  `/api/v1/app/config` 에 `capabilities` 항목 `raw_detail_v1` 과 `raw_detail_version: 1` 추가
  (이 신호가 없는 구서버에는 Client 가 원본 보기 동작을 보이지 않는다).
- **파일 탐색기**: `services/file_service.ALLOWED_BROWSER_DIRS` 에 `raw_details`
  (`settings.raw_details_dir`) 추가 — 보기·다운로드만 허용하고 삭제는 금지한다
  (개별·선택·API·일괄 모두 거부, 원본은 DB 에서 다시 만들 뿐 지우지 않는다).
  경로 이탈 금지는 기존 규칙(`_under_root`·O_NOFOLLOW·하드링크 거부) 그대로다.

## 2026-10-11 서버 스키마 6 / 모바일 17 — 신고 한 건 한 행, 5분류 (이전 기준, 스키마 7 설명이 우선)

이 절이 아래 모든 표 설명보다 우선한다. 계획·결정: `docs/plans/2026-10-11-report-taxonomy.md`.

| 표 | 내용 |
|---|---|
| `mysafety_report` | 신고 한 건 한 행: 목록 열(`상태` `신고번호` `신고명` `신고일` `만족도조사여부` `별점` `별점사유` `감시목록`) + 상세 열(처리상태 … 사진_촬영수) + `category` `entry_value` `ssnpc_cd` `ssnpc_type`. **`category` NULL = 목록에서만 본 신고(상세 미저장)** — 수집 대상 선정(`collection_policy`)이 쓴다. |
| `mysafety_report_effective` | 화면용 = category 가 있는 신고 + 수정값(`mysafety_report_override`) + 감시목록(계산) + 6개월 지난 첨부 가림. 바뀐 ID 만 갱신(`reports_repo.refresh_effective_rows`), 전체는 `database.rebuild_effective`. 통계·API·내보내기·검색은 이 표를 읽는다. |
| `mysafety_report_body` | 본문 원문(`raw_content` `raw_type` `saved_at`). 중복군 판정·차량번호 복구. |
| 그대로 | `mysafety_report_override` `mysafety_watchlist` `mysafety_duplicate_group/member/decision` `mysafety_change_log/cursor` `mysafety_geocode_cache` `mysafety_sync_meta` `admin_users` `api_keys` |
| 없어짐 | `mysafety` `mysafetydetail_{traffic,parking,other}` `mysafetymerge_{traffic,parking,other}` `mysafety_entry_value` `mysafety_raw_content` |

- **분류**: `services/report_category.resolve(ssnpc_cd, ssnpc_type, entry_value)` — 코드가 있으면 코드, 없으면 접수 메뉴. 정본 `contracts/report-categories.json`(두 레포 바이트 동일, 43개 코드). 값은 `traffic` `parking` `safety` `living` `other`.
- **업그레이드**: 버전 5 → 6 은 서버 시작의 `database.migrate_schema`(백업 `before_schema_v5_*.db` → 한 쓰기 트랜잭션에서 옮기기 → 행 수·값 해시 대조 → 커밋). 신고가 없는 옛 `mysafety_entry_value` 행(고아)은 `mysafety_sync_meta` 의 `legacy_orphan_entry_values`(JSON `[[ID, entry_value], …]`)에 보존한다. 5 미만은 기존 `reset_legacy_database`(백업 뒤 비우기). 크롤러 단독 실행은 서버가 먼저 올리기 전에는 멈춘다.
- **교환·복원**: 같은 스키마 버전끼리만(서버 6 ↔ 모바일 17). 모바일 `reports` 는 `mysafety_report` 와 같은 열(+ legacy `raw_content`), 모바일에는 category NULL(목록만 본 신고)을 둘 자리가 없어 그런 행이 있는 서버 DB 가져오기는 **거절**한다(크롤링이 끝난 뒤 다시). 모바일 DB 를 서버에 복원할 때 서버의 목록 전용 행은 그대로 둔다(`exchange.apply_mobile_snapshot`).
- **모바일 API 호환**: `X-SR-Taxonomy: 5`(또는 `?taxonomy=5`, WS 는 `/ws/events?taxonomy=5`)를 보내지 않는 앱에는 JSON 의 `category`·`previous_category` 의 safety·living 을 other 로 접는다(`core/utils/taxonomy_compat.py`). `/reports/other` 는 구앱에 safety+living+other. `/server/version` 의 `report_taxonomy: 5`.
- **상세 저장 튜플**: `(df, category, entry_value, progress_status, title_fields, raw_content, raw_type, ssnpc_cd, ssnpc_type)`.
- 아래 "DB 컬럼명"·"DB 테이블" 절의 `mysafety*` 다중 표 설명은 스키마 5 까지의 기록이다.

## 코드 대조 정정 (기준 17df6cb)

현재 dev 기준은 위 **2026-10-17 상세 원본 저장** 절이다. 이 문서의 2026-09-24 R1 절과 이관 원문의 자동 업그레이드 설명은 당시 기록이며 현재 값이 아니다. 현재 서버 스키마는 `core/database/database.py`의 `SCHEMA_VERSION=7`(2026-10-17, 위 절); 6 은 제자리 변환, 5 는 5 → 6 → 7 체인, 그 미만은 서버 시작 때 백업 후 초기화한다. 현재 모바일 스키마는 `contracts/storage-contract.json`의 `schema_version.mobile=18`이다. 제품 VERSION 및 self-host protocol 3과 별개다.

| 원문 절 | 현재 코드 | 조치 |
|---|---|---|
| 설정 표 crawl_type | 코드는 `[Crawler] crawl_type` 이 `api` 면 api, 그 외 값은 모두 `legacy` 로 읽는다(`settings/settings.py:68-69`). 표의 `api / web` 는 부정확. | 정정 |
| 설정 표 auto_export_sheet | 코드 기본값은 `False`(`settings/settings.py:66`). 표의 `True` 는 부정확. | 정정 |
| config.ini 섹션+키 | 과거 `[MAP] kakao_rest_api_key` 는 읽거나 저장하지 않는다. 기존 설정 파일의 값은 기능에 사용하지 않는다. | 공식 좌표로 전환 |
| get_dashboard_stats() / get_agency_stats() 키 | 현재 코드는 목록 외에 `withdrawGraphCount`, `dedupe_mode`(대시보드), `avg_days`, `total_fine_amount`, `unconfirmed(_pct)`, `avg_rating`, `rating_count`, `by_law`, `available_laws`, `has_empty_law`, `dedupe_mode`(기관 통계)를 반환한다(`services/report_stats_service.py:440-470, 603-705`). | 추가 — 기존 키는 유지 |
| 모바일 API 표 | `/summary 의 취하 필드 규칙` 목록이 표 중간에 끼어 뒤쪽 행(`/app/config` 이하)이 표로 렌더되지 않는다. 내용은 유효. | 형식만 문제 |
| 이전 API/WS 키만 전송 | 현재 외부 요청은 키 인증 후 protocol 3 필수 검사; 과거 키도 예외 없음 | selfhost-compat 정본 우선 |
| 이전 DB 거절 문구/오류 종류 | Legacy/Future/Corrupt/Account 거절 코드를 분리하고 detail/message를 유지 | 현행 복원 거절 절 우선 |

## 2026-09-24 통계 계약 갱신 (모바일 세션 `feature/stats-overview-api` 병합분)

이 절은 해당 브랜치가 옛 루트 `CLAUDE.md` 에 추가한 내용을 옮긴 것이다(원문 그대로). 위 "이관 원문"의 `get_agency_stats()` 키 목록보다 이 절이 최신이다.
결정 번호 S-xx 는 모바일 레포 `docs/design/statistics-spec.md` 의 결정표를 가리킨다.

**get_agency_stats() → traffic/parking/other 각각:**
```
by_agency / by_person / police_by_agency / police_by_person / other_by_agency / other_by_person
  └ agency  person  total  avg_days  total_fine_amount  fine_amount_unknown
    fines  fines_pct  warnings  warnings_pct  rejects  rejects_pct  unconfirmed  unconfirmed_pct
    in_progress  in_progress_pct  avg_rating  rating_count
by_law (법규별, 같은 필드 + law)
```
행 규칙(2026-09-24, 모바일 S-10 — `_build_stats_tables`, 모바일 `LocalDbService.buildStatsCategory` 와 같은 정의):
- 표에 넣을지는 처리상태가 아니라 값으로 정한다. 기관표 = 처리기관(trim)이 있는 신고, 담당자표 = 처리기관과 담당자가 모두 있는 신고(`''`·`미지정` 제외). 처리기관이 없으면 어느 표에도 넣지 않는다('알수없음' 행 없음).
- 배정된 처리중 신고도 들어간다. `in_progress` = 완료(수용·일부수용·불수용·기타·답변완료)도 취하도 아닌 상태, `unconfirmed` 에서 뺀다. 대시보드용 `_disposition_counts` 는 그대로다.
- `avg_days` 는 완료 신고만(이송 답변일이 붙은 처리중·취하 제외). `/stats/overview` 의 `avg_days` 도 같다.
- 표시용 반올림은 `_round_half_up`(x.x5 올림, 모바일 Dart `toStringAsFixed` 와 동일). Python `round()` 를 쓰지 않는다.

### 모바일 API 응답 필드 (/api/v1)
공통 래퍼: `status` `data` `count`

**crawl/done:** `done` `timestamp` `changed_count`
**crawl/config:** `crawl_type` `crawl_mode` `max_empty_pages`
**stats:** `traffic` / `parking` / `other` / `available_years` / `traffic_total_fine`
**stats/overview** (2026-09-24, 모바일 통계 요약용): `all` / `traffic` / `parking` / `other` 각각
`total completed accept partial reject supplement processing withdraw avg_days avg_days_count reversed_date_count undated_report_count monthly_reported[{month,count}] monthly_answered[{month,count}]`
+ `available_years` `year_basis`(='답변일') `exclude_withdraw` `dedupe_mode`.
`get_agency_stats` 와 같은 행(로딩·대표건·행 필터·취하 제외·법규)을 쓴다 — 공통 헬퍼 `_load_stats_frames` / `_apply_stats_row_filters` / `_apply_stats_law_filter`.
평균 처리일은 기관 평균을 합치지 않고 원자료에서 직접 계산(완료 상태 + 두 날짜 유효 + 차이 ≥ 0), 표본 수 함께 제공. 모바일 Standalone `LocalDbService.summarizeOverviewRows` 와 같은 정의 — 한쪽을 바꾸면 양쪽 테스트를 함께 고친다.

## 2026-09-28 통계 화면 개편 — 추가형 필드·경로 (기존 필드 삭제·의미 변경 없음)

- **stats/overview** 각 분류 요약에 추가(서버 `_summarize_overview_frame`, 모바일 `LocalDbService.summarizeOverviewRows`):
  - `monthly_answered_fine[{month,count}]`: 답변월 기준 과태료 건수(`범칙금_과태료` 에 '과태료'). `monthly_answered` 와 같은 답변일 기준 보조 계열.
  - `disposition{fines warnings rejects unconfirmed in_progress disposition_unknown no_penalty unclassified overlap}`: 기관 유무와 무관한 분류 전체의 처분 분류.
    기관표 행과 같은 규칙(`_stats_row_disposition_counts`). `overlap` = 과태료+경고/범칙금+불수용 − 셋 중 하나 이상인 신고 수(한 신고에 겹친 수). 일곱 항목 합 = `total + overlap`.
  - `fine_amount{confirmed_amount confirmed_count unknown_count estimated_amount estimated_count}`: 확정(원문 금액)·추정(법정 최저)을 따로. 합친 값은 없다(PROJECT_RULES §3-2).
  - `report_types[{name,count}]`: 신고명(trim) 건수, 건수 내림차순·이름 오름차순, 빈 신고명은 `name:''`. 전체 목록.
  - 구서버(필드 없음)는 모바일이 null 로 읽고 '미지원'으로 보인다.
- **stats** 행(기관·담당자·법규)에 `avg_days_count` 추가: 평균 처리기간 표본 수. 표 합계는 행 평균을 이 수로 가중한다(행 수·총 건수 가중이 아님).
- 같은 입력·기대값: `contracts/stats-overview-vectors.json`(두 레포 바이트 동일) — 서버 `tests/test_stats_overview_vectors.py`, 모바일 `test/services/stats_overview_vectors_test.dart`.
  실제 fixture DB 로 `scripts/dev/logic_parity_check.py` 48개 조합 차이 0(2026-09-28).
- 웹 전용(세션 인증): `GET /stats/map/points?category=&year=&<통계 조건>&targetAgency=&targetPerson=` → `get_report_map_stats` 와 같은 `{points, meta}`.
  `/stats/map`·`/stats/map/missing` 도 같은 통계 조건 이름(`law reportName location reportDate* occurDate* responseDate* occurTime* agency agencyExact excludePolice onlyPolice`)과
  `targetAgency`(registry 현행 표시명과 정확히 일치)·`targetPerson`(정확히 일치)을 선택적으로 받는다. 없으면 예전과 같은 전체 지도. `/api/v1/stats/map` 은 바꾸지 않았다.
  상세 표의 지도 링크는 `targetAgencyKey=<행 agency_key>`와 `completedOnly=true`도 전달한다. 키가 있으면 표시명보다 키가 우선하며,
  완료 상태(수용·일부수용·불수용·기타·답변완료)만 남긴다. 두 필드가 없는 요약 지도와 기존 이름 조회의 모집단은 바꾸지 않는다.
  지도 모집단은 통계와 같은 순서(SQL 조건 → 대표건 → 행 조건 → 취하 제외 → 법규)라 `meta.total_reports` 가 요약 `total` 과 같다(분류를 고른 경우. 테스트 `test_map_uses_same_population_as_stats`).
  예외: 대표건 projection 을 지도는 고른 분류의 행만으로, 통계는 세 분류를 합쳐서 한다 — 분류를 넘나드는 중복군이 있으면 달라질 수 있다(기존 동작, 바꾸지 않음).
- 목록 `/data/<분류>` 는 통계에서 넘어온 `reportName reportDateStart/End occurDateStart/End responseDateStart/End occurTimeStart/End` 를 같은 이름의 상세 검색칸에 채운다(연도 → 답변일 범위).
  기관·담당자 상세 링크는 `agencyKey=<행 agency_key>`와 `status=완료`를 추가한다. registry 집계 키를 동일 resolver로 해석하며
  같은 표시명의 서로 다른 코드가 합쳐지지 않는다. `agencyKey`가 없으면 기존 `agency/agencyExact` 동작을 유지한다(외부 API 응답 변경 없음).

- (2026-09-28 추가) `/stats` 기관·담당자·법규 행은 답변 완료 신고만 집계한다. `in_progress`·`in_progress_pct` 필드는 남지만 0이다(의미 축소, 삭제 아님 — 구 앱 호환).
  `disposition_unknown` 은 저장값 '미확인' + 주정차·버스전용차로·쓰레기 메뉴의 일부수용·처분 빈값. 파서는 그 일부수용에 '미확인'을 저장한다(parser-vectors).

## 2026-10-05 신고 지도 핀 기준(pin_basis)

- 정본: `contracts/map-pin-basis-vectors.json`(서버·모바일 바이트 동일). 주소키 = `trim(주소정규화)`, 비면 `trim(위반장소)`.
- `pin_basis=coords`(기본): 각 신고의 공식 위도·경도(기존 동작 그대로). `address`: 주소키가 있는 신고는
  같은 주소키 신고들(현재 조회 모집단)의 유효 공식 좌표 중 가장 많이 나온 (위도,경도) 쌍에 찍는다.
  동률이면 위도 작은 쌍, 다음 경도 작은 쌍. 자기 좌표가 없어도 같은 주소에 유효 좌표가 있으면 찍히고,
  주소키가 빈 신고는 자기 좌표, 같은 주소에 유효 좌표가 하나도 없으면 좌표 없음. 표시용 계산이며
  DB 의 위도·경도·지오코딩상태, DB 교환·백업·CSV·커뮤니티 업로드는 바꾸지 않는다. 외부 주소 변환 없음.
- 서버 구현: `services/stats/map.py` 의 `apply_pin_basis(frame, basis)`(순수 함수, 입력 불변) 뒤
  `get_report_map_stats`·`get_report_map_missing_summary`·`get_report_map_missing_groups` 가
  `pin_basis` 키워드(기본 `"coords"`, `address` 외 모두 `coords` 로 정규화)를 받는다.
  유효 좌표 판정 뒤 모든 단계(geocoded/missing 건수, bounds 거르기, 점/클러스터, 좌표 없는 요약·목록)는
  effective 좌표 기준. `get_report_map_stats`·`get_report_map_missing_groups` 의 `meta` 에 `pin_basis` 추가(하위호환).
  `@cached` 키는 kwargs 를 포함하므로 모드별 캐시가 분리된다.
- 경로(모두 선택 쿼리 `pin_basis`, 없으면 coords — 기존 응답과 동일):
  웹 `/stats/map`, `/stats/map/points`, `/stats/map/missing`;
  API v1 `/api/v1/stats/map`, `/api/v1/stats/map/points`, `/api/v1/stats/map/missing`.
- `address_groups`(서버 정의가 정본, 모바일도 같게): effective 좌표가 있는 신고의
  서로 다른 (위도, 경도, 주소키) 조합 수
  (`geocoded_df[['위도','경도','주소키']].drop_duplicates()`). 주소키가 빈 신고도
  (위도, 경도, '') 로 센다. `pin_basis=address` 에서는 effective 좌표로 센다.
  벡터 기대값 coords 6 / address 4 — `tests/test_map_pin_basis.py` 가 확인한다.
- 공간 칸 묶음 점(`cluster: true`) 이름(후속 B, 2026-10-05 —
  고정 문구 '영역 집계'/'이 영역의 신고' 제거). 정본:
  `contracts/map-cluster-label-vectors.json` description(서버·모바일 바이트 동일).
  칸 안 주소키 = `trim(주소정규화)`, 비면 `trim(위반장소)`.
  `address_count` = 빈 주소키를 뺀 종류 수(하위호환 추가 필드, 묶음 점에만).
  대표 주소키 = 신고 수 최다(동률이면 문자열 비교 작은 것).
  `address` = 대표 주소키 신고들의 빈 문자열이 아닌 `trim(위반장소)` 중 가장 작은 값,
  없으면 대표 주소키, 주소키가 하나도 없으면 빈 문자열.
  `region` = 0곳이면 '주소 정보 없음', 1곳이면 address, 2곳 이상이면 '{address} 외 {N-1}곳'.
  서버 계산은 순수 함수 `services/stats/map.py` 의 `cluster_cell_label(pairs)` —
  벡터 직접 테스트 + `max_points` 를 작게 한 `get_report_map_stats` 로 확인한다.
  비묶음 점의 address/region 은 그대로다.

## 2026-09-24 사진 촬영 시각 컬럼 (주정차 과태료 추정용)

- detail/merge 3개 테이블에 `사진_첫촬영`(TEXT `YYYY-MM-DD HH:MM:SS`), `사진_끝촬영`(TEXT), `사진_촬영수`(INTEGER) 추가. `upgrade_schema()` 가 자동 ALTER.
- 의미: NULL = 아직 시도 안 함(또는 네트워크 오류로 다음 크롤링에 재시도), `사진_촬영수 = 0` = 받았지만 EXIF 촬영 시각 없음.
- 채우는 곳(세 군데, 모두 `services/photo_capture_time.py`): 크롤링 상세 저장 때 `core/storage/reports_repo._prefetch_derived`(주정차 신고만, 트랜잭션 밖에서 사진 앞 128KB 스트리밍), 크롤링 끝 `start._process_and_save_results` 의 `backfill_missing`(종결돼 다시 안 받는 신고 재시도, 한 번에 30건), 서버 기동 때 `maintenance_service.start_photo_backfill`(남은 대상 전체 한 번 훑기).
  값은 재크롤링에서 이어받고, 변경 감지(`synced_at`, 변경 알림)에 포함하지 않는다.
- 모바일 Standalone 도 같은 세 시점(상세 저장 때 `MaintenanceService.prefetchForSave`, 동기화 끝 `backfillMissing(limit: 30)`, 앱 열 때 `startPhotoBackfill`)과 같은 6개월 기준일(말일 보정)로 채운다 — 한쪽을 바꾸면 다른 쪽도 같이 고친다. 판독 규칙도 같다(`lib/services/photo_capture_time.dart`, 2026-09-25). 공용 합성 JPEG 벡터 `contracts/exif-vectors.json`(두 레포 바이트 동일, `scripts/dev/db_roundtrip_check.py` 가 sha 비교)로 `tests/test_parser_vectors.py` ExifVectorTests 와 모바일 `test/services/photo_capture_time_test.dart` 가 같은 결과를 확인한다.
- 서버↔모바일 변환: `db_backup.restore_from_mobile_db` 가 세 컬럼을 옮긴다. 모바일 `reports` 테이블과 서버 DB 가져오기 쪽도 같은 세 컬럼을 가져야 한다(PROJECT_RULES §3-1).
- API: `/api/v1/reports/*` 는 테이블 전체 컬럼을 내보내므로 세 필드가 추가된다. 대시보드 `recent_answers`/`watchlist` 레코드에도 포함.
- 추정 과태료(`services/fine_estimate.py`)는 이 컬럼과 번호판·신고 메뉴로 계산하며 DB 에 저장하지 않는다(statistics-spec §4-2).
- 2026-10-12: 로컬 통계가 '추정 과태료'로 세는 신고는 커뮤니티 공유 payload 의 `fine_estimate` 선택 필드로도 나간다 — 입력·판정은 통계와 공유(중복 구현 없음),
  상세는 `community-upload.md` "추정 과태료 공유" 절.

## 2026-09-24 서버↔모바일 DB 왕복 검사 (PROJECT_RULES §3-1)

- `scripts/dev/db_roundtrip_check.py --mobile-repo <모바일 작업트리>` — 실제 변환 코드를 이어서 돌린다.
  A: 서버 S0 → 모바일 `importFromServerDb`(Dart, `test/tool/db_roundtrip_harness_test.dart`) → M1 → 서버 `restore_from_mobile_db` → S2, S0 == S2.
  B: M1 → S2 → 모바일 M3, M1 == M3. NULL/''·정수/실수 타입까지 원시 값으로 비교, 차이 1건이라도 있으면 종료코드 1.
- 서버 쪽은 `SAFETYREPORT_DATA_DIR` 임시 폴더 + fixture 모드 서브프로세스, 모바일 쪽은 sqflite FFI. 운영 DB·외부 요청 없음.
- S0 은 fixture 24건에 크롤러와 같은 값(주소정규화 3컬럼, `synced_at` 백필)과 까다로운 값(사진 시각·`사진_촬영수=0`, NULL 벌점, 빈 별점사유, 줄바꿈·따옴표, 실수 좌표, 지오코딩 캐시 ok/not_found)을 넣는다.
- 허용 차이는 하나: `mysafety_sync_meta.map_backfill_state`(서버 지도 백필 런타임 상태, 복원 직후 서버가 새로 쓰고 모바일은 의도적으로 버림).

## 2026-09-25 서버↔모바일 계산 동등성 검사

- `scripts/dev/logic_parity_check.py --mobile-repo <모바일 작업트리> [--server-db <사본> --summary-only]` — 같은 DB(S0 → 모바일 가져오기 M1)로
  서버 `get_dashboard_stats`/`get_agency_stats`/`get_stats_overview` 와 모바일 `computeSummary`/`computeStats`/`computeStatsOverview`
  (`test/tool/logic_parity_harness_test.dart`)를 각각 계산해 비교한다. 조합: 취하 제외 × 대표건(raw/canonical) + 필터(최근 연도, 첫 법규, 연도+법규, 법규 없음) = 44.
- 한쪽에만 있는 키(서버 `by_law`·`traffic_total_fine`·`estimate_rule_version`·`dedupe_mode` 등 모바일 화면이 쓰지 않는 필드)는 따로 세고 실패로 치지 않는다.
- 첫 실행에서 찾은 차이: 통계 법규 선택지(`available_laws`) 범위가 달랐다(모바일은 필터 없이 전체, 서버는 연도·취하 제외 적용 후) · 법규 필터로 카테고리가 비면 서버가 선택지를 비웠다. 둘 다 맞췄다 → fixture·운영 사본(3,063건) 모두 48조합 차이 0.
- 변경 판정 열 목록은 계약 `change_tracked`(서버 `CHANGE_TRACKED_COLUMNS`, 모바일 `_syncedAtTrackedKeys`)로 묶었다.
- 알려진 정규화(실제 크롤러 출력에는 나타나지 않아 현재 데이터 손실은 없음, 운영 사본 NULL 0건 확인): 모바일 가져오기와 서버 복원이
  지오코딩 캐시 `error_message` NULL→'', 주소정규화·행정구역·지오코딩상태 NULL→''/재계산, `synced_at` NULL→가져온 시각으로 바꾼다. 저장 로직 리팩터링 때 NULL 보존으로 정리한다.
- 변환 코드(서버 `db_backup.py`, 모바일 `local_db_service.dart` import/export)나 공통 컬럼을 바꾸면 이 검사를 돌린다.

## 2026-09-24 저장 계층 재설계 R1 — 교환·복원·스키마 버전

- **저장 계약** `contracts/storage-contract.json`(모바일 레포 같은 경로에 바이트 동일). 모델·표가 어긋나면 `tests/test_storage_contract.py` 가 실패한다.
- **스키마 버전**: 서버 `PRAGMA user_version` = `core/database/database.py SCHEMA_VERSION`(현재 2). 더 새 버전 DB 는 upgrade 전에 거부. 모바일은 `LocalDbService.dbVersion`(현재 12).
- **감시목록**: 원천은 `mysafety_watchlist` 하나. title·merge 의 `감시목록` 열은 `database.refresh_watch_flags` 가 계산(merge_final·감시목록 변경 때). 서버 sync_meta 에는 'watchlist' 키를 두지 않는다(모바일은 sync_meta 'watchlist' 가 원천이라 교환 때 변환).
- **복원**(`services/db_backup.py` → `core/storage/exchange.py`): 사본에 적용 → 무결성 검사 → 백업(`data/backups/`) → 원자적 교체. 크롤링·지도 좌표 변환 중에는 409.
  모바일 DB 로 복원해도 관리자·API 키·변경 기록은 유지되고 지오코딩 캐시는 합쳐진다. 구버전 앱 DB 에 수정값·중복 판단 표가 없으면 서버 것을 유지.
- **2026-09-26 감사 보강(SOL-02·03·04, 모바일 레포와 함께 변경)**:
  - 모르는 열: 변환 대상 표에 계약(`storage-contract.json`)에 없는 열이 있고 그 열에 NULL 아닌 값('' 포함)이 하나라도 있으면
    교체 전에 거부한다(서버 `exchange.UnknownColumns` → 409 문장, 모바일 `UnknownColumnsException`). 값이 모두 NULL 인 열은 잃는 값이 없어 통과.
  - 모르는 분류: 모바일 `reports.category` 가 `traffic|parking|other` 가 아니면(NULL·빈 값 포함) 기타로 바꾸지 않고, 해당 ID·값을 밝혀
    `DB_INVALID` 로 거부한다(서버 `exchange._refuse_unknown_categories`, 2026-10-04 기술일지 A1-03). 서버는 분류를 표 이름으로만 표현한다.
    서버가 아는 모바일 열 목록은 `exchange.known_mobile_columns()` 이고 테스트가 계약과 같은지 검사한다.
  - entry_value: 서버 `mysafety_entry_value` 행 없음 ↔ 모바일 `reports.entry_value` NULL, 행의 값(빈 문자열 포함) ↔ 같은 값.
    앱에 열이 있으면 앱 값이 원천(빈 문자열도 덮어씀), 열이 없는 구앱이면 서버 값 유지.
  - 복원 중 동시 쓰기: DB 교체 전에 `db_optimize.pause_for_restore()`가 복원 진행 표시를 켜고
    실행 중 최적화가 현재 신고 1건(네트워크 재조회 중이면 그 저장을 하지 않고)을 끝내고 멈출 때까지
    기다린다(60초 기본, 넘으면 409 거절). 소유 토큰을 돌려주어 획득한 호출자만 `resume_after_restore(token)`으로
    해제한다 — 획득 전 사전 검사 실패는 표시를 건드리지 않고, 중첩된 두 번째 복원이 장벽에서
    거절돼도 첫 복원의 표시를 끄지 않는다.
    작업자 수명 규칙: `_life_lock` 하나가 작업자 스레드·일시정지 토큰 스택·서버 종료 요청(`_shutdown`)·
    일시정지 요청을 지키고, 모든 기동·재판단은 `_spawn_locked()` 하나만 쓴다(`_shutdown` 아님 + 토큰 없음 +
    살아 있는 스레드 없음 + 할 일 있음일 때만 새 스레드). `_shutdown` 은 `stop()` 만 켜고 새 수명 시작에서만
    끄므로, 시간 초과가 종료 요청을 지우지 않고(서버 종료 뒤 재시작 없음) 작업자 종료(`_thread=None` 전환과
    같은 잠금 안 재판단)와 시간 초과·복원 종료가 엇갈려도 작업이 중단된 채 남지 않는다(실패 종료만 재시작 안 함 —
    완료 여부와 무관하게 메타 기준 `needs_run` 으로만 재판단하므로, 마지막 신고 뒤 일시정지로 done 기록을
    건너뛰어도 시간 초과·복원 종료 때 다시 뜬다).
    잠금 순서 규칙(`services/db_optimize.py` 상단): 수명 잠금 안에서는 상태 판단·스레드 생성만 하고
    보류 크롤 시작·크롤 상태 조회(다른 모듈 잠금)는 하지 않는다 — 보류 시작은 잠금을 놓은 뒤 별도로 실행한다.
    상태 조회(`is_running` 등)는 수명 잠금이 아니라 상태 잠금(`_lock`)만 잡으므로, 크롤 시작 잠금을 쥔 채
    수명 잠금을 잡는 경로가 없어 기동 경로와 수동 크롤 시작이 교착되지 않는다.
    표시가 켜져 있는 동안 `maybe_start`는
    시작하지 않으며, 멈춰 끝난 옛 스레드를 반환하지 않고 복원 뒤 새 DB로 새 스레드를 시작한다.
    복원과 최적화가 동시에 돌지 않으므로 ID 목록·정체 경합이 없다(정체 확인은 방어용으로 유지).
    스테이징 복사부터 파일 교체까지 `core/database/write_barrier.exclusive()`가 운영 DB 연결을 막는다.
    복원 완료 지점(성공·실패 공통, 자기가 획득한 경우에만)에서는 진행 표시를 끄고 `db_optimize.maybe_start`로 새 DB 메타를 다시 판단한다.
    이미 빌린 연결이 반납될 때까지 기다리고(20초 넘으면 409), 그동안 새 연결은 기다렸다가 교체된 새 파일로 다시 연다.
    크롤러는 별도 프로세스라 이 장벽 밖이므로, 복원은 `crawl_manager.hold_for_restore()` 로 크롤 시작 검사와 **같은 잠금 안에서**
    "복원 중" 을 표시한다. 그동안 크롤 시작은 `CrawlBlockedByRestore`(화면 문장), 대기 큐 자동 시작은 신고번호를 큐로 되돌린다(Sol 재검증 SOL-04).
    크롤 중지(`/crawl/kill`)는 종료 신호 뒤 **실제로 끝난 것을 확인한 다음에만** 프로세스 참조를 지운다(10초 뒤 kill, 그래도 살아 있으면 참조 유지 →
    복원 계속 거부, 감사 R2-01). 대기 큐는 `data/crawl_pending_queue.json` 에도 남아 재시작 뒤에도 유지되고(시작 때 자동 크롤은 안 함),
    파일에 쓰지 못하면 대기열에 넣지 않고 오류로 답한다(R3-03).
    대기 큐 자동 시작(크롤 완료 뒤·복원 뒤)은 `crawl_manager.launch_pending_crawl()` 한 경계(한 번에 하나): 복원 세대를 먼저 읽고
    일반 크롤과 같은 허용 검사(게이트·1회 초기화) → `start_crawl(prepare=…, restore_generation=…)` 가 **같은 잠금 안에서** 세대가 그대로인지 확인하고
    (검사 뒤 복원이 있었으면 시작 안 함, R4-03) 시작이 확정된 뒤에만 로그 교체·실행별 큐 파일(`pending_queue_<uuid>.txt`)을 만든다(R4-02).
    맡은 번호는 '예약'(다른 실행이 다시 맡지 않음)으로 둔다. 자식(`start.py`)은 큐 번호별 결과를 `<큐파일>.done.json`
    (`services/crawl_queue_report.py`: `processed` = 상세 저장까지 끝남, `not_found`·`ambiguous` = 목록 **전 페이지를 성공적으로** 훑은 뒤에도
    없음·여러 신고에 걸림 — 목록 호출 실패·잘린 페이지·탐색 상한(100쪽) 도달이면 확정하지 않음, 재해석도 정확→접두어→유일한 부분 일치 규칙,
    감사 R6-01·R6-02)에 원자적으로 남기고, 부모는 **이 보고에 있는 번호만** 큐에서 뺀다(형식이 틀린 보고는 없는 것으로 봄, R6-05) —
    종료 코드는 믿지 않는다(로그인 실패도 0 으로 끝날 수 있음, 감사 R5-01). 모호한 번호는 오류 로그로 "정확한 신고번호로 다시 요청" 을 알린다.
    남은 번호는 예약만 풀려 큐에 남고 1분부터 두 배씩(최대 30분) 늘어나는 간격으로 다시 시도한다 — 게이트·초기화에 막혀도 다시 걸고,
    서버 기동 때 남은 번호가 있으면 타이머 하나를 건다(R5-02·R6-03). 사용자 크롤 시작(`crawl_control`)도 같은 세대 확인·시작 뒤 로그 교체를 쓰고,
    번호를 대기 큐에 넣을 때마다 한 번 더 시작을 시도한다 — 요청은 작업자 하나로 합친다(R6-04).
    미확인 번호는 목록 **전체를 한 번** 받아 찾는다(첫 페이지 1회 + 각 페이지 1회, R7-04). 처리하지 못하고 뺀 번호(없음·모호)는
    `data/crawl_queue_unresolved.json`(최근 50개)·`/api/v1/crawl/status` 의 `unresolved`·WS `crawl_queue_unresolved` 로 알린다.
    요청 시점에 이미 여러 신고에 걸리는 번호는 대기열에 넣지 않고 400 으로 거부한다(R7-03) — `/crawl/enqueue`·웹 선택·웹 시작·모바일 `/crawl/start`
    (실행 중 대기 분기 포함) 모두(R8-02). 큐의 번호는 **정확 일치만** 바로 믿고, 부분 일치는 목록 전체를 성공적으로 받은 뒤에만 확정한다(R8-01).
    큐 지정 직접 시작도 실행마다 고유 큐 파일(`<queue|mobile_queue|web_selected_queue>_<uuid>.txt`)을 쓰고(R9-01), 시작하지 못하면 그 파일을
    바로 지운다(R10-03). 끝나면 결과 보고를 읽어 없음·모호 번호를 기록하고 파일을 지운다 — 기록을 저장하지 못하면 보고 파일을 남긴다(R9-02).
    직접 시작은 대기 큐가 아니므로 처리하지 못한 번호를 자동으로 다시 돌리지 않는다(사용자가 멈춘 크롤을 되살리지 않게, R10-02) — 결과는
    크롤 로그와 `unresolved` 로 확인한다. 기동 때 남은 파일을 자동 회수하지 않는다(이름만 비슷한 다른 파일을 건드리지 않게, R10-01).
    시작 알림 실패·준비 실패는 예약이나 임시 파일을 남기지 않는다(R5-03·R5-04).
- **API 값**: `/api/v1/reports/{traffic,parking,other}` 는 NULL 을 null, 정수 열(별점·synced_at·보완횟수·사진_촬영수)을 정수로 보낸다. 웹 화면 조회는 기존처럼 ''.
- **변경 알림 payload**(`crawl_changes.json`): NULL 은 '' (표시용).
- 새 표: `mysafety_report_override`(사용자 수정값), `mysafety_duplicate_decision`(중복 판단), `mysafety_change_log`·`mysafety_change_cursor`(변경 기록) — 쓰기는 R2·R5 부터.

## 2026-09-24 저장 계층 재설계 R2 — 서버 저장 규칙

- 크롤링 저장은 `core/storage/reports_repo.save_crawled`(기존 `database.detail_to_sql` 은 튜플 → `CrawledDetail` 어댑터). 신고 1건 = 트랜잭션 1개, 네트워크는 트랜잭션 전.
- **merge(화면용 표) = title + detail(사이트 원본) + `mysafety_report_override`(사용자 수정값) + 감시목록(계산) + 6개월 지난 첨부 "6개월 초과"**. 전체(`merge_final`)와 1건(`refresh_merge_rows`)이 같은 규칙.
- 서버 기동은 legacy 검사·스키마 준비 다음, 스케줄러·크롤 재시도 시작 전에 `repair_stale_merge`로 분류별 title/detail 공통 ID의 행 수·기관코드 건수와 merge를 비교한다. 누락 ID·동일 ID의 기관코드 차이도 검사하고, 기관코드 사용자 수정값(NULL/빈 값 포함)은 정상 원천으로 인정한다. 불일치 때만 기존 `merge_final`로 재생성하며 실패는 예외 종류만 경고하고 기동을 계속한다. 서버 DB 복원도 교체 전 staging DB에 같은 검사를 적용하되 실패 시 복원을 중단한다. 모바일 가져오기는 기존의 전체 `merge_final`을 유지한다.
  detail 은 사이트 원본만 담는다 — 편집기(`db_editor_service.update_record`)는 수정값 표에 쓰고 detail 을 건드리지 않는다.
- 교환은 원본으로: 모바일 가져오기는 title+detail 을 읽고(merge 아님) 수정값은 표로 따로 옮긴다.
- 변경 판정(synced_at·변경 알림): 상세 사이트 열 + category + entry_value + 본문, NULL 과 '' 는 같음. 지오코딩·사진 열은 제외(모바일 `_syncedAtTrackedKeys` 와 같음).
- 중복 판단: `mysafety_duplicate_decision` 이 원천(개별·일괄 저장이 기록). 그룹 재생성은 판단 표 → 기존 그룹 → 자동 기본값 순.
- 별점 일괄 제출·이미 참여 확인은 점수도 기록(`sync_rating_status(score=, cause=)`).
- `upgrade_schema(engine, maintenance=False)`: 크롤링 서브프로세스용 가벼운 확인(표·열·버전). 스키마 버전 4(인덱스).

## 2026-09-24 저장 계층 재설계 R6 — 시각 형식 (S-32)

형식이 여러 개인 것은 받는 쪽이 이미 그 형식으로 읽고 있어서다. 저장된 값을 바꾸지 않고, 새 필드는 아래 규칙을 따른다.

| 종류 | 형식 | 필드 |
|---|---|---|
| 기계용 기록 시각(새 필드 기본값) | 정수 Unix epoch **밀리초** | `synced_at`, `mysafety_raw_content.saved_at`, 중복 그룹·멤버·판단의 `created_at`/`updated_at`, `mysafety_report_override.updated_at`, `mysafety_change_log.created_at`, `mysafety_change_cursor.updated_at` (모바일 같은 이름 열도 같음) |
| 마지막 동기화 | ISO8601 로컬 시각, 시간대 없음 | `mysafety_sync_meta.last_sync` — 서버는 초까지, 모바일(`toIso8601String`)은 마이크로초까지. 둘 다 `datetime.fromisoformat`·`DateTime.parse` 로 읽힌다 |
| 사이트 원문 날짜 | `YYYY-MM-DD` 문자열, 시각은 `HH:MM` | `신고일`·`답변일`·`발생일자`·`발생시각`, 사진 촬영 시각 열 — 사이트가 준 값 그대로 |
| 사람이 보는 표시용 | `YYYY-MM-DD HH:MM:SS` 문자열 | `crawl_done*.json` 의 `timestamp`, `api_keys.created_at`, 기기 연결 목록 — 서버 전용·화면 표시 |
| WebSocket 메시지 | `datetime.isoformat()`(마이크로초 포함) | `/ws/events` 의 `timestamp` |

`synced_at` 의 뜻: "이 신고의 상세가 마지막으로 실제로 바뀐 시각". 2026-05-06 이전 행은 그 시각을 알 수 없어 `답변일`(없으면 `신고일`) 그날 끝으로 추정해 채웠다 —
정렬(최근 답변)용으로는 같은 뜻으로 쓰고, "언제 크롤링했나"로 쓰지 않는다. 크롤링 시각은 `last_sync`.

## 2026-09-24 저장 계층 재설계 R6 — 라우터 실행 (S-29)

- 웹·모바일 API 라우터는 await 가 없으면 `def`(FastAPI 스레드풀에서 실행)로 둔다. 본문을 읽느라 `async def` 인 라우터는 DB·파일 작업을 `run_in_threadpool` 로 넘긴다.
  판다스·SQLite 작업을 이벤트 루프에서 돌리면 복원·편집·중복 판단 저장 동안 WebSocket·미디어 스트림까지 멈춘다.
- 스레드에서 돌기 때문에 크롤링 시작·큐 적재·중지는 `crawl_control._launch_lock` 으로 한 줄로 세운다(실행 중 확인 → 로그 회전 → 프로세스 시작이 끊기지 않게).
- 스레드풀 토큰은 미디어 스트림(`iter_stream`)과 같이 쓴다(anyio 기본 40).

## 2026-09-25 중복군 응답 필드 추가 (W3)
- `duplicate_group_service.get_duplicate_groups()`(웹 관리 화면, `/api/v1/duplicates/groups`) 그룹마다 `user_decided`(판단 표에 행이 있음), `decided_at`(epoch ms), `decided_at_text`("YYYY-MM-DD HH:MM") 추가. 기존 필드는 그대로.
- 판단 표는 그룹별 **마지막** 사용자 판단만 보관한다. 여러 번의 변경 이력은 저장하지 않는다(필요하면 서버·앱 계약에 이력 표를 새로 넣어야 함).

## 2026-09-27 이전 버전 DB 처리 — 초기화 크롤링 릴리스 (서버·모바일 함께, 2026-10-17 변경으로 일부 바뀜)
2026-10-17 부터 지원 범위(서버 6~7, 모바일 앱 17~18)의 예전 DB 는 `migrate_schema` 로 올려서 받는다(위 절). 아래 "옮기지 않는다"는
그보다 오래된 DB(서버 5 이하·모바일 16 이하)에만 해당한다. 5 → 6 → 7 체인, 5 미만은 백업 뒤 비우기.
당시 릴리스는 이전 DB 를 새 구조로 옮기지 않았다. 아래 "2026-09-25 업데이트 때 DB 처리" 의 업그레이드 로직(업그레이드 전 백업, 열 추가 ALTER,
번호 붙은 마이그레이션, 감시목록 열 이관, entry_value 재분류, synced_at 백필, 상태 정규화)은 `core/database/database.py upgrade_schema` 안에 **주석으로 남겨 비활성**했다.
함수 본체(`_apply_versioned_migrations`·`_migration_*`·`backup_before_upgrade` 등)는 다음 스키마 변경 때 다시 켜려고 남겼다.
- `upgrade_schema`: 새 DB(표 없음)는 지금 스키마로 만들고 `user_version=SCHEMA_VERSION` 을 적는다. 지금 버전 DB 는 빠진 표만 만들고 인덱스(`_index_statements`, IF NOT EXISTS)를 매번 확인한다.
  이전 버전 DB(`is_legacy_database`: 표가 있고 `user_version < SCHEMA_VERSION`)는 건드리지 않고 `LegacyDatabase` 로 멈춘다. 더 새 버전은 전처럼 거부.
- 서버 시작(`main.py`): `upgrade_schema` 전에 `reset_legacy_database(engine, data/backups, before_reset=community dataset 선회전)`.
  이전 버전이면 먼저 쓰기 잠금(명시적 `BEGIN IMMEDIATE`, 최대 `LEGACY_RESET_LOCK_TIMEOUT` 300초 대기)을 잡고 **그 안에서 버전·표를 다시 확인**한다
  (다른 프로세스가 먼저 끝냈으면 ROLLBACK 하고 아무것도 안 함 — 동시 기동 때 비운 뒤 수집한 신고를 다시 지우지 않게, Sol 검토 1).
  → sqlite backup API 로 `data/backups/legacy_v<옛 버전>_<시각>.db` + `integrity_check`(잠금 중에도 읽기는 됨) → 선회전 → 같은 트랜잭션에서
  남길 표 외 전부 DROP(보기 포함, 가상 표(FTS)를 먼저 지우고 나머지는 IF EXISTS — 보조 표가 함께 지워진다) → 지금 스키마 CREATE·인덱스 → `sync_meta[legacy_reset]` 기록 → `user_version=SCHEMA_VERSION` → COMMIT.
  백업·선회전이 실패하면 아무것도 지우지 않고 서버 시작이 멈춘다. 트랜잭션 안에서 실패하면 전부 되돌아간다.
- 크롤러(`start.py _prepare_database`)는 이전 버전 DB 면 `--reset` 을 포함해 무엇이든 바꾸기 전에 `LegacyDatabase` 로 멈춘다(비우기는 서버 시작만, Sol 검토 3).
  `--reset` 은 `sync_meta` 를 지울 때 `watchlist` 와 함께 `legacy_reset` 기록도 남긴다.
- 남기는 표(`LEGACY_KEEP_TABLES`, 구조가 지금과 같을 때만): `admin_users`·`api_keys`(관리자 로그인·모바일 연결, 구조가 다르면 비우지 않고 멈춤),
  `mysafety_watchlist`, `mysafety_geocode_cache`. 그 밖(신고·상세·병합·원문·entry_value·중복군·sync_meta·수정값·중복 판단·변경 기록)은 비운다.
  예전에 직접 고친 값·중복 판단·메모는 옮기지 않는다(백업 파일에만 남음).
- 기록 `mysafety_sync_meta[legacy_reset]` = JSON `{from_version, backup, kept, dropped, at}` (`legacy_reset_info`). 초기화 크롤링 판정과 안내 화면이 쓴다.
- 가져오기 거절(`core/storage/exchange.refuse_other_version`, `restore()` 첫 단계, 2026-10-17 변경 전): 서버 DB 는 `user_version == SCHEMA_VERSION`,
  모바일 DB 는 `user_version == MOBILE_SCHEMA_VERSION`(계약 `schema_version.mobile`, 테스트가 확인)만 받았다.
  2026-10-17 부터는 지원 범위(서버 6~7, 모바일 앱 17~18)면 올려서 받고, 범위 밖만 `LegacyDatabaseRefused`(너무 오래됨)/
  `FutureDatabaseRefused`(더 새 버전)로 거절한다(RestoreRefused → 라우트 409) — 쓰기 장벽·스테이징 전에 멈춘다.
- 모바일은 같은 규칙(`LocalDbService.resetLegacyDatabase`·`_refuseOtherVersion`, 모바일 레포 `docs/architecture/data-contracts.md`). 계약: `contracts/community-ingest/rebuild.md` "이전 버전 개인 DB".
- 되돌리기: 서버를 멈추고 `data/data.db`(와 `-wal`/`-shm`)를 `legacy_v*` 백업으로 바꾼 뒤 **이전 버전 서버**로 띄운다(이번 서버로 띄우면 다시 비운다).

## 2026-09-27 신고 자료의 주인 = 카카오 계정 (서버·모바일 함께)

사용자 결정: 카카오 로그인은 필수, 카카오 로그아웃은 신고 자료를 지운다, 다른 사람의 DB 는 가져오지 못한다.
- 키 `kakao_member_id`: 서버 `mysafety_sync_meta`, 모바일 `sync_meta`. 값은 카카오 회원번호 원문(숫자 문자열, GoTrue `identities[provider=kakao]` 에서 읽음 —
  `user_metadata` 는 쓰지 않음). 스키마·버전 변경 없음(key/value 행 하나). 교환 때 다른 sync_meta 키와 같이 그대로 옮긴다 —
  `scripts/dev/db_roundtrip_check.py` 가 S0·M1·S2·M3 네 DB 모두 같은 값인지 따로 확인한다(`[owner]`).
- 쓰는 때: 게이트가 처음 통과할 때(주인 표시가 없을 때만), "신고 내역 지우고 이 계정으로 시작". 지우는 때: 카카오 로그아웃·위 전환(자료와 함께).
  `start.py --reset` 은 이 키를 남긴다(같은 사용자의 초기화).
- 가져오기·복원(서버 `exchange.restore`, 모바일 `importFromServerDb`·`replaceFromBackup`): 버전 검사 바로 뒤·무엇이든 바꾸기 전에 파일의 주인을 본다.
  없거나(이 기능 전 DB 포함) 지금 로그인한 계정과 다르거나 로그인 계정 번호를 모르면 거절(서버 409 `ForeignDatabaseRefused`, 모바일 `ForeignDatabaseException`).
- 카카오 로그아웃이 비우는 범위 = 이전 DB 초기화와 같다: 서버는 `admin_users`·`api_keys`·`mysafety_watchlist`·`mysafety_geocode_cache` 유지,
  모바일은 감시목록(`sync_meta['watchlist']`)·`geocode_cache` 유지. 백업은 만들지 않는다. 크롤링·동기화·지도 변환 중이면 거절(아무것도 안 바꿈).
- API: `POST /api/v1/community-auth/disconnect` 는 **삭제**(사용자 결정 "경로 삭제", 코드 주석 처리). 폰에서 서버의 카카오 로그인을 풀 수 없다.
  상태 DTO 의 로그인 후보에 `is_different_data_owner`(하위호환 추가). 게이트 상태에 `db_owner_mismatch` 추가.

## 2026-09-25 업데이트 때 DB 처리 (서버) — 2026-09-27 부터 비활성(위 절)
- 서버 기동(`main.py`)·크롤러(`start.py`)가 `upgrade_schema(..., backup_dir=data/backups)` 를 부른다. DB 의 `PRAGMA user_version` 이 코드(`SCHEMA_VERSION`)보다 낮으면,
  표·열 추가를 포함해 **무엇이든 바꾸기 전에** `data/backups/before_schema_v<옛 버전>_<시각>.db` 로 SQLite backup API 복사(WAL 포함). 이 접두어 파일은 최근 5개만 남긴다.
- 그 뒤 단계별 마이그레이션(v1~)은 단계마다 트랜잭션 — 실패하면 그 단계는 되돌아가고 버전도 오르지 않는다. 코드보다 새 버전 DB 는 거부.
- 복원 경로(`core/storage/exchange.restore`)의 임시 사본 업그레이드는 백업하지 않는다(복원 자체가 교체 전 `data_before_restore_*.db` 를 남김).
  같은 초에 복원이 겹치면 `data_before_restore_<시각>_2.db` 처럼 이름을 배타적으로 확보해 앞 백업을 덮지 않는다(2026-10-04, A1-04).
- 되돌리기: 서버를 멈추고 `data/data.db`(와 `-wal`/`-shm`)를 백업 파일로 바꾼 뒤 **이전 버전 서버**로 띄운다(새 서버로 띄우면 다시 올린다).

## 2026-09-25 업데이트 뒤 한 번 훑기 작업 (서버)
- `services/maintenance_service.py`: 서버 기동 때(스키마 업그레이드 뒤) 신고일 6개월 이내 주정차 신고 중 `사진_촬영수` NULL 인 것의 첨부 사진 EXIF 촬영 시각을 채운다.
  한 건마다 0.4초 간격, 크롤링 중에는 기다렸다 이어 감. 진행은 메모리에만(재기동하면 남은 대상부터 다시 셈). 네트워크 오류 건은 다음 기동·크롤링 끝 재시도(`photo_capture_time.backfill_missing`, 30건)에서 다시.
- 추정 과태료·처분 분류는 통계를 볼 때 저장된 값으로 계산하므로 훑을 필요가 없다.
- 진행 표시: 모든 화면 하단 `#srJobBar`(base.html, 작업 있을 때만), `GET /maintenance/status`(웹), `GET /api/v1/maintenance/status`(Client 앱). 지도 좌표 변환 작업은 실행하지 않는다.
  응답: `{active, jobs:[{key,label,state(running|paused|completed),total,done,current,message}]}`.

## 2026-09-26 커뮤니티 필수 게이트·공유 업로드 계약 (하위호환 추가 + 인증 강화)
정본: `contracts/community-ingest/`(map 레포 원본의 사본), 설명: `community-gate.md`·`community-upload.md`·`community-rebuild.md`.

| 항목 | 내용 |
|---|---|
| 게이트 미충족 | `/api/v1/**`(allowlist 제외)·관리자 화면 AJAX → 403 `{"detail":"COMMUNITY_ONBOARDING_REQUIRED","code":…,"gate":{state,reasons}}`. 키가 틀리면 여전히 401 이 먼저 |
| allowlist(API 키) | `GET /api/v1/app/config`, `/api/v1/community-auth/*`, `GET /api/v1/community/gate`, `GET/POST /api/v1/community/rebuild[/start|/resume]` |
| 새 API | `GET /api/v1/community/gate`(서버 게이트 요약, 토큰·UUID 없음), `/api/v1/community/upload/{status,run}`, `/api/v1/community/rebuild[...]` — 실행·시작은 manager 키 + `X-Community-User-Token`(폰 사용자 = 서버 연결 사용자) |
| `X-Community-User-Token` 검증 결과(2026-10-11, cloud-retry) | `/api/v1/community/upload/{status,run}`: 토큰 없음 403 `user_token_required`, 실제로 다른 계정 403 `account_mismatch`, 쿨다운·GoTrue 일시 장애로 확인 자체를 못하면 **503 `cloud_unavailable`**(Retry-After 포함, 재시도 가능 — 이전에는 이 경우도 항상 403 `account_mismatch` 였다). `/api/v1/community/rebuild/*` 는 아직 bool 호환 API(`verify_client_user_token`)만 써서 같은 상황이 403 `user_token_required` 로 보인다(계정 불일치로는 안 보이지만 재시도 신호는 아님 — 후속 과제, `community-gate.md` §3.0.0.3 참조) |
| 크롤 시작 | `/api/v1/crawl/start`·`/crawl/enqueue`: 게이트 60초 재검증 실패 403 `detail=COMMUNITY_ONBOARDING_REQUIRED`, 초기화 필요·진행 중 409 `detail=COMMUNITY_REBUILD_REQUIRED` |
| WebSocket | `/ws/events` 게이트 미충족·상실 시 4403(인증 실패는 기존 4001). **`/crawl/ws/logs`·`/rating/ws/rating_logs` 는 이제 API 키(`?api_key=`/`X-API-Key`) 또는 관리자 세션 필요** |
| `/media/*` | 이제 관리자 세션 또는 API 키 + 게이트(이전: 인증 없음) |
| 기존 필드 | `/api/v1/community-auth/status` 의 `upload_enabled` 유지(의미: 연결 + 게이트 통과) |
| 로컬 저장 | `data/community.db`(개인 `data.db` 와 분리 — 서버↔모바일 DB 교환 대상 아님, PROJECT_RULES 3-1 영향 없음), `data/auth/community_writer.enc`(업로드 연결 비밀, 암호화) |
| 업로드 결과(2026-09-27 UC-1) | `/api/v1/community/upload/run`·`status` 의 `result`/`last_result` 는 **기존 값 그대로**(success·no_change·partial·deferred·auth_required·consent_required·failed — 모바일 앱 호환). 새 결과 코드는 추가 필드 `outcome`/`last_outcome`(sent·no_pending·not_due·cooldown·busy_other_run·needs_auth·needs_consent·blocked_gate·failed·more_pending·partial), cooldown 이면 `next_attempt_kst`. status 추가 필드: `states`·`auth_required`·`quarantined`·`stored`·`oldest_unsent_kst`·`next_retry_kst`·`last_central_ack_kst`·`control{state,until_kst,reason}`·`last_request`(8자). 제거·변형 없음 |
| `community.db` 스키마 | `meta.schema_version` **2**(모바일 같은 값): v2 = 영속 전송 제어 `upload_control(scope, state ready\|cooling_down\|probing, next_attempt_at, consecutive_failures, last_error_code, updated_at)` + `upload_runs.result` CHECK 확장(표 재생성·행 보존) + 인덱스. v1 파일은 열 때 번호 단계로 올린다(지우거나 초기화하지 않음). 교환 대상 아님(3-1 무관) |

## 이관 원문

<!-- legacy CLAUDE.md 199-307 -->
## 주요 변수명 / 필드명 정리

### 설정값 (settings.py self.xxx)
```
datapath / config_path / resultfile / resultpath / logfile / logpath / db_path
table_title / table_detail_traffic / table_detail_parking / table_detail_other
table_merge_traffic / table_merge_parking / table_merge_other
loginurl / myreporturl / mysafereporturl / titletable / remotepath
chrome_mode / remote_debug_port / headless
username / password / phone_number
telegram_token / chat_id / telegram_enabled
google_api_auth_file / google_sheet_key / google_sheet_enabled
scheduler_enabled / scheduler_mode / scheduler_interval_hours / scheduler_cron_times / scheduler_interval_start
exclude_withdraw / use_representative_records / auto_export_excel / auto_export_sheet
crawl_mode / crawl_type / max_empty_pages / retry_interval / max_retry_attemps
session_max_age / log_level / TZ / trusted_proxies
```

### config.ini 섹션+키
```
[SELENIUM]    remotepath / chrome_mode / remote_debug_port / headless
[LOGIN]       username / password
[TELEGRAM]    telegram_token / chat_id
[SCHEDULER]   enabled / mode / interval_hours / cron_times / interval_start
[RATING]      phone_number
[SETTINGS]    auto_export_excel / auto_export_sheet / crawl_mode
              exclude_withdraw / use_representative_records
              retry_interval / max_retry_attemps / max_empty_pages
              session_max_age / log_level / TZ / trusted_proxies
[Crawler]     crawl_type
[GOOGLESHEET] sheet_key
[COMMUNITY]   enabled / supabase_url / publishable_key / site_url / device_label / api_key_managers / upload_enabled
              (2026-09-25, 상세 docs/architecture/community-account.md)
```

### DB 컬럼명
**mysafety:** `ID` `상태` `신고번호` `신고명` `신고일` `만족도조사여부`

**mysafetydetail_*/mysafetymerge_* 공통:**
```
ID  처리상태  차량번호  위반법규  범칙금_과태료  벌점
처리기관  담당자  답변일  발생일자  발생시각  위반장소
종결여부  신고내용  처리내용  지도  첨부사진  첨부파일
```
merge에는 title 컬럼(`상태` `신고번호` `신고명` `신고일` `만족도조사여부` `감시목록`)도 포함

title 컬럼에 `별점`(Integer 1~5, NULL 가능) `별점사유`(TEXT) 추가됨. merge에도 동일 컬럼 포함.

**mysafety_watchlist:** `신고번호` / **admin_users:** `username` `password_hash` `salt` / **api_keys:** `key` `name` `created_at`

### 크롤러 파싱 키 (API 응답 원시값)
```
C_A_CONTENTS  C_A_BODY  C_APP_GUBUN_NM  RN_ADRES
C_A_ADD2  C_A_ADDR_HEAD  C_A_ADDR_TAIL  C_NOW
STTEMNT_IMAGE_URL  ARR_C_FILES  answers
```
**answers[*]:** `C_MANAGER_TYPE_NM` `C_R_PROC_STAT_NM` `C_MANAGE_ORG_NAME` `C_MANAGE_MAN` `C_R_MOD_ID` `C_DATE` `C_R_MOD_DATE` `C_MANAGE_CONTENTS` `C_R_BODY`

**ARR_C_FILES[*]:** `FILE_URL` `ATCH_FILE_ID` `FILE_TY` `ORGINL_FILE_NM` `FILE_EXTSN` `EXT`

### 조회/통계 서비스 반환 딕셔너리 키

`services/data_service.py` 는 현재 `report_query_service.py`, `report_stats_service.py`,
`crawl_state_store.py` 를 재노출하는 호환 facade다. 아래 키는 실제 서비스 구현이 유지해야 하는 외부 계약이다.

**get_dashboard_stats():**
```
last_crawl_time  total
acceptCount  partialCount  rejectCount  processingCount  supplementCount  completedCount  withdrawCount  withdrawRawCount
tFineCount  tPenaltyCount  tRejectCount  tUnconfirmedCount
accept_pct  partial_pct  reject_pct  processing_pct  supplement_pct  withdraw_pct
tfine_pct  tpenalty_pct  treject_pct  tunconfirmed_pct
recent_answers  watchlist  exclude_withdraw
```

**get_traffic/parking/other/all_records():**
```
ID  신고번호  신고명  신고일  답변일  처리기관  담당자  처리상태  결과
범칙금_과태료  벌점  차량번호  위반법규  위반장소  발생일자  발생시각
신고내용  처리내용  첨부사진  첨부파일  지도  감시목록  category
```
**get_duplicate_records():** 위 + `total_count` `valid_count`

**get_agency_stats() → traffic/parking/other 각각:**
```
by_agency / by_person / police_by_agency / police_by_person / other_by_agency / other_by_person
  └ agency  person  total  fines  fines_pct  warnings  warnings_pct  rejects  rejects_pct
```

### 모바일 API 응답 필드 (/api/v1)
공통 래퍼: `status` `data` `count`

**crawl/done:** `done` `timestamp` `changed_count`
**crawl/config:** `crawl_type` `crawl_mode` `max_empty_pages`
**stats:** `traffic` / `parking` / `other` / `available_years` / `traffic_total_fine`

Flutter Report 모델 필드(fromJson 매핑) 및 모바일 상세 구조는 `safetyreport-mobile` 레포의 CLAUDE.md 참조.

### category 전파 규칙

- `category` (`traffic` / `parking` / `other`) 는 단순 표시용이 아니라
  웹 상세 모달 링크와 모바일 상세 시트 링크가 원래 카테고리 탭으로 돌아가기 위한
  구조적 계약이다.
- `get_dashboard_stats().recent_answers`, `watchlist`, `get_duplicate_records()`,
  `get_all_watchlist()` 뿐 아니라 `get_all_records()`, `search_by_vehicle()`,
  `search_by_address()`, `get_unrated_records()` 도 각 행에 `category` 를 유지해야 한다.
- 카테고리별 목록 함수는 `_get_records_from_table(..., category=...)` 를 통해
  기본 라벨을 붙이고, 합본/검색 함수는 병합 과정에서 이를 지우지 않아야 한다.

---


<!-- legacy CLAUDE.md 308-371 -->
## DB 테이블

| 테이블 | 설명 |
|--------|------|
| `mysafety` | 신고 목록 (ID, 신고번호, 신고명, 신고일, 상태 등) |
| `mysafetydetail_traffic` | 교통위반 상세 |
| `mysafetydetail_parking` | 주정차위반 상세 |
| `mysafetydetail_other` | 기타 신고 상세 |
| `mysafetymerge_traffic` | 최종 병합 (traffic) |
| `mysafetymerge_parking` | 최종 병합 (parking) |
| `mysafetymerge_other` | 최종 병합 (other) |
| `mysafety_entry_value` | 신고별 entry_value 저장 (ID, entry_value) — 카테고리 재분류 기반 |
| `mysafety_raw_content` | 신고별 원본 payload 저장 (ID, raw_content, raw_type, saved_at) |
| `mysafety_sync_meta` | key/value 메타. `last_sync`(서버 마지막 크롤링 시각, ISO8601), `watchlist`(감시목록 mirror), 기타 확장 키 |
| `mysafety_duplicate_group` | payload exact 중복군 메타데이터 (대표건, 대표건 모드, 중복 상태, 전역 반영 여부) |
| `mysafety_duplicate_member` | 중복군 멤버 목록 (report_id, category, 대표건 여부) |
| `api_keys` | 모바일 API 인증 키 |
| `admin_users` | 웹 관리자 계정 |
| `mysafety_watchlist` | 감시 목록 (신고번호) |

- `mysafetydetail_*`, `mysafetymerge_*` 는 2026-05-06부터 `synced_at INTEGER` 컬럼을 가진다.
  값은 Unix epoch milliseconds 이며, "이 detail/merge 레코드가 마지막으로 실제 반영된 시각"을 뜻한다.
- `synced_at` 는 신규 insert 또는 실제 detail 변경 시에만 갱신된다.
  내용이 같은 단건 재크롤은 기존 값을 유지해야 최근 답변 정렬이 재조회 순서로 오염되지 않는다.
- 구버전 서버 DB를 열면 `upgrade_schema()`가 `synced_at` 컬럼을 추가한 뒤 기존 row도 자동 백필한다.
  - 백필 규칙: `답변일`이 있으면 그 날짜를, 없으면 title의 `신고일`을 기준으로 epoch ms 생성
  - 날짜 문자열에 시각이 없으면 그날의 마지막 시각(23:59:59.999)으로 채워 같은 날짜끼리는 `신고번호 DESC`가 tie-break로 작동하게 한다.
- `raw_content` 는 목록/통계 테이블에 싣지 않고 `mysafety_raw_content` 별도 테이블에 보관한다.
  - 서버 상세 크롤링은 신고 본문 원문을 `raw_type='report_body'` 로 함께 저장한다. 이 row 가 있어야 reset 후 재크롤링에서도 payload exact 중복군이 다시 생긴다.
- 서버 "마지막 크롤링 시각"은 `mysafety_sync_meta.last_sync` 에 ISO8601 문자열로 저장된다.
  - `start.py` 의 `_process_and_save_results` 가 merge 직후 `mysafety_sync_meta` 에 `key='last_sync'`, `value=datetime.now().isoformat(timespec='seconds')` 로 직접 upsert 한다.
  - 모바일 `sync_engine.dart._saveSyncTime()` 와 같은 키/형식이라 서버↔모바일 DB import 시 round-trip 으로 의미가 보존된다.
  - 대시보드는 `report_stats_service.get_dashboard_stats` 가 `mysafety_sync_meta.last_sync` 를 직접 SELECT 해서 `datetime.fromisoformat(...).strftime('%Y-%m-%d %H:%M:%S')` 로 표시한다.
  - 값이 없으면 `기록 없음`. (이전의 `current_crawl.log` mtime fallback 은 제거됨)
- `--reset` 크롤링은 신고 ID 에 매여 있는 사이드카 테이블도 같이 비운다.
  - drop 대상: `mysafety*`, `mysafetydetail_*`, `mysafetymerge_*`, `mysafety_entry_value`, `mysafety_raw_content`, `mysafety_duplicate_member`, `mysafety_duplicate_group`
  - `mysafety_sync_meta` 는 통째로 drop 하지 않고 `key='watchlist'` 만 보존한 채 `last_sync` 등 나머지 키를 삭제한다.
  - `admin_users`, `api_keys`, `mysafety_watchlist` 는 보존.
- 서버-모바일 DB 변환 시 아래 항목은 round-trip 보존 대상으로 취급한다.
  - 신고 row: title/detail/merge 필드, `entry_value`, `synced_at`
  - payload 메타: `raw_content`, `raw_type`, `saved_at`
  - 모바일 메타: `mysafety_sync_meta` (`last_sync`, `watchlist`, 기타 key/value)
  - 중복군 메타: `status`, `representative_mode`, `representative_id`, `apply_globally`, `note`
  - 중복 멤버 메타: `priority_score`, `raw_match`, `field_match`, `created_at`, `updated_at`
  - 보완요청 요약: `보완횟수`, `보완_미응답`, `보완_요청_내용`, `보완_신고자_의견` (detail/merge 컬럼으로 서버/모바일 동일 키)
- `db_backup.restore_from_mobile_db()` 는 모바일 레거시 duplicate hash가 들어와도 `raw_content` 기준 SHA-256 canonical group_id로 재매핑한 뒤 저장한다.
- 보완요청은 신고 행에 **마지막 round 1세트 + 누적 횟수** 만 보존한다. 다회차 이력 전체는 저장하지 않는다.
  - detail/merge 의 신규 컬럼 4개로 표현: `보완횟수` (누적 round 수), `보완_미응답` (`Y/N`), `보완_요청_내용` (요청자/연락처/요청·완료 일시 prefix + 본문), `보완_신고자_의견`.
  - 보완요청 내용 prefix 는 `services/parser.py:_build_supplement_summary()` 가 `"보완 요청자: <name> (<phone>) · 요청 일시: ... · 완료 일시: ..."` 형식으로 조립한다. 마지막 답변자와 최종 판정자가 다를 수 있으므로 누가 이 보완을 요청했는지 본문 안에 함께 표시.
  - round 카운트는 `SPLMNT_DMND_NO`(예전 레거시 HTML 파서 `supplement_parser` 는 2026-09-25 삭제).
  - 보완 완료 시 신고자가 고친 차량번호/발생일자·시각/위반장소는 `parse_json_details` 가 `SPLMNT_VHRNO`·`SPLMNT_DEVEL_*`·`SPLMNT_RN_ADRES` 로 신고 메인 행에 덮어쓴다.
  - 신고 본 row 의 `처리상태/종결여부` 는 보완요청이 열려 있으면 `보완요청 / N`, 종결 상태(취하/답변완료 등)에서는 `보완_미응답='N'` 으로 닫는다.
  - `get_pending_detail_ids()` 가 detail 의 `보완_미응답='Y'` row 를 항상 재크롤링 대상에 포함 → 다음 크롤링에서 답변/추가 round 변화가 자동 반영된다.
  - API 모드 JSON 만으로 보존되는 정보는 마지막 round 의 요청자·요청일시·요청 내용 + 신고자 의견 + 종결 여부 + 누적 횟수에 한정된다. 과거 round 의 요청자 이름은 보존하지 않는다(레거시 HTML 크롤링 제거).
  - crawl_changes payload 의 `change_reason` 은 신고 자체가 보완요청 상태이거나 `보완_미응답='Y'` 일 때 `supplement`, 그 외는 `report`. payload 에 `supplement_count`, `supplement_open`, `보완_요청_내용`, `보완_신고자_의견` 가 함께 들어간다.
- 중복군 자동 감지는 `mysafety_raw_content.raw_content`가 있는 row만 대상으로 한다.
  - 같은 payload hash라도 `차량번호`, `category`, `entry_value`가 충돌하면 기본 상태는 `review_required` + `apply_globally=0`이다.
  - 기본 자동 감지 결과에서 충돌이 없으면 `confirmed_duplicate`, 충돌이 있으면 `review_required`로 시작한다.
  - `not_duplicate`는 “화면에서 숨김”이 아니라 “중복군 메타는 유지하되 canonical projection에서 원본 child를 모두 살려둠”을 뜻한다.
  - `representative_mode='auto'`인 그룹은 refresh 때마다 대표건이 다시 계산될 수 있고, `manual`은 저장된 대표건을 유지한다.
  - 투영 구현(EO R-03, 2026-10-05): `duplicate_group_service` 의 공개 어댑터 하나로 모은다. `normalize_mode`(raw/canonical),
    SQL 투영 `canonical_sql(table, query, mode)`, 같은 snapshot 의 투영 문맥 `ProjectionContext.load(conn, mode, report_ids=None)` →
    `project_frame(df)`(통계: raw 는 그대로, canonical 은 비대표건 제외 + 대표건 감시 승계) / `project_records(records)`(목록: 중복 메타 부착,
    canonical 은 비대표건 제외). 목록 페이지는 페이지 신고가 속한 그룹만 읽는다(`report_ids`). 통계·목록·페이지가 같은 행과 메타를 내는지
    `tests/test_projection_context.py` 가 수동 대표건·비대표건만 감시·not_duplicate 로 검사한다.
  - `raw_content`가 없는 과거 데이터는 자동 확정 대상이 아니라 후속 검토 후보 경로로 다룬다.

---


<!-- legacy CLAUDE.md 413-427 -->
## 설정 (config.ini / settings.py)

| 키 | 섹션 | 설명 | 기본값 |
|----|------|------|--------|
| `crawl_type` | `Crawler` | (2026-09-25 제거) 저장값과 무관하게 늘 `api` 로 읽는다. `/api/v1/crawl/config` 는 구앱 호환으로 `api` 를 돌려준다 | `api` |
| `crawl_mode` | `SETTINGS` | 늘 `full`(`min` 제거, reset 은 저장 안 함) | `full` |
| `max_empty_pages` | `SETTINGS` | (최소 크롤링 제거로 사용 안 함, `/crawl/config` 호환 필드만) | `3` |
| `normalize_police` | `SETTINGS` | 폐지된 저장 키. 읽거나 적용하지 않음. `/api/v1/app/config`만 호환 필드 `false` 반환 | 무시 |
| `exclude_withdraw` | `SETTINGS` | 취하 데이터 숨기기 | `True` |
| `use_representative_records` | `SETTINGS` | 대표건 기준 canonical 집계를 전역 기본값으로 사용 | `True` |
| `auto_export_excel` | `SETTINGS` | 크롤링 후 엑셀 자동 저장 | `True` |
| `auto_export_sheet` | `SETTINGS` | 크롤링 후 구글 시트 자동 업로드 | `True` |
| `enabled` | `COMMUNITY` | 커뮤니티 계정 연결 켜기 (env `SAFETYREPORT_COMMUNITY_ENABLED` 우선) | `false` |
| `supabase_url` | `COMMUNITY` | Supabase 프로젝트 https origin, 공개값 (env `SAFETYREPORT_COMMUNITY_SUPABASE_URL`) | 빈 값 |
| `publishable_key` | `COMMUNITY` | 공개(publishable/anon) 키. `sb_secret_`·service_role 거부 (env `SAFETYREPORT_COMMUNITY_PUBLISHABLE_KEY`, 별칭 `COMMUNITY_SUPABASE_PUBLISHABLE_KEY`·`COMMUNITY_PUBLISHABLE_KEY`) | 빈 값 |
| `site_url` | `COMMUNITY` | 중앙 연결 페이지 (env `SAFETYREPORT_COMMUNITY_SITE_URL`) | `https://safeauth.worklazy.net/` |
| `device_label` | `COMMUNITY` | 중앙 페이지에 보일 서버 이름 | 빈 값("이 PC"/"Docker 서버") |
| `api_key_managers` | `COMMUNITY` | 커뮤니티 계정 관리를 허용한 API 키의 SHA-256 목록(쉼표) | 빈 값 |
| `upload_enabled` | `COMMUNITY` | 업로드 허용(아직 업로더 없음, 화면에서 안 바꿈) | `false` |

커뮤니티 세션·토큰·PKCE 대기값은 config.ini·data.db 가 아니라 `data/auth/community_session.enc`(키 `data/auth/.community_key`)에만 있다 — 백업·모바일 DB 변환 대상 아님.

---


<!-- legacy CLAUDE.md 439-468 -->
## WebSocket 이벤트 (`/ws/events`)

완료 이벤트의 복구 계약은 추가형이다. 기존 `type`/`timestamp`/`data`와 인증·protocol3·gate 종료 코드를 유지한다.
- `crawl_finished`만 `datapath/ws_terminal_events.db`에 먼저 기록한다. `run_id`로 중복 기록을 막고 최근 256개를 보존한다. 교환 DB의 테이블·컬럼을 바꾸지 않는 서버 전용 sidecar다.
- `?after=<event_id>`는 저장한 번호 뒤의 종료 이벤트를 재전송한다. 생략한 기존 클라이언트는 과거 이벤트를 받지 않는다. `connected.data`에 `latest_event_id`, `replay_gap`, `cursor_reset`, `last_attempt`를 추가한다. 보존 범위 밖이면 gap을 알리고, 미래 커서는 reset 후 남은 이벤트를 보낸다.
- 클라이언트별 writer 하나, 대기 32개/4MiB, send 5초/close 1초. 느리거나 초과한 연결은 1013으로 종료한다. thread bridge 대기도 32개로 제한하며, terminal 기록은 연결·대기 슬롯 유무보다 먼저 이루어진다. SQLite 저장 실패는 성공으로 처리하지 않는다.
- 종료 알림과 별도로 실제 변경 자료는 기존 `/crawl/results`의 기기별 cursor 계약을 따른다. 모든 이벤트가 이 종료 journal에 들어간다고 가정하지 않는다.
- 모바일 `WsService`는 설정 scope별 커서를 유지하고 알림 이력과 커서를 **하나의 SharedPreferences commit**에 저장한다. failed/cancelled/unknown은 성공 문구를 쓰지 않는다. gap은 현황 확인을 알리며, 저장 실패는 재연결한다. 구 모바일도 기존 payload를 계속 받는다.


WsService.kt가 `ws://<host>/ws/events?api_key=<key>` 로 영구 연결.

| 이벤트 | 방향 | 설명 |
|--------|------|------|
| `connected` | 서버→앱 | 연결 확인 |
| `ping` (30s) | 서버→앱 | 연결 유지 |
| `pong` | 앱→서버 | ping 응답 |
| `crawl_started` | 서버→앱 | 크롤링 시작 알림 |
| `crawl_finished` | 서버→앱 | 크롤링 완료 + 변경 건수 |
| `crawl_changes` | 서버→앱 | 개별 신고 변경 상세 |

### 핵심 주의: 백그라운드 스레드 브로드캐스트
배경 스레드에서 반드시 `ws_manager.broadcast_from_thread(event_type, data)` 사용.
`new_event_loop()` + `run_until_complete()` 방식은 FastAPI 메인 루프의 WebSocket 객체에 접근 불가 → 이벤트 전달 실패.

`main.py` lifespan startup에서 `ws_manager.set_main_loop(asyncio.get_event_loop())` 호출 필수.

### 메시지 형식
```json
{
  "type": "crawl_finished",
  "timestamp": "2026-04-01T23:00:00",
  "data": {"changed_count": 5}
}
```

---


<!-- legacy CLAUDE.md 469-501 -->
## 모바일 알림 파이프라인

모바일 앱 내부 구현(WsService.kt, 알림 채널 등)은 `safetyreport-mobile` 레포의 CLAUDE.md 참조.

**서버 측 완료 마커 파일 구조:**

### crawl_done.json
- `services.crawl_state_store.save_crawl_done(changed_count)` — 크롤링 완료 시 저장
- `services.crawl_state_store.get_and_clear_crawl_done()` — 읽고 즉시 삭제 (한 번만 읽힘)
- 위치: `data/crawl_done.json`

### crawl_done_ext.json (크롬 확장 전용)
- `services.crawl_state_store.save_crawl_done_ext(changed_count, changes)` — 크롤링 완료 시 저장, changes에 신고번호/신고명 포함
- `services.crawl_state_store.get_and_clear_crawl_done_ext()` — 읽고 즉시 삭제
- 위치: `data/crawl_done_ext.json`
- **주의**: `crawl_done.json`(모바일용)과 별도 파일 — 크롬 확장이 소비해도 모바일 흐름 무영향
- 저장/회전/브로드캐스트 후처리는 `services/crawl_manager.py` 의 `run_after_crawl()` 내부에서 공통 처리

### crawl_changes.json
- `services.crawl_state_store.save_crawl_changes(engine, changed_item_ids)` — 일반 신고는 `notification_kind=report`, `synced_at` 을 포함한 payload로 저장
- 일반 신고 변경 목록은 저장 전에 `synced_at DESC`, fallback `답변일 DESC`, `신고번호 DESC` 로 재정렬한다.
- 각 report payload 에는 `change_reason ('supplement' | 'report')`, `supplement_open`, `supplement_round_no`, `supplement_round_count` 도 함께 들어간다. `supplement` 는 신고가 보완요청 상태이거나 보완 history 에 열린 round 가 있을 때.
- `services.crawl_state_store.peek_crawl_changes()` — 읽기만 (삭제 안 함) → WS 브로드캐스트용
- 모바일 API 폴링(`/api/v1/crawl/results`)은 이 파일이 아니라 `core/storage/change_log.py`(변경 기록 + 기기별 읽은 위치, R5)를 읽는다. 파일을 읽고 지우던 `get_and_clear_crawl_changes()` 는 R6 에서 삭제.
  기록은 60일·5천 건까지, 180일 넘게 안 온 기기의 읽은 위치는 새 묶음을 쌓을 때 지운다.
- 위치: `data/crawl_changes.json`

### 연속 알림 큐 처리 (crawl_manager.py)
- 크롤링 중 추가 enqueue/start 요청 → `_pending_queue`에 적재
- 크롤링 완료 후 `pop_pending()` → 큐에 쌓인 신고번호 전부 묶어 자동 재실행
- `append_to_pending(report_number)`, `pop_pending()`, `pending_count()` 메서드 제공

---


<!-- legacy CLAUDE.md 536-570 -->
## 모바일 API 엔드포인트 (/api/v1)

인증: `X-API-Key` 헤더, `auth_middleware` 우회 (`_PUBLIC_PREFIXES`에 `/api/v1/` 등록).

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET | `/summary` | 대시보드 요약 |
| GET | `/reports/traffic` | 교통위반 신고 목록 |
| GET | `/reports/parking` | 주정차위반 신고 목록 |
| GET | `/reports/other` | 기타위반 신고 목록 |
| GET | `/stats` | 기관별/담당자별 통계 |
| GET/POST | `/watchlist` | 감시 목록 조회/수정 |
| POST | `/rating/start` | 모바일 Client 별점 배치 시작 (API 키 인증). 본문 `{report_numbers, score, cause?}` — `cause` 는 공통 사유(선택, 2026-09-25, 코드포인트 1000 초과면 400). 결과는 `logs/current_rating.log` 줄 형식이 계약(모바일이 정규식으로 읽음) |
| POST | `/crawl/enqueue` | 신고번호 큐 등록 (알림 리스너 연동) |
| GET | `/crawl/status` | 크롤링 실행 여부. 추가 필드(하위호환, 2026-09-26 감사 R7-03): `pending`(아직 맡지 않은 대기 번호 수), `unresolved`(대기 큐에서 처리하지 못하고 뺀 번호 최근 50개 — `{number, reason: not_found\|ambiguous, at}`) |
| GET | `/crawl/done` | 완료 마커 조회 (읽으면 삭제) |
| GET | `/crawl/results` | 변경 신고 목록 조회 (읽으면 삭제) |
| GET | `/crawl/config` | crawl_type(늘 api), crawl_mode(늘 full), max_empty_pages — 구앱 호환 필드 |
| POST | `/crawl/start` | 모바일에서 크롤링 시작 |
| POST | `/crawl/kill` | 크롤링 강제 중지 |
| POST | `/crawl/resume` | (2026-09-25 비회원 제거) 410 Gone — 구앱 호환으로만 남음 |

- `/summary` 의 취하 필드 규칙
  - `exclude_withdraw=True` 이면 그래프/모바일 카드 기준 `withdrawCount=0`, `withdraw_pct=0`
  - 실제 원본 취하 건수는 `withdrawRawCount` 로 별도 전달
| GET | `/app/config` | 앱 설정 (`exclude_withdraw`, `use_representative_records` 등). `normalize_police`는 구버전 앱 호환용 고정 `false`(registry는 항상 적용, 값이 동작을 제어하지 않음). `capabilities`(기능 목록: `rating_cause`, `community_account`)·`rating_cause_max` — 앱이 서버 기능을 알아본다(2026-09-25) |
| GET | `/community-auth/status` | 서버의 커뮤니티 계정 상태 DTO(`{"data": …}`). 모든 키 가능, 관리 권한 없는 키는 `can_manage=false`·연결 링크 없음 (2026-09-25) |
| POST | `/community-auth/start` · `/confirm` · `/cancel` | 커뮤니티 계정 연결 관리(`/disconnect` 는 2026-09-27 삭제). `[COMMUNITY] api_key_managers` 에 허용된 키만(아니면 403 `permission_required`). 본문·오류 코드는 `community-account.md` |
| POST | `/settings` | 필터 설정 저장 (`exclude_withdraw`, `use_representative_records`; 옛 `normalize_police` 요청은 오류 없이 무시) |
| GET | `/files?path=` | 서버 파일 브라우저 (logs/results 한정) |
| GET | `/files/download?path=&api_key=` | 파일 다운로드 (헤더 또는 쿼리 파라미터 인증) |
| GET | `/server/version` | 서버 버전 + GitHub 최신 버전 (모바일·크롬 확장 공통) |
| GET | `/crawl/done/ext` | 크롤링 완료 마커 조회 (크롬 확장 전용, 확인 후 자동 삭제) |
| GET | `/vehicle/{vehicle_number}` | 차량번호 부분 일치 검색 (전체 카테고리, 크롬 확장용) |

---


<!-- legacy CLAUDE.md 710-722 -->
## 크롬 확장 연동 (`web/routers/api_route.py`, `services/report_query_service.py`)

크롬 확장(`safetyreport-chromeextension`)에서 차량번호 검색 지원.

```
GET /api/v1/vehicle/{vehicle_number}
```
- 인증: `X-API-Key` 헤더
- 동작: 교통/주정차/기타 전체 merge 테이블에서 차량번호 부분 일치 검색, 신고번호 역순 정렬
- `search_by_vehicle(engine, vehicle_number)` — 현재 구현은 `report_query_service.py`, `data_service.py`는 호환 재노출만 담당

---

## 2026-10-04 기술일지 반영 (계약 변화 없음, 동작 정정)
- `POST /api/v1/editor/{category}/{record_id}`: 필드 값은 문자열·숫자·null 만 받는다. 객체·배열·불리언이면 400(예전에는 SQLite 바인딩 오류로 500, A1-10).
- `crawl_changes.json`(WS `crawl_changes`·크롬 확장 완료 목록용 "마지막 크롤링 1회분")은 완료 처리에서 한 번 읽고 비운다. 다음 실행이 후처리 전에 실패해도
  이전 목록을 다시 보내지 않는다(A2-05). 모바일 `/api/v1/crawl/results` 의 기기별 변경 기록(change_log)은 영향 없음.
- 모바일 `/api/v1/stats`(기관 통계)는 웹 통계 화면과 같은 파생값 준비(`_prepare_metrics`)를 거친다. 응답 필드·값은 같다(B-08).
- 모바일 DB 복원의 `entry_value` 반영은 묶음 삭제·삽입으로 바꿨다. 의미(앱 값이 원천, NULL 은 행 없음, 빈 문자열 유지)는 같다(B-09).
- `POST /devices/api-keys/delete`: 키를 지운 뒤 그 키로 열린 `/ws/events` 연결을 4001 로 닫는다(A1-02).


## 공식 계정 바인딩과 개인 DB 교환
안전신문고 ID 원문·해시는 개인 DB(`mysafety_sync_meta`/모바일 `sync_meta` 포함)에 기록하지 않는다.
1:1은 config의 ID에서 계산한 `sha256("safetyreport-dataset|v1|" + trim(lower(ID)))`와
서버 `status.official_account.dataset_key` 대조·연결 등록 거절로 보장한다.
복원/업로드의 주인 검사는 `account_data.refuse_foreign_owner`의 카카오 검사만 적용한다.
같은 카카오면 다른 안신 계정 시절 백업 또는 안신 정보가 없는 백업도 허용한다.
카카오 주인 누락·불일치·현재 로그인 미확인 거절과 스키마/무결성 검사는 그대로다.
개발 중 남은 임의 안신 메타는 판정에 쓰지 않고 새로 기록하지 않는다. 임의 메타를 조용히 버리지 않는
기존 교환 규칙은 유지하며 삭제 마이그레이션·스키마 버전/열 변경은 없다.

### 코드 대조 정정 — 공식 계정
| 종전 설명 | 현재 코드 |
|---|---|
| 안신 키 최초 기록·변경 후 갱신 | 개인 DB 기록 제거. 새 백업 사본에도 안신 키를 보충하지 않음 |
| 안신 키 누락/불일치 백업 거절 | 카카오 주인만 같으면 허용. 모바일에 안신 메타 기록 요구 없음 |


## 코드 대조 정정: 모바일 답변 알림의 멱등 접수

`POST /api/v1/crawl/notification`은 기존 `/crawl/enqueue`를 유지하면서 추가한 경로다. 기존 API 키 인증·프로토콜·커뮤니티 게이트·초기화 차단을 적용한다. JSON은 `report_number`(SPP-YYMM-6~8자리)와 `request_id`(`inbox.enqueue.native.notification.<UUID>`)이며, 정상 접수는 `{status: "accepted", request_id: ...}`로 응답한다. 이는 크롤 **완료**가 아니라 영속 대기 접수다.

`crawl_control.enqueue_notification → CrawlManager.append_notification → request_pending_launch → launch_pending_crawl → start.py --queue` 순서다. API 키의 SHA-256과 request ID를 receipt 키로 삼고, receipt와 대기 번호를 같은 파일 교체에서 저장한다. 같은 ID/번호는 재접수하지 않으며 다른 번호로 ID를 재사용하면 400, 저장 실패는 503이다. receipt를 저장하기 전에는 성공 응답하지 않는다. 실제 실행 실패는 기존 서버 대기 큐의 재시도/기동 복구가 맡는다. 접수 뒤 게이트나 복원 상태가 바뀌어 시작 함수가 예외 없이 false를 반환하더라도, 크롤 중이 아니고 대기가 남으면 재시도 타이머를 예약한다.

`crawl_pending_queue.json`은 기존 번호 배열을 계속 읽는다. 멱등 접수를 사용한 뒤에는 `{reports: [...], notification_receipts: {...}, notification_again: [...]}` 형태로 저장한다. 현재 실행이 이미 맡은 신고에 새 이벤트가 도착하면 `notification_again`으로 남겨 이전 실행의 완료가 새 답변 의무를 지우지 못하게 한다. 완료 후에도 receipt를 보존하므로 응답을 잃은 폰이 늦게 재시도하거나 서버가 재기동해도 같은 요청이 새 크롤을 만들지 않는다. 파일 파손·형식 오류는 빈 큐로 간주하지 않고 원본을 보존하며 접수를 거절한다.

운영 주의: 새 모바일은 이 경로가 없는 서버에 요청을 임의로 재전송하지 않는다(404 대기). 서버를 먼저 반영한 뒤 모바일을 반영한다. 새 저장 형식을 사용한 서버를 구버전으로 되돌리면 구버전은 객체 큐를 읽지 못하므로 **대기 파일을 보존한 상태에서 이 코드로 복구해야 한다**. receipt는 중복 방지를 위해 자동 만료시키지 않아 장기적으로 파일 크기가 증가한다. 신고 DB 스키마·교환 컬럼은 변경하지 않는다.


## 과태료 금액 오타 해석

`범칙금_과태료` 원문과 DB 교환 값은 보존한다. 정상 정수/천 단위 쉼표·점 표기는 그대로 읽는다.
깨진 구분자 묶음은 구분자를 제거한 숫자와, 첫 묶음(1~3자리)을 유지하고 뒤 묶음을 3자리로 맞춘 숫자만 후보로 만든다.
짧은 묶음은 끝에 0을 붙이고, 긴 묶음은 3자리 뒤가 모두 0일 때만 그 0을 지운다(다른 숫자는 바꾸지 않는다).
기존 fine_estimate 금액표(기본·2시간 가중)와 위반 조항별 법정 금액표의 합집합에 후보 하나만 일치할 때 확정으로 읽는다.
일치 없음/복수 일치/사용 가능한 규칙·조항 없음은 금액 미확인이며 기존 추정 대상 판정으로 이어진다. 범칙금에는 과태료 규칙을 적용하지 않는다.
PC는 `services/fine_estimate.py`, 모바일은 `lib/services/fine_estimate.dart`가 숫자 판정 정본이다. 통계·업로드는 신고 맥락을 함께 넘긴다.
공유 시험 벡터는 `fine_amount_typo_vectors.json`이며 합성 후보 집합과 실제 법정 집합 모두에서 복수 일치 방어 조건을 검사한다.
파서 버전 pc-parser-7/mobile-parser-7은 기존 규칙·파서 버전 기반 재전송 경로를 다시 실행한다.
재전송은 journal 최신 payload의 추정 필드만 재계산하며, 확정 금액의 파서 보정은 새 상세 관측을 수집할 때 반영한다.
상세 화면의 보정값/원문 표기는 표시용이며 DB에 쓰지 않는다.

### 코드 대조 정정 — 금액 판정 입력과 토큰 경계

| 항목 | 정정된 계약 |
|---|---|
| 숫자 토큰 | `[0-9,.]+` 전체를 통계·업로드·표시가 공유한다. 앞/뒤 쉼표·점은 버리지 않고, 빈 묶음은 미확인으로 처리한다. |
| 판정 입력 | category, entry_value, ssnpc_cd, ssnpc_type, 신고명, 위반법규, 차량번호, 사진_첫촬영, 사진_끝촬영, 발생시각을 함께 넘긴다. 모바일 Report 변환은 fineRecordForReport가 정본이다. |
| 코드 우선순위 | 분류 코드가 있으면 메뉴보다 우선한다. 일반 주정차 메뉴 + EP-03은 fire_lane 500000원, 코드가 없으면 parking 40000원이다. 통계·업로드·목록/상세·추정 금액 내보내기 모두 같다. |
| 규칙 버전 | 양쪽 RULE_VERSION/ruleVersion은 2026-10-13.2. 파서 버전은 pc-parser-7/mobile-parser-7을 유지한다. 공유 벡터와 커뮤니티 계약 벡터·MANIFEST도 함께 변경한다. |
| 재전송 범위 | PC `_backfill_payload`, 모바일 `refreshFineEstimate`는 journal의 `amount`를 보존하고 `fine_estimate`만 재계산한다. 버전 상승만으로 과거 공유 확정 금액을 보정하지 않는다. PC의 종전 “로컬 값으로 payload를 만든다”는 설명을 이 코드에 맞춰 정정한다. 2026-10-16: 추정 판정은 일반 캡처와 같은 `estimate_if_unknown`/`estimateIfUnknown` 을 개인 DB 현재 원문(범칙금_과태료·위반법규 포함)에 적용하되, 그 원문의 지금 금액 판정(confirmed_won·kind·disposition)이 journal 과 같을 때만 — 금액표 보정으로 확정 금액이 새로 읽히면 추정을 싣지 않는다(상세: `community-upload.md` 추정 판정 절). |
| 저장·표시·내보내기 | DB와 내보내기의 확정 원문은 보존한다. 목록/상세만 해석한 보정값을 쓰고, 상세에만 원문 보조를 붙인다. 미확인 금액의 추정은 같은 상세 줄에서 별도로 표시한다. |

추정 과태료 업로드는 아직 미릴리스다. 코드 우선 적용으로 추정액·payload 해시가 바뀌는 범위는 있지만,
이미 배포된 추정값을 다시 보내야 하는 추가 부담은 없다. 기존 버전 기반 재전송 경로와 해시 멱등성은 유지한다.

## 이륜 번호판 정규화와 추정 규칙 버전

| 규칙 버전 | 의미 |
|---|---|
| `2026-10-12.1` | 금액 오타 정리 규칙. 지역+시군구 이륜 번호판은 정규화되지 않아 승용등으로 추정하는 회귀가 있었다. |
| `2026-10-13.1` | 지역 필수 이륜형을 인식한다. `경기도군포시다2671` → `경기군포다2671`, 차종 `이륜`, 금액군 `이륜등`. 주정차는 추정 없음, 일반도로 버스전용차로는 40,000원. |

지역은 기존 사전의 긴 이름을 먼저 적용한다. 지역 뒤 시군구는 한글 1~3자이며 별도 `시/군/구` 접미 한 글자를 제거한다.
한 글자 지명 자체는 제거하지 않는다. 숫자 앞 마지막 한글은 용도 글자로 고정해 지명 분할을 추측하지 않는다. 접미 제거 뒤에도 두 글자 이상 지명이 `시/군/구`로 끝나
재정규화 결과가 달라지는 복수 접미 해석은 모호하므로 제외한다. 약칭과 지명 첫 글자가 다시 지역 정식명으로 읽히는 경우도 제외한다
(예: 지명 `도봉`의 `경기도도봉다1234`는 줄인 정규형 `경기도봉다1234`가 다시 긴 지역명 `경기도`+지명 `봉`으로 읽혀 정규형이 안정되지 않으므로 제외한다.
반면 원문 `경기도봉다1234` 자체는 긴 지역명을 먼저 적용해 `경기도`+`봉` → `경기봉다1234`로 인식한다. 코드: PC `services/plate_normalize.py`, 공유 벡터 `plate_normalize_vectors.json`).
지명 사전으로 오타를 교정하지 않으며, 지역 없는 `여수사6361`·`천안차2711`은 제외한다.
경계는 지역 앞과 숫자4 뒤에서 검사한다. 서로 다른 후보가 두 개 이상이면 미인식이다.
원문 차량번호·확정 금액은 그대로 보존하며 정규화된 값은 차종 판정과 중복차량 묶기에 사용한다.

규칙 버전이 달라지면 기존 재전송 완료 표식이 만료된다. 최신 journal payload의 추정만 재계산해
주정차의 잘못된 추정을 제거하고 버스전용차로 50,000원을 40,000원으로 교정한다.
다른 payload 필드는 유지한다. 재전송 완료 뒤 동일 버전으로 다시 실행하면 중복 이벤트를 만들지 않는다.
지도 수집기는 `rule_version`을 길이 제한 문자열로 받으므로 새 버전 허용 목록 변경은 필요하지 않다.
기존 중앙의 오계산은 각 PC·모바일이 업데이트 후 재전송해야 고쳐진다. SQL은 앱 추정을 임의로 다시 계산하지 않는다.


### 위반 조항별 법정 금액표

- 원문: [도로교통법 시행령, 시행 2026-10-02, 대통령령 제36728호](https://www.law.go.kr/LSW/lsInfoP.do?lsiSeq=290387&efYd=20261002).
  [별표 6](https://www.law.go.kr/LSW/flDownload.do?flSeq=169705915&bylClsCd=110201)(별표 개정일 2025-03-18),
  [별표 7](https://www.law.go.kr/LSW/flDownload.do?flSeq=169705941&bylClsCd=110201)(별표 개정일 2020-12-01).
  시행령 시행일과 별표 개정일은 서로 다르다. 작성일 2026-10-13. 원문 PDF SHA-256도 JSON metadata에 저장한다.
- PC `services/data/traffic_fine_table.json`, 모바일 `assets/data/traffic_fine_table.json`은 바이트 동일하다.
  44개 조, 세부 항·호와 상위 합집합 포함 110개 조회 키. 각 키의 `amounts`는 차종별 원 단위 집합,
  `references`는 `별표번호:행번호`, `includes`는 상위 조·항에 합산한 확인된 하위 조항이다.
  원문이 차종별로 나누지 않은 정액·기간 가산액은 `차종무관`에 두고 행위자/적용 범위를 `notes`에 기록한다.
  제50조제4항은 `개인형이동장치`로 별도 구분하여 승용·승합·이륜 금액에 섞이지 않게 한다.
  `제160조제3항`은 부과 근거이지 위반행위 조항이므로 조회 키가 아니다. 감경·체납 가산금·별표 8/10 범칙금은 제외한다.
- `제14조5항`/`제14조제5항`, 공백, 법률명 생략을 정규화한다. 조 및 호의 `의2`는 보존한다.
  다른 법률, 복수 조항, 미확인 하위 조항을 상위 조항으로 추측해 연결하지 않는다.
  항 생략 시에는 표에 명시한 상위 집합만 조회한다(예: 제25조의2는 확인된 제1항·제2항의 합집합).
- `legal_fine_amounts`/`legalFineAmounts`는 기존 `_RULES`의 기본·2시간 가중 금액을 유지하고 해당 위반 조항의
  차종별 일반·보호구역·속도 구간·2시간 가중 금액을 합친다. 번호판 미상은 그 조항 전 차종,
  화물의 4톤 경계·긴급차 원차종 미상은 승용등+승합등을 사용한다. 추정 함수의 기존 차종 선택은 바꾸지 않는다.
  표로 보호구역 여부나 실제 부과액을 계산하지 않으며, 오직 깨진 숫자의 두 후보 대조에만 쓴다.
- 정상 형식 금액은 표와 달라도 그대로 읽는다. 깨진 숫자는 후보 중 정확히 하나만 집합에 포함될 때 읽는다.
  0개 또는 2개 일치면 원문/금액 미확인을 유지한다. 제23조의 `40,0000` → `40,000`.
  실제 법정 집합에도 모호성이 있다: 별표 6 제10호의3(법 제50조의3제6항)은 10만원부터 500만원까지
  10만원 단위 기간 가산액이므로 `100,0000`의 10만원/100만원 후보가 모두 맞아 보정하지 않는다.
- `_RULES`, `classify`, `estimate`의 대상 유형·추정 금액은 그대로다. 다만 보정 성공으로 금액 미확인 여부가 바뀐다.
  제23조는 추정 규칙 없이 확정 4만원이 되고, 일반 주정차+제32조 `120,0000`은 기존 추정 4만원 대신 확정 12만원이 된다.
  따라서 양쪽 규칙 버전 `2026-10-13.2`로 기존 재전송 경로를 재실행한다. 파서 버전은 pc-parser-7/mobile-parser-7 유지.
  커뮤니티 observations 벡터의 규칙 버전·canonical JSON·해시·MANIFEST는 PC/모바일 사본만 변경한다.
  지도 저장소 정본 반영은 통합 담당자가 수행해야 한다.
  **재전송 한계:** 기존 backfill은 journal의 `amount`를 보존하므로 과거 공유된 금액 미확인을 보정하지 않는다.
  화면·로컬 통계와 새 상세 관측의 payload에는 즉시 적용되고, 과거 공유 금액은 새 상세 관측 때 반영된다.
- Python은 모듈 기준 경로로 JSON을 읽고 PyInstaller 데이터 옵션에 포함한다.
  모바일은 `tool/generate_traffic_fine_table.py`가 같은 JSON 전체를 `traffic_fine_table.g.dart`에 그대로 넣는다.
  동기 함수·백그라운드 isolate에서 초기화 순서 없이 사용하며 Flutter asset 비동기 로딩은 필요 없다.
  `--check`와 시험이 원본 JSON/생성 문자열/벡터의 SHA-256 일치를 검사한다.
- PC `web/routers/filters.py` → `fine_corrected_text`에는 위반법규가 이미 전달된다.
  Client 페이지 API는 위반법규를 포함한 원문 필드를 반환하고 `ApiService.getReportsPage` → `Report.fromJson` → `law` →
  `fineRecordForReport`로 전달한다. 목록 `ReportCardMetaItem.fine` → `fineTextForReport`, 상세 `_fineField` →
  `correctedFineText`를 사용한다. 화면 보정값·상세 원문 보조 표기만 달라지며 DB/내보내기 원문은 보존한다.

## 모바일 Client 검색 계약 v1

`GET /api/v1/app/config`의 `data.capabilities`에 `report_search_v1`,
`data.report_search_version`에 정수 `1`이 **모두** 있을 때 사용한다. 제품 버전·protocol 3·DB schema 6은 변경하지 않는다.

`GET /api/v1/reports/{category}/search` (기존 API 키 인증 유지):

| 파라미터 | 의미 |
|---|---|
| `category` | traffic / parking / safety / living / other / all(5분류 통합) |
| `search_version` | 필수 정수 1, 다른 버전은 400 |
| `filter` | JSON 객체(최대 32768자), 생략하면 `{}`; 모르는 키·잘못된 타입은 400 |
| `offset`, `limit` | 결과 기준 offset ≥ 0, limit 1~200(기본 200) |
| `dedupe` | canonical(기본) / raw; 그 밖의 값은 400 |

필터 키는 모바일 `ReportFilter.toSearchJson()` 및 양쪽 `contracts/client-search-vectors.json`이 정본이다.
문자열 키: `name`, `reportNumber`, `id`, `ratingCause`, `agency`, `manager`, `carNumber`,
`law`, `location`, `fine`, `supplementCount`, `reportContent`, `processContent`, `pollStatus`,
`reportDateStart/End`, `occurDateStart/End`, `responseDateStart/End`, `occurTimeStart/End`.
문자열 배열: `ratings`, `statuses`. bool: `excludePolice`, `onlyPolice`.
빈 문자열·배열/false는 해당 조건 없음. 텍스트는 쉼표 OR/`&` AND, 양끝 공백 제거,
Dart와 같은 Unicode 소문자 부분 일치이며 `%`·`_`는 리터럴이다(casefold·SQL LIKE 아님).
법규는 아래 "복수 법규 TEXT 저장·검색 계약"의 개별 키 검색(쉼표 OR·`&` AND), `lawExact: true`(bool, 2026-10-16 추가)면 저장 키 소속 판정, `__없음__`은 NULL/빈 법규. 별점 NULL/0 이하의 토큰은 `__none__`. 보완횟수가 숫자로 읽히지 않으면 0(모바일 `_toIntOrNull ?? 0`과 같음, 예전에는 500).
상태는 canonical 표시 상태(처리상태 NULL일 때 상태로 fallback), 보완횟수 NULL은 0,
만족도 NULL은 `답변 대기`. 날짜·시각은 실제 유효 앞자리와 양끝 포함 범위이며 기존 모바일 판정을 보존한다.
기관·경찰 조건은 현행 registry 표시명을 사용한다.

응답은 기존 페이지 필드(`category,total,offset,limit,count,next_offset,dedupe_mode,data`)에
`status:success`, `search_version:1`, 수신한 `filter` 객체, 서비스 처리 시간 `server_ms`를 더한다.
`data`는 본문·처리내용·첨부·보완·편집용 필드를 생략하지 않은 full report이다.
통합 목록에서는 각 행의 실제 category를 보존한다. 모바일은 버전·조건 echo·분류·offset·limit·dedupe를
확인하며, 단순 HTTP 200만으로 검색이 적용됐다고 판단하지 않는다.

`services/client_report_search.py`가 필요한 열만 받는 SQLite UDF로 **정확한 판정 후** COUNT와
LIMIT/OFFSET을 적용한다. 기존 stats `sql_candidates`/목록 `_build_records_query`의 필터 의미를 대신 쓰지 않는다.
신고번호 DESC, ID DESC 및 기존 canonical 대표건 투영·취하 제외 설정을 보존한다.
COUNT·페이지·페이지의 중복 메타는 하나의 명시 읽기 트랜잭션을 공유한다.
COUNT/검색결과 서버 캐시는 없으므로 DB 변경·dedupe·설정·registry에 대한 캐시 무효화 누락이 없다.
기관 해석 캐시는 요청 안에서 최대 512개이고, full report 객체는 현재 페이지에 대해서만 만든다.
정확한 부분 문자열 검색은 DB 스캔을 할 수 있다. 번호/ID, 분류/번호/ID 복합 인덱스로
동일 신고번호의 ID 보조 정렬에 필요한 임시 정렬을 피한다. 인덱스만 추가하며 교환 DB 열은 바꾸지 않는다.

모바일은 조건 검색과 전체 분류 조회를 이 경로로 읽고 현재·이전·다음 최대 3페이지 캐시,
다음 한 페이지 prefetch를 유지한다. 조건이 없는 단일 분류는 기존 `/page`도 동일한 제한 조회다.
capability를 성공적으로 조회했지만 신호가 없으면 기존 취소 가능한 200건 순회로 정확한 total을 구한다.
401/timeout/5xx 및 새 경로의 404/응답 계약 오류는 오류로 전달하며 구서버 fallback 사유가 아니다.
페이지 API 자체가 없는 서버의 기존 업데이트 필요 오류도 유지한다.
임의 predicate를 사용하는 `FilteredListScreen`의 대시보드 드릴다운과 rating scope는
명시적으로 기존 정확 순회에 남는다(부분 페이지 total로 속이지 않음).
배포 순서는 PC 검색 계약 제공 → capability를 확인하는 모바일이며, 기존 모바일 `/page`와 `/reports`는 유지한다.


## 명시 과태료 금액 문법 확대 (2026-10-14)

답변에서 과태료에 직접 연결된 정수 원·쉼표/점 묶음·정수 만원, 선택적 콜론·소괄호와 전각 숫자/구분자를 읽는다. Python `services/fine_amount_syntax.py`, Dart `lib/services/fine_amount_syntax.dart`는 같은 `contracts/explicit-fine-vectors.json`을 검증한다. 만원은 원 숫자로 환산해 기존 `범칙금_과태료`의 `과태료: N원` 형식으로 전달한다. 기존 공통 금액 파서의 손상 숫자 후보 보정·차종/법정 금액 대조는 그대로다.

상한·조건부 금액, 서로 다른 금액(감경 전후 포함)은 명시액 채택을 보류한다. 여러 값 중 첫 값이나 합계를 쓰지 않는다. 정수 만원만 추가하며 소수 만원·혼합 한글 단위·역방향 금액 문장은 미지원이다. 실제 데이터의 조건부 역방향 금액과 포상금은 채택하지 않는다. 범칙금/벌점 우선순위는 유지한다. 내부 None과 숫자0은 구분하지만 기존 외부 계약(숫자 읽기 실패도0,0은 추정 대상)은 변경하지 않는다.

처분을 결정하는 메뉴·수용 정책, 법규 인식, 최소 추정의 유형/금액/RULE_VERSION은 변경하지 않는다. 확정액을 읽으면 기존 통계·업로드 경로에서 추정 합계에 포함하지 않는다. 파서 버전만 pc-parser-8/mobile-parser-8로 올린다. 기존 버전 기반 재전송은 journal의 amount를 보존하고 fine_estimate만 재계산하므로 과거 답변을 자동 재파싱하거나 금액을 수정하지 않는다. 새 상세 수집에 적용되며 과거 값 보정은 별도 승인한 재파싱이 필요하다.

2026-10-16 보완: 상한·조건 문맥은 금액이 속한 절에만 적용한다. 절은 문장 끝(줄바꿈, `。`, `다.`/`요.` 등)과 금액 뒤 연결어미(`며`, `으나`, `지만`, `는데`, `였고/했고/하고/되고` 뒤 쉼표·공백)로 나눈다. 따라서 같은 문장의 다른 절(`부과할 예정이며, … 자진납부하는 경우 20% 감경`, `부과하였으며 이에 불응할 경우 가산금`)이나 다른 줄의 일반 안내(`※ 위반 시 20만원 이하의 과태료`)는 앞의 명시액을 거절하지 않는다. 금액 앞의 조건(`기한 내 납부하지 않을 경우, 과태료 50만원`)과 미래 조건(`다시 적발될 시`, `적발되면`, `~할 시`, `~하면`)은 그 절의 금액을 보류한다. 다른 금액은 그 금액의 절이 상한·조건 안내이거나 조건 때문에 건너뛴 과태료 금액일 때만 무시하고, 감경 후 금액처럼 다른 확정 금액이 보이면 계속 보류한다. 공백은 Python·Dart 공통 명시 집합(U+001C-U+001F, U+0085, U+FEFF 포함)을 쓰고, 숫자 15자리를 넘는 토큰은 금액으로 읽지 않는다(Python `int()` 자릿수 제한 예외 방지, 양쪽 None). 제공 백업 15,804행에서 명시 금액 변경 0행, Python/Dart 차이 0행.

2026-10-17 보완(독립 검수 반영): 10-16 절 범위 규칙은 그대로 두고 두 경우만 좁게 더했다. (1) 금액 절에 `예정`이 있고 `으나`·`지만`·`는데`로 끝나며, 바로 다음 문장(종결 마침표·`。`·쉼표로 끝나지 않는 줄의 줄바꿈까지)에 미부과 결정(`부과하지`, `부과할 수 없`, `부과가 불가·어렵·곤란`, `부과를 취소`; `불가피` 제외)이 있으면 그 금액을 건너뛴다(`부과할 예정이었으나, … 부과하지 않습니다`; 쉼표 없이 줄을 바꿔도 다음 문장으로 본다). (2) 다른 금액 바로 뒤가 `(으)로 감경·경감·감면`이면(닫는 괄호, `20%` 같은 감경률이 사이에 있어도) 조건 절이나 건너뛴 금액이어도 보류한다(`자진 납부하는 경우 (과태료) 80,000원으로 감경`). 문장 전체에서 미부과·감경·상한을 넓게 찾는 일반 규칙은 2~6차 검수에서 다른 부과금·사유 명사구·납부기한 등 회귀를 계속 만들어 쓰지 않았다. 그 경계 입력들은 계약 벡터로 고정해 기준 동작을 지킨다. 남은 한계: 두 규칙은 확정 금액을 보류하는 쪽으로만 작동하므로, 같은 문장의 다른 부과금 미부과(`예정대로 부과하였으나, 가산금은 부과하지 않습니다`)나 적용되지 않는 감경액(`… 80,000원으로 감경되지 않습니다`) 같은 합성 문장에서는 확정 금액 대신 추정으로 남을 수 있다(7차 검수). 틀린 확정 금액보다 덜 해로운 방향(3-2)이라 받아들였다. 제공 백업 15,804행에서 10-16 결과 대비 명시 금액 변경 0행, Python/Dart 차이 0행.


## 미확인 처분의 사건별 과태료 보완 (2026-10-14)

기존 처분/금액 파서와 최종 상태 판정, 취하 처분 제거가 끝난 뒤 `supplement_case_fine` / `supplementCaseFine`을 호출한다. 완료 상태(수용·일부수용·불수용·기타·답변완료)의 빈 처분 또는 미확인에만 적용한다. 명시액(0원 포함)·기존 과태료·범칙금·벌점·경고와 취하/이송/진행 상태는 유지한다.

답변의 문장·항목 경계를 유지하고, 실제 관측한 위반 확인→부과 결정, 완결된 부과 예정 문장, 사전통지서 발송, 구조화된 `처리결과: 수용(과태료 부과)`를 인식한다. 해당 사건의 부과 불가와 충돌하면 보완하지 않는다. 불수용 상태라도 본문에 해당 사건의 부과가 명확하면 처분을 과태료로 보완하되 원본 상태는 바꾸지 않는다. 구조화된 수용 결과가 명시된 경우에만 표시 처리상태도 수용으로 보완한다. 기존 메뉴별 수용 정책과 최소 추정 유형/금액/RULE_VERSION은 유지한다. 법규 저장·복수 조문 선택·조/호 오타 보정 및 기신고 전용 분기는 추가하지 않는다.

정본 구현은 Python `services/case_fine_supplement.py`와 Dart `lib/services/case_fine_supplement.dart`. 인식용 사본에만 기본 HTML entity/숫자 entity, 전각 ASCII, 공백 정규화를 적용하며 원문은 보존한다. `contracts/case-fine-supplement-vectors.json`은 실제 관측 답변 발췌에서 식별정보를 제거한 공통 회귀 입력이다. 원본 백업·개인 식별정보는 계약 벡터에 포함하지 않는다.

파서 버전은 pc-parser-9/mobile-parser-9. 기존 버전 기반 재전송은 journal의 원래 금액 판정을 보존하며 추정 필드만 재계산한다. 이번 변경에 과거 답변 자동 재파싱/DB 보정은 포함하지 않는다. 새 상세 수집에 적용되며 기존 데이터의 일괄 보정은 별도 작업이다.

2026-10-16 보완: `재차`·`경우` 외에 미래 조건(`다시 적발`, `적발 시`, `적발될 시`, `적발되면`, `위반 시`, `위반하면`, `~될 시`, `~할 시`, `~되면`, `~하면`, `~않으면`; 공백 제거 사본 기준)이 있는 문장은 보완 근거로 쓰지 않는다. 본문이 경고·계도 조치(`경고 조치하였`, `경고장 발부`, `계도 조치하였`, `교통질서 안내장 발송` 등)를 이미 했다고 말하면 다른 문장에 부과 표현이 있어도 과태료로 보완하지 않는다. 문장 경계의 공백·목록 번호는 명시 공백 집합과 `[0-9]`로 Python·Dart를 같게 했다. 제공 백업 15,804행에서 보완 결과 변경 0행, Python/Dart 차이 0행.

2026-10-17 보완(독립 검수 회귀 수정): 경고·계도 조치 표현 바로 뒤가 `하지`·`되지`이면(`경고장을 발부하지 않았습니다`) 이미 한 조치로 보지 않는다. 제공 백업 15,804행에서 10-16 결과 대비 보완 결과 변경 0행, Python/Dart 차이 0행.


## 법규 인식 보완 (2026-10-15)

기존 법규 정규식 결과를 먼저 얻고 공통 법규 인식·사건 근거 판정으로 보완한다. 원문과 인용 위치를 유지하며 법률·시행령·시행규칙을 구분한다. 위반 확인은 과태료 처분을 필수 조건으로 삼지 않는다. 일반 안내·적용 배제·신고포상금·경고 지침을 사건의 위반 근거와 구분한다. 제2조를 제2호로 추측 보정하지 않는다.

기존 인식값의 의미가 같으면 기존 표기를 유지한다. 파서 버전은 pc-parser-11/mobile-parser-11이며 기존 DB나 journal을 자동 덮어쓰지 않는다. 과거 답변에 개선을 적용하려면 명시적인 재수집/재파싱이 필요하고 사용자 수정값의 우선순위는 유지한다. 금액·최소 추정 정책 변경은 없다.

공통 계약은 `contracts/law-recognition.json`과 `contracts/law-parser-vectors.json`이다. 후자는 식별정보 없는 관측 문법 발췌와 경계 조합이며 실데이터 파일을 포함하지 않는다.

2026-10-16 보완(인식용 사본 규칙, `law-recognition.json`의 `normalization_policy`):
- 숫자와 숫자 사이의 공백 묶음은 공백 하나로 남기고 나머지 공백·가운뎃점은 지운다. 조문 문법은 이 공백을 항·호 앞에서만 허용하므로 `제32조의2 1항`은 `제32조의2제1항`, `제17조의2 3항`은 `제17조의2제3항`이 된다(이전에는 `제32조의21`처럼 가지번호와 항 번호가 합쳐졌다). 원문에 공백이 없는 `제32조의21항`은 가지번호와 항의 경계를 정할 수 없어 기존 해석(`제32조의21`)을 유지한다. 제공 백업에는 이런 표기가 0행이다.
- 숫자는 `[0-9]`만 인식한다(전각 숫자는 NFKC로 ASCII가 된 뒤 인식, 아랍 숫자 등은 인식하지 않음). 같은 조 상속 항(`같은 조 제３항에`)도 ASCII 숫자만 쓴다.
- 공백 집합은 Python·Dart `\s`의 합집합(U+001C-U+001F, U+0085, U+FEFF 포함)을 명시한다. 기존 정규식(`_legacy_violation_law`/`legacyViolationLaw`)은 Dart(JS) `\s` 집합과 ASCII 숫자를 Python에서도 명시해 같은 결과를 낸다.
- HTML entity는 세미콜론으로 끝나는 토큰만 푼다(`&nbsp`는 문자 그대로). 이름 참조는 WHATWG 표와 최장 접두 규칙, 숫자 참조는 HTML5 대체표(0→U+FFFD, 0x80-0x9F 표)를 쓰며 서로게이트·U+10FFFF 초과는 U+FFFD, 그 밖의 제어·비문자 코드포인트는 유지한다(Python `html.unescape`처럼 지우지 않음). 숫자 참조는 자릿수를 제한해 직접 풀어 Dart html 파서가 큰 숫자에서 예외를 던지던 경로를 막았다.
- 기존 정규식(`_legacy_violation_law`/`legacyViolationLaw`)은 공백을 지울 때 가지번호와 항 번호가 맞닿으면(`의2` + `1항`) 사이에 `제`를 넣어 `도로교통법 제32조의2제1항`으로 저장한다. 두 숫자가 맞닿지 않는 `제13조 3항`→`제13조3항`과 원문에 공백이 없는 `제32조의21항`(기존 해석 `제32조의21`)은 기존 표기를 유지한다. `law_values`의 정규화는 Python `fullmatch`와 Dart 앵커 전체 일치로 같은 결과를 내며, 합성 차등 시험 14,000건에서 Python/Dart 차이 0건이다.
- 파서 버전은 pc-parser-12/mobile-parser-12다. 버전이 바뀌면 앱 시작 뒤 한 번 기존 공유 신고의 journal 최신 payload에서 `fine_estimate`만 다시 계산하는 보충이 돈다(답변 재파싱·저장값 변경 없음). 이번 변경은 저장값과 추정 판정 입력을 바꾸지 않으므로 제공 백업 15,804행 기준 새 payload 차이 0건, 즉 새 전송 이벤트가 없어야 한다. 후보 전체를 한 번 훑는 비용만 있다.


## 복수 법규 TEXT 저장·검색 계약

최종 파서는 기존 정규식 인식에 B 보완을 적용한다. 사건 근거로 채택된 조문 중 가장 높은 근거 순위의 조문들을 중복 제거하여 기존 `위반법규` TEXT에 LF로 연결한다. 대표 조문을 첫 줄에 놓고 나머지는 원문 순서로 둔다. 위반 확인 근거가 있으면 일반 처분·시정 근거를 함께 저장하지 않는다. 같은 인용 묶음에 있는 기존 파서의 조문도 보존하되, 명시 배제·조건부·일반 안내로 판정한 조문은 제외한다. 법령명 없는 조 번호를 임의로 연결하거나 제2조를 제2호로 바꾸지 않는다.

예: `도로교통법 제32조\n도로교통법 제33조\n도로교통법 제34조`. 테이블·열·자료형·스키마 버전은 변경하지 않는다. 단일 문자열은 계속 읽으며, 의미가 같은 단일 인식값의 기존 표기는 유지한다. 읽기는 CRLF/LF와 Unicode 줄 구분자를 허용하고 새 파서의 저장은 LF로 통일한다. 사용자 수정값은 기존 우선순위대로 적용한다.

`law_values.py` / `law_values.dart` / `law-values.js`가 개별 법규를 분리·정규화한다. 웹 상세 링크·통계 선택 목록·모바일 상세/선택 목록은 조합 전체가 아닌 개별 조문을 제공한다. API는 기존 문자열 필드에 LF를 JSON escape하여 전달한다. 쉼표는 OR, `&`는 AND이며 신고 행을 조문 수만큼 늘리지 않는다. 상위 조·항 검색은 같은 조문의 하위 항·호·목을 포함한다. 제17조와 제170조 또는 제17조의2는 구분하고 법률·시행령·시행규칙도 구분한다. 기존 클라이언트가 LF 조합 전체를 보낸 경우 각 줄의 AND로 읽는다.

서버는 SQLite 결정적 검색 함수를 WHERE에 적용하고 COUNT/LIMIT 전에 필터한다. 모바일은 같은 읽기 트랜잭션에서 DISTINCT 저장값을 읽어 개별 조문으로 판정한 뒤 해당 원본 문자열들의 SQL IN 조건을 COUNT/LIMIT 전에 적용한다. FFI의 선두 BOM 제거를 피하려고 DISTINCT 값을 BLOB으로 읽고 원문 바이트를 복원한다. IN 값은 UTF-8 hex TEXT literal로 만들어 따옴표·호스트 바인드 개수 제한을 피한다. 새 인덱스·FTS·관계 테이블은 없다. 두 방식 모두 전체 스캔 비용이 남으므로 대규모 DB 성능은 별도로 측정해야 한다. 캐시는 문자열 판정만 저장하며 신고 행·건수는 캐시하지 않는다.

법규별 통계에는 동일 신고가 복수 법규에 소속될 수 있다. 법규별 건수를 더하면 전체 고유 신고 수와 다를 수 있으며 화면에 이를 표시한다. 전체 건수·금액은 고유 신고 기준이고, 같은 상위 조문에 여러 하위 조문이 걸려도 그 조문에서는 한 번만 센다. 기존 금액 오타 보정·최소 추정 분류는 대표 조문인 첫 줄만 사용한다. 추가 조문으로 금액을 올리거나 후보 금액을 합산하지 않는다.

### 통계 막대·드릴다운 불변식과 법규 검색 capability (2026-10-16)

- **저장 키 소속 판정**(`matches_law_keys` / `SrLawValues.matchesKeys` / `matchesLawKeys`): 질의는 저장 키 그대로(한 줄에 하나, 여러 줄은 AND).
  행의 키 중 하나가 그 키이거나 그 키의 하위 항·호·목이면 속한다(`ancestor_laws`). 쉼표·`&` 분해, 법령명 접두 검색, 법령명 없는 조 번호 검색은 없다.
  `law_memberships`(법규 막대·표 집계)와 같은 규칙이므로 **법규 막대 건수 == 그 막대 링크의 목록 행 수**가 모든 막대에서 성립한다.
- 이 판정을 쓰는 곳: 서버 통계 법규 필터(`_apply_stats_law_filter` → `/stats`, `/api/v1/stats`, `/api/v1/stats/overview`, 지도),
  웹 목록 `lawExact=true`(통계 막대·표·지도 링크, 상세 법규 링크), 모바일 검색 필터 `lawExact`(Client 검색 API·Standalone 목록),
  모바일 Standalone 통계 법규 선택. 통계가 이미 법규 A 로 좁혀져 있으면 막대 B 의 링크는 `A\nB`(줄 = AND)를 넘긴다.
- 검색칸·검색 시트(lawExact 없음)는 예전대로 검색 의미(쉼표 OR, `&` AND, 상위 조·항이 하위 포함, 법령명·조 번호 검색)다.
  질의 전체가 저장 키 하나와 같으면 그 키를 통째로 찾는다(쉼표가 든 사용자 수정값).
- 구조화된 한 키로 완전히 읽히지 않는 값(법령명 부분에 `,`·`&`·다른 `N조`가 들어간 `도로교통법 제32조, 제33조`, `… 제32조 제33조`, `… 및 제33조`)은
  원문 공백을 지킨 한 개의 키다. 조문 키 인식은 Python `fullmatch`, JS `^…$`, Dart 앵커 정규식(백트래킹 전체 일치)으로 같다.
  HTML 문자 참조는 세미콜론이 있어야 풀고(Dart html·브라우저 규칙), 숫자는 ASCII 만 조 번호로 읽는다.
- 공통 벡터: `contracts/law-search-vectors.json` v2 의 `cases`(검색), `canonical_cases`, `key_cases`(소속 판정), `stats_cases`(막대 == 드릴다운). 두 레포 바이트 동일.
- **capability**: `GET /api/v1/app/config` 의 `capabilities` 에 `law_search_v2`, 그리고 `law_search_version: 2` 를 **추가**했다(기존 `report_search_v1`·`report_search_version: 1` 유지).
  뜻: 서버가 법규를 개별 키로 정규화해 검색하고 검색 필터 `lawExact` 를 받는다.
- **구버전 조합**: 앱은 Client 모드에서 이 신호를 확인한 서버에만 정규화한 개별 키와 `lawExact` 를 보낸다. 신호가 없거나 아직 모르는 서버(v3.3.0 이하)에는
  예전처럼 저장 원문 값(앞뒤 공백 제거)을 선택지·상세 링크·검색 값으로 쓰고 `lawExact` 를 보내지 않는다(구서버는 모르는 키를 400 으로 거절).
  구서버의 법규 검색·통계는 저장값 완전 일치이므로 원문 값을 보내면 예전 결과와 같다. 새 서버 + 구 앱: 구 앱은 `lawExact` 를 보내지 않으며 원문 값도 정규화해 찾는다.

CSV/Excel 및 DB 백업·양방향 변환은 원본 LF 문자열과 사용자 수정값을 그대로 전달한다. 웹·모바일 편집기는 여러 줄을 유지한다. 웹 textarea가 CRLF로 제출해도 기존 유효값과 줄바꿈 외에 차이가 없으면 법규 수정값을 새로 만들지 않는다. 커뮤니티 업로드 `violation_law`는 기존 60자 계약에 맞춰 대표 조문만 기존 정제기에 전달한다. 외부 저장 구조·업로드 계약은 바꾸지 않는다.

구버전은 LF 문자열을 보관할 수 있어도 개별 법규 선택·검색·금액 보정을 보장하지 못한다. 서버와 모바일은 함께 갱신해야 한다. 파서 버전은 pc-parser-11/mobile-parser-11이다. 기존 DB 일괄 보정은 수행하지 않는다. 과거 답변에 새 인식을 적용하려면 별도의 명시적 재파싱이 필요하며 사용자 수정값을 덮어쓰면 안 된다.


## 2026-10-18 통계 전체 분류 (추가형)

`GET /api/v1/stats`의 `data.all`을 추가한다. 기존 분류·`available_years`·`traffic_total_fine`·`dedupe_mode`는 유지한다.
`all`은 요청 taxonomy의 모든 분류에 동일 필터·projection·취하 설정을 적용한 행을 다시 집계하며 기존 분류와 같은 표/법규 목록/금액 구조다.
기관·담당자 키별 건수·처분 수·확정액·추정액은 분류별 합과 같다. 평균은 행 원자료의 합·표본 수로 계산한다.
법규표(`by_law`)는 분류별 합이 아니다. 전체는 모든 분류의 완료 신고를 한 범위로 묶어 상위 조문에 하위 조문을 포함한다(다른 분류에만 있는 하위 조문도 포함). 전체 탭의 법규 필터·요약 법규 막대(`overview.all`)와 같은 소속 규칙이다(2026-10-18 Opus 검수 결정).
`available_laws`/`has_empty_law`는 법규 필터 전 분류별 합집합/OR다. 확정액과 추정액은 별도 필드로 유지한다.

모바일 `AgencyStats.all`은 nullable이다. 값이 있으면 전체를 기본으로 사용하고, 구서버처럼 키가 없으면 전체 칩을 숨기고 교통위반으로 전환한다.
로컬 `computeStatsBundle`은 `all`과 분류별 `by_law`를 함께 계산한다. 요약은 기존 `StatsOverview.all`을 재사용한다.
DB 스키마·교환 컬럼·인증·WS 계약은 바뀌지 않는다. 공통 합성 입력은 `contracts/stats-all-vectors.json`이다.

전체 기관/담당자 카드의 모바일 목록 이동은 `FilteredListScreen(category: all)`에 화면 전용 `reportFilter`를 전달한다. 완료 상태·연도·법규와 함께 기관 키·담당자 정확값을 COUNT·LIMIT 전에 판정한다(아래 식별 조건 계약). 공용 목록 필터는 바꾸지 않는다.


## 통계 기관·담당자 목록 검색 식별 조건 (2026-10-18)

- `agencyKey`(string, 기본 빈 값)는 통계와 같은 기관 집계 키를 비교한다. 서버 `resolve_stats_agency(code, name)[1]`, 모바일 `registryKeyedAgency(code, name).key`를 그대로 사용한다. 키가 있으면 `agency` 이름 검색은 무시하며 모바일 요청에는 이름 조건을 넣지 않는다. 모델의 기관 표시명은 제목·칩과 구서버 fallback을 위해 유지한다.
- `managerExact`(bool, 기본 false)가 true면 `manager` 전체를 공통 문자열 정규화 후 simple lowercase로 비교한다. 쉼표 OR·`&` AND로 나누지 않는다. 빈 질의도 빈 담당자와 정확 비교한다. 일반 검색은 기존 부분 검색을 유지한다.
- `/api/v1/app/config`의 `capabilities`에 `report_search_identity_v1`을 추가한다. `report_search_version: 1`과 기존 키·응답은 유지한다. Client는 지원 신호를 확인한 경우에만 두 키를 전송한다. 미확인·구서버에서는 기관 표시명·담당자 부분 검색으로 열며 카드와 정확한 건수 일치를 보장하지 않는다. Standalone은 항상 지원한다.
- 서버는 SQLite 검색 UDF, 모바일은 읽기 트랜잭션 안의 기관 TEMP lookup(`stats_key`) 및 담당자 DISTINCT 원문 판정 후 SQL IN 조건으로 COUNT·LIMIT 전에 필터한다. 담당자는 BLOB 읽기로 BOM을 보존하고 Dart 정규화·소문자 비교를 적용하므로 SQLite의 ASCII 한정 lower()에 의존하지 않는다. 영구 DB 스키마·교환 컬럼은 바꾸지 않는다.
- 통계의 전체·분류별 카드만 새 조건을 사용한다. 연도(답변일)·법규·답변 완료·대표건 조건을 유지하며 조회 뒤 predicate로 제거하지 않는다. 검색 시트에 새 입력은 추가하지 않는다. 기존 기관·담당자를 바꾸지 않고 적용하면 정확 조건을 유지하고, 해당 이름을 바꾸거나 칩을 지우면 해당 정확 조건도 해제한다.
- 두 저장소 `contracts/client-search-vectors.json`에 기관 키 우선·담당자 정확값·구분자 리터럴 사례를 추가했다.

### 코드 대조 정정

| 기존 설명 | 현재 코드와 계약 |
|---|---|
| 전체 카드가 기관·담당자 조건을 좁힌 뒤 키를 대조한다 | 조회 후 대조는 없다. 정확 조건을 COUNT·LIMIT 전에 적용한다. 구서버 fallback은 이름 부분 검색이다. |
| 명세 배경의 웹 담당자 이름 부분 검색 | 기존 서버 SQL은 `담당자 == person` 원문 완전 일치였다. 기존 링크는 보존하고 `personExact=true`에서만 정규화한 정확값 비교를 추가한다. |
