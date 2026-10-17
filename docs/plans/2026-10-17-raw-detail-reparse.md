# 상세 원본 저장·재파싱·DB 최적화 (2026-10-17 명세)

사용자 결정(2026-10-17)을 구현 명세로 옮긴다. 서버(safetyreport)와 모바일(safetyreport-mobile) Standalone 은 **같은 시점·같은 규칙**으로
동작해야 한다(호출 지점까지 1:1). 같은 문서를 두 레포 `docs/plans/` 에 둔다. 구현: Muse, 검수: Sol, 최종 감사: Opus.

규칙 우선순위: `PROJECT_RULES.md` 3-1(DB 교환 무결성)이 이 명세의 다른 목표보다 앞선다. 모르는 값은 버리지 말고 보존하거나 명확한 오류로 멈춘다.

## 0. 용어
- **상세 원본**: 안전신문고 상세 API(`/api/v1/portal/mypage/mysafereport/{c_no}`) 응답 envelope 전체(JSON 객체).
  서버 `core/crawler/crawldetail_api.py` 의 `data`, 모바일 `standalone_api_service.dart fetchReportDetail` 이 지금 버리는 envelope.
- **신고자 키 5개**: `result.C_NAME`, `result.C_EMAIL`, `result.C_PHONE`, `result.C_PHONE2`, `result.C_ID`. 이 5개만 지운다. 나머지는 전부 저장한다
  (공무원 담당자 이름·연락처, 본문, 파일 목록 포함). `result` 가 객체가 아니면 아무 것도 지우지 않는다.

## 1. 상세 원본 저장
### 1-1. 정규 직렬화(서버·모바일 동일)
1. 응답 envelope 를 JSON 으로 읽는다. 키 순서는 응답 순서를 유지한다.
2. `result` 객체에서 신고자 키 5개를 제거한다(다른 위치의 같은 이름은 건드리지 않음).
3. 직렬화: UTF-8, 비ASCII 그대로, 공백 없는 구분자(Python `json.dumps(obj, ensure_ascii=False, separators=(",", ":"))`, Dart `jsonEncode`).
4. `raw_sha256` = 3의 바이트 sha256(hex 소문자).
5. DB 저장값 = 3의 바이트를 **zlib(RFC 1950)** 으로 압축한 BLOB(Python `zlib.compress(b, 6)`, Dart `ZLibCodec(level: 6).encode`).
   읽을 때는 zlib 해제 후 sha256 이 맞는지 확인한다. 안 맞으면 그 행은 "손상"으로 보고 원본 보기·재파싱에서 쓰지 않는다(조용히 버리지 말고 로그).
- 서버와 모바일의 압축 바이트가 달라도 된다. 교환·백업은 **저장된 BLOB 바이트를 그대로** 옮긴다(재압축 금지).

### 1-2. 표(스키마)
- 서버 새 표 `mysafety_report_detail_raw`, 모바일 새 표 `report_detail_raw`. 열은 같다:
  `ID TEXT PRIMARY KEY`, `raw_zlib BLOB NOT NULL`, `raw_sha256 TEXT NOT NULL`, `raw_size INTEGER NOT NULL`(압축 전 바이트 수), `fetched_at TEXT NOT NULL`(UTC ISO8601, 밀리초, `Z`), `source TEXT NOT NULL`(`crawl` | `backfill` | `notification` | `rebuild` 등 받은 경로).
- 신고 행과 1:1. 같은 ID 로 다시 받으면 덮어쓴다(upsert). 신고 행을 지우는 기존 경로가 있으면 원본 행도 같이 지운다.
- 스키마 버전: 서버 `SCHEMA_VERSION` 6→**7**, 모바일 `dbVersion` 17→**18**, 모바일 `serverSchemaVersion` 6→7, 서버 `MOBILE_SCHEMA_VERSION` 17→18,
  `contracts/storage-contract.json`(두 레포 바이트 동일) 갱신.

### 1-3. 저장 시점(모든 상세 수집 경로)
- 서버: `crawldetail_api.crawl_details` 가 `data` 를 받은 직후(`:97` 근처) 정규 직렬화 → 결과 튜플/`CrawledDetail` 에 원본(직렬화 바이트)을 실어
  `reports_repo._save_one` 의 같은 트랜잭션에서 원본 표에 upsert. 초기화 크롤(`_rebuild_consume_details`)·일반 크롤·단건 모두.
- 모바일: `fetchReportDetail` 이 envelope 도 돌려주게 바꾸고(기존 반환값 의미 유지), `captureAndSaveDetail` → `upsertReport` 와 같은 트랜잭션에서 upsert.
  증분/전체/rebuild 루프(`sync_engine.dart`)와 알림 단건(`_tryFetchSingle`) 모두.
- 파서 입력은 지금처럼 `result` 이다(신고자 키 제거 **전** 객체를 파서에 줘도, 제거 후를 줘도 파서 결과가 같아야 한다 — 파서는 5개 키를 쓰지 않음을 시험으로 고정).

### 1-4. 원본 파일(사람이 보는 사본)
- 서버: `<datapath>/raw_details/<ID>.json`. 모바일: `Documents/mysafetyreport/raw_details/<ID>.json`(`AppStoragePaths.subDir`).
- 내용: 1-1 의 신고자 키 제거 객체를 **들여쓰기 2칸**으로 쓴 UTF-8 JSON(비ASCII 그대로). 신고당 최신 1개, 받을 때마다 덮어쓴다(임시 파일 → rename).
- 파일 쓰기 실패는 DB 저장을 막지 않는다(대체 진단 기록 `note_fallback`/모바일 대응). 기존 `data/logs/*_api_raw.json`(디버그 `scripts/debug/extractor.py`)은 건드리지 않는다.
- DB 를 복원·가져오기 하면 파일은 DB 에서 다시 만들 수 있어야 한다(원본 보기 화면의 "파일로 다시 쓰기" 또는 최적화 작업이 없는 파일을 채움).

## 2. 스키마 업그레이드(이전 DB 비우기 대신)
- 출시 스키마는 서버 6, 모바일 17 이다. 새 버전은 **추가만 하는 제자리 업그레이드**로 자료를 그대로 둔다:
  업그레이드 전 백업(서버 `before_schema_v7_*.db`, 모바일 `before_v18` — 기존 `migrate_schema`/`migrateInPlace` 방식) → 새 표 생성 → 버전 기록. 실패하면 DB 를 바꾸지 않는다.
- 서버 6 미만·모바일 17 미만(5→6, 16→17 제자리 경로가 이미 있는 버전 제외)은 지금 규칙(백업 뒤 비우기)을 유지한다. 즉 체인: 서버 5→6→7, 모바일 16→17→18.
- 모바일 `_open` 의 "[이전 DB 업데이트 비활성 — 2026-09-26 초기화 크롤링 릴리스]" 마커 자리는 이번 규칙에 맞게 정리하고 주석을 갱신한다(죽은 코드 `_migrateLocalDatabase` 는 쓰지 않으면 그대로 두되 설명을 맞춤).
- 예전 백업 파일 가져오기: 서버는 서버 6 DB·모바일 17 DB 를, 모바일은 서버 6 DB·모바일 17 백업을 **같은 업그레이드 로직으로 올려서** 받는다. 지금의 "버전이 정확히 같아야 함" 검사는
  "지원 범위(서버 6~7, 모바일 17~18)면 올려서 받고, 더 새 버전은 거절, 더 오래된 버전은 지금처럼 거절"로 바꾼다. 올려 받은 DB 는 원본 표가 비어 있다(최적화 작업이 채움).
- 교환(서버 `core/storage/exchange.py`·`services/db_backup.py`, 모바일 서버 DB 가져오기/내보내기·모드 전환): 원본 표를 양방향으로 옮긴다(`known_mobile_columns` 등 매핑에 추가, 모르는 열 거절 규칙 유지).
  왕복 시험(서버→모바일→서버, 모바일→서버→모바일)에서 원본 표의 **모든 열 값이 바이트까지 같음**(BLOB, sha, NULL 없음)을 확인한다. 계약 열 목록은 각 레포 `docs/architecture/data-contracts.md` 한 곳.

## 3. DB 최적화 작업(백그라운드, 취소 불가)
### 3-1. 언제 도는가
- DB 메타(서버 `mysafety_sync_meta`, 모바일 `sync_meta`)에 키 `detail_raw_optimize` = `{"target": "<파서 버전>", "cursor": "<마지막 처리 ID>", "done": bool, ...}`.
- 시작(서버 기동, 모바일 앱 실행 — Standalone 모드만) 때 `target != 현재 파서 버전` 이거나 `done` 이 아니면 작업을 시작/이어서 한다. 평소 실행에는 아무 일도 하지 않는다.
- 이번 업데이트는 파서 버전을 서버 `pc-parser-13`, 모바일 `mobile-parser-13` 으로 올려 첫 실행에 한 번 돈다. 앞으로 파서 로직이 바뀌면 버전만 올리면 된다.

### 3-2. 하는 일(신고마다, ID 순, 커서로 이어서)
1. 원본 표에 행이 있고 sha 가 맞으면: **네트워크 없이** 저장된 원본의 `result` 로 다시 파싱한다.
2. 행이 없으면(이번 업데이트 직후 기존 신고 전부): 상세를 다시 받아(`crawldetail_api._fetch_detail` / `fetchReportDetail`, 크롤과 같은 요청 간격·재시도) 1-3 처럼 원본 표·파일에 저장하고 파싱한다.
   로그인이 필요하면(세션 없음·만료) 작업을 잠시 멈추고 상태에 "로그인 필요"를 보인 뒤, 로그인되면 이어서 한다. 받을 수 없는 신고(404·삭제 등)는 건너뛰고 사유를 기록한다.
3. 파싱 결과 저장: **파서가 만드는 열만** 바꾼다(목록 크롤이 채우는 열·사용자 수정값 `override` 는 유지). 기존 저장 경로의 공유 캡처를 그대로 타서,
   공유 payload 가 바뀐 신고만 새 `completed_observation`(source_revision 증가)으로 커뮤니티 서버에 재전송된다. 바뀌지 않으면 이벤트 없음.
   (기존 "재전송은 journal amount 보존·추정만 재계산" 결정은 이 작업에서는 적용하지 않는다 — 원본으로 다시 파싱한 확정 판정이 정답이다. `resend_fine_estimate_updates` 는 그대로 둔다.)
4. 원본 파일이 없으면 만든다.
5. 진행 로그(단계·건수·건너뜀 사유)를 남긴다. 모든 신고를 끝내면 `done=true`.
- 크롤링 상태 UI(크롤 창의 진행/중지 버튼, 크롤 run 상태)에 **넣지 않는다**. 사용자가 취소할 수 없다. 서버 종료·앱 종료 뒤에는 커서부터 이어서 한다.

### 3-3. 크롤과의 순서
- 작업이 도는 동안 수동 크롤(웹·`/api/v1/crawl/start`·모바일 크롤 화면)과 알림 크롤은 **바로 시작하지 않고 대기열 뒤에 붙는다**.
  크롤 창 로그에 "DB 최적화 중입니다. 최적화가 끝난 뒤 크롤링을 시작합니다." 를 한 줄 남기고, 작업이 끝나면 자동으로 이어서 시작한다.
  서버는 `crawl_control._check_crawl_allowed`·`crawl_manager` 의 보류/재시도(`launch_pending_crawl`, `run_after_crawl`) 장치를, 모바일은 `SyncEngine` 의 차단 훅(`rebuildBlocks`)·알림 큐를 쓴다.
  지금 수동 크롤이 "실행 중이면 예외"인 서버 경로는 최적화 중일 때만 대기열 등록으로 바꾼다(API 응답은 하위호환 — 필드 추가만).
- 작업 시작 시점에 크롤이 이미 돌고 있으면 그 크롤이 끝난 뒤 작업을 시작한다.
- 초기화(rebuild) 크롤이 필요한 상태면 기존 규칙(초기화 우선)을 따르고, 최적화는 초기화가 끝난 뒤 돈다.

### 3-4. 표시(하단 "DB 최적화 중")
- 서버 정비 상태(`services/maintenance_service.status()` → `/maintenance/status`, `/api/v1/maintenance/status`)에 작업 `db_optimize` 를 추가한다.
  필드 추가만(하위호환): `label`("DB 최적화 중입니다…"), `done`/`total`, `stage`, `log`(최근 단계 로그 최대 50줄, 시각+문구).
- 단계 로그 예: "업그레이드 전 백업 완료(파일명)", "새 표 생성(스키마 7)", "원본 채우기 1,234/3,125", "재파싱 변경 17건", "공유 재전송 5건", "로그인 필요 — 로그인 뒤 이어서", "완료".
  스키마 업그레이드 단계(기동 때 웹이 뜨기 전에 끝나는 단계)도 같은 로그에 남겨 기동 뒤 표시줄에서 보이게 한다.
- 웹: `base.html #srJobBar`·`shell-polling.js` 하단 표시줄에 라벨·진행률을 보이고, 누르면 단계 로그를 펼친다. 라이트·다크 테마, 390px 폭.
- 모바일: `widgets/maintenance_status_bar.dart` 에 같은 작업·로그를 보이고 탭하면 로그 시트. Standalone 은 로컬 작업, Client 는 서버 상태 API.
  모바일 DB 업그레이드(앱 시작 때 DB 를 여는 단계)는 시작 화면 하단에 "DB 최적화 중입니다…"와 단계 로그를 보여 준다(앱이 멈춘 것처럼 보이지 않게).

## 4. 원본 보기 화면
- 웹(PC): 신고 상세 모달 footer 에 "원본 응답" 버튼 → 모달에서 들여쓰기 JSON(고정폭, 검색, 복사, `.json` 다운로드). 원본이 없으면 "아직 원본이 없습니다(DB 최적화가 채웁니다)".
  읽기 라우트는 관리자 인증 필수. 파일 탐색기(`/file-browser`, `services/file_service.ALLOWED_BROWSER_DIRS`)에 `raw_details` 폴더 추가.
- 모바일 API(Client 모드용): `GET /api/v1/reports/{id}/raw-detail` (API 키 인증) → `{id, fetched_at, source, raw_sha256, json}`. 추가만, 기존 응답 불변.
- 모바일: 신고 상세 시트에 "원본 응답" 동작 → 전체 화면 JSON 보기(고정폭, 검색, 복사, 공유). Standalone 은 로컬 DB, Client 는 위 API. 설정 '데이터 관리'에 "원본 응답 폴더"(파일 탐색기 `raw_details`).
- 표시하는 JSON 은 신고자 키 5개가 이미 빠진 것이다.

## 5. 모바일 검색 필터(별점 탭 로딩 버그)
- 신고내역 탭·별점 탭·데이터 수정 탭이 `ReportProvider._filter` 하나를 공유해, 신고내역에서 건 조건이 별점 탭에 걸리고 Client 모드에서 전체 스캔이 계속 돈다.
- 변경: 이 세 화면 중 하나에서 **다른 탭(하단 탭 또는 신고관리 하위 탭)으로 떠나면 공용 검색 조건을 비운다**. 드릴다운(`pushReportDrillDown`, 별도 `_localFilter`)은 영향 없음을 시험으로 고정.
- 함께 고칠 결함: `local_paged_report_list.dart` 에서 스캔 중 `datasetEpoch` 가 바뀌면 `_loading` 을 끄지 않고 return 하는 경로, 숨은 탭이 필터 변경마다 전체 스캔을 다시 시작하는 경로(보이지 않는 동안은 미룸).

## 6. 시험·문서·완료 기준
- 계약 벡터: `contracts/` 에 정규 직렬화·신고자 키 제거·sha 벡터(서버·모바일 바이트 동일) — 같은 입력에서 Python·Dart 의 정규 바이트와 sha 가 같아야 한다.
- 시험: 업그레이드(6→7, 17→18, 실패 시 무변경, 백업 생성), 예전 백업 가져오기(올려 받기), 교환 왕복(원본 BLOB 바이트 동일), 최적화 작업(커서 이어하기·로그인 필요 멈춤·건너뜀·파서 열만 변경·override 유지·
  바뀐 건만 재전송), 크롤 대기열(최적화 중 수동·알림 크롤이 뒤에 붙고 로그 한 줄), 상태 API 추가 필드, 원본 보기 API 인증, 필터 초기화·로딩 결함.
- 웹 브라우저 시험(fixture 서버): 하단 표시줄·로그 펼침, 원본 응답 모달(라이트·다크, 1440·390px).
- 실데이터·실크롤·외부 전송·운영 DB 접근 금지. fixture·합성 응답만 쓴다. 합성 원본에는 신고자 키 5개를 넣어 제거를 시험한다.
- 문서: 각 레포 `docs/architecture/data-contracts.md`(새 표·열·교환·업그레이드 규칙·최적화 메타), `crawling-and-processing.md`, `web-ui.md`(서버), 모바일 대응 문서, `CHANGELOG.md`.
- 금지: VERSION·pubspec version·태그·push·운영 접근. 테스트 skip·assert 완화 금지. 기존 결함과 신규 회귀를 구분해 보고.
