# safetyreport/dev 전체 리팩터링 검토·상세 계획 — 2026-10-04

> **계획 제출본. 구현 승인이나 실행 검증 결과가 아니다.** 이번 세션은 입력 압축 해제, 코드·계약의 정적 열람, 문서 작성과 요청된 입력 패키지 정리만 수행한다. 앱 import/기동, 테스트, 벤치마크, 첨부 helper, 의존성 설치, 실제 외부 작업, 운영 변경, commit/push/배포는 수행하지 않았다. 이 문서 제출 뒤 구현을 자동으로 시작하지 않는다.

## 1. 결론과 적용 기준

자료 손실 방지와 권한 경계를 먼저 고친다. 다음은 API/필터의 정확성, 크롤·별점·공유·복원의 작업 수명, 그 다음 조회·집계·캐시·렌더링 성능이다. 성능 목표 달성을 이유로 DB 왕복, 확정/추정 금액 분리, 전체 모집단, 실패 표시 또는 인증을 완화하지 않는다. 기존 FastAPI/Jinja2/Bootstrap/DataTables/SQLite와 Python source/PyInstaller/Docker를 유지한다. 신규 신고 접수·랭킹·알림센터·React/새 DB 엔진은 범위 밖이다.

현재 기준은 `dev`의 HEAD `e9444aa4e14fa3d3bacc7efcfc32cd6e26f782d0`이다. 감사 기준 `2eb833d8b748b8b3bb94e254e680739b06f362fd`와의 차이는 문서 6개뿐이다. 제품 소스가 같다는 사실은 감사의 인과 설명이나 재현 주장을 모두 증명하지 않는다. 현재 코드에서 부분 해결·기존 방어를 별도로 확인했으며, 이를 다시 만드는 작업을 제외했다. 로컬 캐시의 `refs/remotes/origin/dev`는 감사 기준 SHA였다. fetch/ls-remote를 하지 않았으므로 실제 원격 최신 상태는 미확인이다. reset/checkout으로 기준 SHA에 돌아가지 않았다.

입력은 `02_sol_planning_prompt_ko.md` → `01_safetyreport_dev_refactoring_plan_ko.md` → `03_source_audit_original.md` 순서로 끝까지 읽었다. 01은 837줄, 03은 1,062줄의 입력이다. 관련 규칙은 현재 `AGENTS.md`, `PROJECT_RULES.md`를 우선하고, 계약은 현재 코드·정본의 현행 정정 절을 대조했다. 옛 문서의 역할 분담과 자동 테스트 명령을 이번 구현/실행 허가로 해석하지 않았다.

입력 추적 SHA-256:

| 입력 | SHA-256 |
|---|---|
| 02 계획 요청 | `11898ebfa45b71425056c504ec52fbbd42abf75879dccca35a38b8e91a3d7f6f` |
| 01 단계 계획 | `24bb68377edf7ec6b41a4af6cc5af40fc341926e26a7141fdbfedff69866222c` |
| 03 원본 감사 | `a1d324e24f08169fdd0879c436904f0b8af7ef2d78d065dc3193769aea047885` |

기존 사용자 변경 `VERSION`의 dev 값, `docs/refactor-hardening-test-plan-2026-05.md`, `issue/`, `testresults/`, `tests/test_db_backup_regression.py`, `tools/web-tests/preview.config.ts`, 별도 `safetyreport-agency-registry-handoff.zip`을 보존한다. 10-03 계획·부록도 덮어쓰지 않는다. 입력 ZIP과 이번 압축 해제 폴더만 정리 대상으로 지정한다. 제품 코드·기존 테스트·설정·DB는 이번 변경 대상이 아니다.

## 2. 증거 범위와 부록

추적 파일 399개를 분류했다. Python 181개, HTML 24개, TypeScript 23개, JavaScript 7개, CSS 8개다. 자체 AST 열람으로 class/def/async def 2,407개와 route decorator 139개를 목록화했다. 이 수는 런타임 호출 수·실제로 mount된 route 수·심층 검토 완료 수가 아니다. include_router prefix, middleware, 인증, 동적 등록은 별도 대조 대상이다. 자체 정적 열람 코드는 저장소 모듈을 import하지 않았다.

| 부록 | 범위와 한계 |
|---|---|
| [scope CSV](dev-refactoring-scope-2026-10-04.csv) | 399개 파일별 유형·검토 수준·not-run. N 173개 이름/유형, C 18개 지침/계약 전체 또는 명시한 관련 절, F 46개 핵심 경로 집중 열람, S 142개 AST 구조, X 16개 generated/binary, T 4개 특정 assertion 열람. F는 파일 전체 감사라는 뜻이 아니다. |
| [symbol CSV](dev-refactoring-symbols-2026-10-04.csv) | 2,407개 정적 심볼/시작·종료 줄. 호출·동시성의 동적 증거는 아님. |
| [route CSV](dev-refactoring-routes-2026-10-04.csv) | 139개 decorator의 파일/함수/메서드/선언 경로. `/api/v1` mount와 protocol middleware를 함께 봐야 함. |
| [feature gates CSV](dev-refactoring-feature-gates-2026-10-04.csv) | 기능표 128행 전부의 원본 열, 행 순번, 관련 PR, 입력·assertion·완료 게이트. SH-12가 중복되어 고유 ID는 127개; 행을 임의 병합하지 않음. |
| [storage columns CSV](dev-refactoring-storage-columns-2026-10-04.csv) | 현재 storage-contract의 14 entity, 110개 컬럼 정의와 ownership/교환 여부/원문 정의. 실제 양쪽 스키마 일치/변환 성공의 증거는 아님. |

증거 수준은 **S: 현재 소스의 분기·호출 정적 확인**, **H: 과거 문서/테스트 기록**, **R: 이번 실행 재현**으로 구분한다. 이번 R 증거는 없다. 기존 10-03 문서의 `573 tests / 568 passed / 5 skipped / 138.954초`, 감사 helper의 Python 3.12/pandas 2.2.3 수치(약 3.687초 포함), 과거 운영 사본/왕복 결과는 H다. 현재 Python 3.14/pandas 3 환경에서 재측정한 값이 아니다. 아래 계획의 테스트·목표값은 모두 **예정/not-run**이다.

## 3. 현재 아키텍처·호출·writer/lock inventory

### 3.1 요청·자료 흐름

1. `main.py`가 인증/protocol/커뮤니티 gate middleware와 router를 구성한다. 웹은 관리자 세션, `/api/v1`은 API key와 selfhost protocol 3 계약, WS는 별도 인증이다. `lifespan`은 DB 초기화/유지보수, scheduler, Sunwi, 커뮤니티 uploader/auth/rebuild, media cleanup, direct-login keepalive, bot 프로세스를 관리한다. import/기동 자체가 운영 부수효과를 낼 수 있다.
2. 목록/검색 → `report_query_service` → SQL/대표건 projection → 기존 전체 JSON 또는 기존 bounded page API. 통계 → `report_stats_service._load_stats_frames` → SQL → 대표건 → 행 필터 → 취하 → 법규 → 집계. `get_stats_page`는 이미 로딩을 공유한다. 전체 API를 페이지 일부로 바꾸지 않는다.
3. 크롤 요청 → `crawl_control`/`CrawlManager` → 실행별 큐 파일·로그 → source `start.py` 또는 frozen `--mode crawl` → 로그인/목록/상세 pipeline → `reports_repo` → raw/title/entry/merge → 중복 갱신 → 내보내기/알림 → 완료 marker/후처리. bot는 현재 이 접수 경계를 우회해 프로세스를 직접 실행한다.
4. 상세 저장 → `reports_repo._community_capture_for`의 community capture → 개인 DB transaction → 개인 저장 완료 표시. capture-first와 recovery를 보존한다. 모든 DB를 무조건 한 transaction으로 합치는 변경은 하지 않는다.
5. 공유 → scoped outbox → uploader의 lease/배치/ACK/retry → 서버 완료 manifest. rebuild는 개인 백업·manifest·목록/상세·공유·commit을 잇는 영속 job이다. 원격과 개인 DB의 부분 성공을 분리한다.
6. 복원 → `db_backup`/`core/storage/exchange` 검증 → staging → 백업 → integrity → 연결 정리/원자 교체. 기존 `sqlite3.backup` 백업, 현재 구버전 거절, 복원-크롤 예약·실제 종료 확인·pending 원자 저장을 보존한다.
7. 미디어 → remote URL 검증 → prime future → tmp/progress → 스트림/완성 캐시. WS는 현재 broadcast gather, 크롤 로그는 이미 bounded tail, 별점 로그는 별도 무제한 경로다.

### 3.2 경계별 소유권

| 자원/writer | 현재 동시성 경계 | 유지/보완할 경계 |
|---|---|---|
| 개인 DB: `reports_repo`, overrides/entry/editor/watchlist/rating/duplicate 저장 | SQLAlchemy transaction, `write_barrier`의 프로세스 내 연결 장벽 | 모든 writer 목록과 generation을 명시. 교체 시 transaction/connection을 새 DB로 재개. 프로세스 간 안전은 별도 접수·프로세스 종료/lease로 보장해야 함. |
| `CrawlManager` active process/pending | `_state_lock`, restore generation, 실행별 queue, atomic JSON/fsync, 예약 worker/timer | 이미 있는 예약·큐 복구를 보존. start/register/watch/terminal 순서, rating와 공통 충돌 경계 추가. 네트워크 flush를 state lock 안에서 오래 기다리는 문제는 예약 후 밖에서 처리. |
| bot/source/frozen child | 별도 프로세스라 위 singleton/write barrier를 공유하지 않음 | bot의 시작을 서버 접수 경계로 통합. 자식은 job/attempt를 받아 outcome만 기록하고 권한 판단은 재검증. |
| community.db outbox/upload_runs/rebuild jobs/items | store transaction, upload lease, active rebuild 유일성, rebuild control 직렬화 | 프로젝트/계정/connection/grant/dataset scope, 만료·heartbeat·attempt fencing, pause의 실제 정지 확인. |
| settings.ini와 전역 settings | `ConfigParser` 재사용·직접 저장, 통일된 쓰기 lock 없음 | snapshot 교체·동일 process lock·다중 writer 파일 lock·atomic replace. 여러 process의 stale 덮어쓰기 거절. |
| 파생 cache/probe | 전역 RLock, 8-entry LRU, deepcopy, DB/WAL/stat/data_version | 결과 소유권 분리·single flight·generation 재검증·inode 교체 probe 재연결·byte budget. 인증 결과는 저장하지 않음. |
| media tmp/cache/future/progress | path별 lock, `_prime_guard`, callback, progress dictionary | callback 등록 시 즉시 실행 가능; file ready/length/failure/cancel을 별도 상태로 관리. |
| WS/API clients/log | main loop 등록, gather, thread bridge, 연결 metadata | 연결별 bounded queue/send deadline, disconnect 정리, thread future 관찰, bounded log, lifespan join. |
| Sunwi/direct login/uploader/auth/scheduler/retry timer | 각 worker/thread/event와 종료 함수 | 한 종료 deadline 아래 cancel→실제 join→reference 정리. 살아 있는 thread를 stopped로 표시하지 않음. |
| ZIP/export/Sheets/Telegram | 메모리 ZIP, 외부 I/O와 재시도, 일부 import 시 초기화 | 임시 파일·응답 수명·외부 deadline·부분 성공 journal. 기존 파일과 원격 sheet를 무조건 되돌리는 rollback 금지. |

**잠금 기본안:** 접수 lock은 scope/generation/job 예약만 짧게 보호한다. SQL transaction 안에 네트워크·프로세스 join·WS send를 넣지 않는다. 개인 DB 연결 장벽과 community transaction을 동시에 잡아 외부 작업을 기다리지 않는다. 상세 저장의 기존 capture-first 순서는 유지하며 교차 DB 실패를 journal/recovery로 해결한다. 복원 예약과 공통 작업 예약은 같은 순서로 획득하고, 취소 시 실제 writer 종료를 확인하기 전 예약을 풀지 않는다. 이 순서는 P06/P08의 fault-injection으로 검증한 뒤 확정한다.

## 4. 보존 계약과 설계 기본안

| 계약 | 현재 근거·보존할 의미 | 선행/완료 게이트 |
|---|---|---|
| 전체 기능 | `feature-matrix.csv` 128행, pilot DOM, web-ui 문서 | 부록의 각 행을 실제 fixture에서 수행. existing/planned 분류를 보존; 없는 기능을 통과로 바꾸지 않음. 메뉴/테마/storage/선택/CSV/지도/다운로드 포함. |
| DB 구조·값·타입 | `storage-contract.json`: 서버 5/모바일 16, 14 entity; `exchange`/`reports_repo` | 스키마 변경 기본 제외. 모든 교환 컬럼 값+SQLite typeof+NULL/''+행 존재 비교. unknown non-NULL 컬럼은 현재처럼 교체 전 거절. |
| 원문/수정/entry/merge | site/user/derived/meta/server_only ownership | 좁은 조회가 public 전체 필드를 누락하지 않아야 함. 저장/병합 우선순위·빈 entry와 미존재 entry·첨부 만료·수동 대표·not_duplicate 유지. |
| 양방향 DB 교환 | 서버→모바일→서버 및 모바일→서버→모바일의 실제 변환 | 모바일 저장소는 이번 세션 열람/실행하지 않음. 변환/스키마 의미 변경은 양쪽 동시 작업의 별도 승인 후 진행. 서버만 mock한 검사를 왕복 성공이라 부르지 않음. |
| watchlist/sync_meta/owner | 서버 watchlist 표 vs 모바일 sync_meta 변환, dataset/account ownership, 기존 관리자/API key 보존 | 교환 전후 각 key·값·타입과 owner를 비교. 잘못된 계정/legacy/future/corrupt DB는 409 기존 code/detail/message. |
| 통계 모집단 | raw/canonical 상태, lifecycle, 대표건 projection은 독립 축 | SQL→대표건→행조건→취하→법규의 현재 순서 유지. 사람/기관 미지정 제외 기준, 분모·완료건 처리일수·rating==0 포함 여부를 현행 함수의 golden으로 고정. |
| 확정/추정 금액 | 원문 `범칙금_과태료`와 파생 추정 금액, 촬영정보·규칙 버전 | 별도 열·합계·라벨. 일부수용과 과태료 mask의 중첩을 허용. 계산한 추정값을 DB 확정 열에 저장하지 않음. |
| 날짜/NULL/filter | 현재 리스트·통계·지도 경로가 일부 불일치 | 기본안 NULL 상태는 unknown으로 취하 제외 시 포함, 종료일은 로컬 날짜의 하루 전체. 이 버그 수정은 성능 PR과 분리하여 기대 변경을 명시. |
| HTTP/API | `/api/v1`, protocol3, key scope, 기존 전체 + page API | URL/method/query/default/필드/type/order/error 비교. static route 우선. 내부 DTO 변경은 외부 adapter로 보존. 전체 API에 자동 limit 금지. |
| WS | `/ws/events`, web 로그 WS, auth/gate/protocol close | 4001/4403/4406와 기존 type/data payload 보존. overload code/pong 정책은 실제 소비자 호환 확인 전 강화 보류. |
| 완료 marker | done/new-ID/last_sync와 확장 소비자 | job 성공·실패와 marker 소비를 분리. 실패를 성공 로그로 표현하지 않음. 신규 내부 last_attempt는 교환 sync_meta 의미를 바꾸지 않음. |
| community | gate/rebuild/envelope/ACK/canonical JSON/upload-control/selfhost 계약 | 탐색 TTL과 action freshness 분리. scope가 다른 outbox를 이번 계정 upload 성공/실패와 섞지 않음. 기존 quota/간격/원격 정책 유지. |
| source/frozen/Docker | runtime-and-packaging, `runtime_paths`·resource 수집 | 신규 모듈/리소스 포함·플랫폼별 경로·subprocess mode·temp cleanup. 승인된 dev 패키징만, release 경로 제외. |

DB 계약 문서의 기존 허용 차이 `map_backfill_state`, NULL→''/주소 재계산/`synced_at` 생성 기록은 **완전 무손실 검증과 동일하지 않다**. 이 항목을 무시하는 comparator로 이번 목표를 통과시키지 않는다. 교환 대상과 server-only runtime metadata의 경계를 양쪽 실제 코드와 계약으로 명확히 하고, NULL 정규화가 실재하면 양쪽 함께 보존을 수정한다. 사용자 요구에 맞지 않는 기존 예외는 별도 결정 없이 확대하지 않는다. 구버전 DB 자동 업그레이드/복원 허용을 재도입하지 않는다.

현재 통계의 추가 고정 조건은 다음과 같다.

- 기관·담당자·법규 표는 `_OVERVIEW_COMPLETED_STATUSES`에 속한 답변 완료 모집단만 사용한다. 함수 안의 옛 S-10 주석(처리중 포함)보다 뒤의 실제 completed mask를 우선한다. 요약·지도 전체 모집단과 이 완료 표의 모집단 차이를 유지하고 상세 지도는 completedOnly로 연결한다.
- 평균 별점은 numeric 1~5만, 평균 처리일수는 현행 유효 답변/신고일 표본만 쓴다. `avg_days_count`와 `rating_count`는 별도다. 값 0/NULL을 임의 같은 상태로 정규화하지 않는다.
- 현재 full-map API의 default dedupe와 bounded points API의 명시 dedupe 옵션 차이는 현행 계약으로 고정한다. 새 기능을 핑계로 default를 통일하지 않는다.
- source 값 보존의 NULL/빈 문자열 구분과 change-tracked 비교에서 NULL/빈 문자열을 같게 취급하는 현재 규칙은 서로 다른 계약이다. 저장·변환 exact 보존을 지키면서 synced_at 비교만 현행 의미를 유지한다.

### 4.2 HTTP/WS wire 세부 게이트

변경 전후 같은 인증 fixture를 사용해 route CSV의 각 등록 함수에 method/query/body/response schema snapshot을 만든다. 아래는 단순 `status==success` 검사로 대체하면 안 되는 주요 필드다. 나머지 endpoint도 전체 payload를 비교하며 이 표에 없다는 이유로 삭제하지 않는다.

| 경로/그룹 | 보존 필드·범위 | 오류/권한·후속 검사 |
|---|---|---|
| `/api/v1/server/version` | status/version/latest_version/up_to_date/protocol_version/supported_client_protocols/minimum_server_major | 유효 API key 필수; 호환성/커뮤니티 probe 예외만 유지. probe 성공으로 신고 권한을 부여하지 않음. |
| `/api/v1/reports/{category}` | status/category/count/data의 전 레코드·교환 컬럼·duplicate metadata | 전체 행 반환, exact_values의 NULL/integer 유지. invalid category400/정상 인증401 경계 유지. |
| `/reports/{category}/page` | status/category/total/offset/limit/count/next_offset/dedupe_mode/data | offset≥0, limit1~1000, ID ascending, 끝 next_offset null. count/page는 동일 read transaction; 여러 page 요청의 snapshot 보장은 새로 주장하지 않음. |
| `/vehicle/{vehicle_number}`, `/address?q=` | vehicle_number 또는 address/count/data와 기존 순서·대상 | query exact/AND·OR/대표건 필터 의미, 전체 레코드 필드 보존. |
| `/summary`, `/stats`, `/stats/overview` | status/data 전체; result_distribution의 accept/partial/reject/unknown, violation_laws의 name/filter/count | unknown은 완료 답변완료 축, 처리중과 구분. 없는 법규 name 빈 문자열/filter `__없음__`, 저장 법규 조합 기준. partial+fine 동시 포함. |
| `/stats/map`, `/stats/map/points`, `/stats/map/missing` | points/meta 전체; total_reports/geocoded_reports/missing_reports/address_groups/agency_count | bounded의 viewport_reports/rendered_points/point_budget/clustered는 표시 축. max_points1~1200/zoom0~19/finite bounds. full API truncate 금지. |
| `/maintenance/status`, `/stats/map/progress`, Sunwi | 기존 data 및 idle 호환/CSV의 kind/path/filename/results_dir | maintenance 자동 작업과 fixture 외부 차단 유지, Sunwi 별도 모집단. |
| watchlist/duplicates/editor/settings/rating/crawl | route별 request field type·response field type·error·ownership | bulk/static dispatch, key의 read/manage 범위, JSON body object 검증; 기존 알려진 오류의 code/detail/message 보존. |
| `/crawl/done`, `/crawl/done/ext` | marker 내용·신고 ID 목록·읽으면 소비하는 기존 의미 | 두 번 읽기/누락/실패 terminal과 소비 경쟁 검사. marker≠job succeeded이며 성공 오표시는 수정. |
| files/database upload/download | binary bytes·Content-Type/Disposition·filename·409 거절payload | query api_key 다운로드도 protocol headers 요구, active log snapshot·WAL backup·현행 owner 검증 보존. |
| `/ws/events`, `/crawl/ws/logs`, `/rating/ws/rating_logs` | events connected/ping/crawl_* 및 type/data, 기존 로그 text framing | auth4001, community4403, compatibility4406; 호환성 검증 전 connected/log 전송0. verified admin session은 log WS에만 허용. |

외부 HTTP는 `X-SafetyReport-Client`(mobile/chromeextension), 실제 `X-SafetyReport-Version`, 정확한 문자열 `X-SafetyReport-Protocol: 3`을 요구한다. WS는 client_type/client_version/client_protocol과 매 재연결 재검증을 유지한다. 유효 key라도 missing/legacy protocol은409 CLIENT_UPGRADE_REQUIRED, malformed/unsupported는409 CLIENT_PROTOCOL_UNSUPPORTED, old/invalid server는409 SERVER_UPGRADE_REQUIRED이며 `Cache-Control: no-store`다. 헤더는 인증이 아니고 관리자 cookie는 `/api/v1` 예외가 아니다. 이 세부 게이트는 `contracts/selfhost-compat/README.md`와 현행 route/middleware를 근거로 한다.

### 4.1 현재 문서와 코드 대조 정정 목록

아래는 향후 해당 architecture 문서에 반영할 정정안이다. 이번에는 기존 문서를 수정하지 않았다.

| 위치/서술 | 현재 코드 기준 | 후속 정정 |
|---|---|---|
| data-contracts의 과거 서버 2/모바일 12 | 현행 server 5/mobile 16, legacy DB 거절·별도 시작 초기화 | 과거 절을 현행 근거로 인용하지 않도록 정정 표 강화. |
| read-only bot/프로세스 내 복원 barrier를 전체 writer 안전으로 해석 | bot가 직접 crawl child 실행; 프로세스 간 barrier 미공유 | bot writer와 모든 subprocess 경계를 writer inventory에 명시. |
| 통계/footer의 raw 가중 평균 지침 | 현재 JS는 그룹별 반올림 평균×count를 다시 평균 | 별도 정확성 결정; 성능 PR에서는 현행 출력을 먼저 보존. |
| 일반 require_fresh가 60초 검증을 항상 강제한다는 해석 | refresh 실패 뒤 TTL 600초 평가로 이전 verified 상태 재사용 가능 | 새 action에 max_age 검증 추가, 탐색 TTL은 유지. |
| rebuild pause=작업 정지 | 현재 상태만 paused로 변경 | cancel 요청/종료 확인/lease fencing 문서화 후 구현. |
| 오래된 완료건/전체 상태 또는 Sunwi 위치 설명 | 현행 통계 정정 절과 코드 우선; Sunwi는 통계 페이지 위젯 | 기존 위치·모집단 보존. 과거 화면으로 옮기는 작업 제외. |

## 5. E01~E35 현재 판정

**확인됨**은 지적의 핵심 조건/분기를 현재 소스에서 확인했다는 뜻이며 실제 공격·교착·자료 손실 재현을 뜻하지 않는다. **해결됨**은 해당 부분 방어가 이미 존재한다는 뜻이다. **미재현**은 재현 증거가 없어 확인됐다고 할 수 없는 부분이다. **추가 확인 필요**는 원인/호환성/부하 효과를 실행 없이 확정할 수 없는 경우다. 한 E에 여러 주장이 있어 주 판정과 부분 판정을 나누었다. 주 판정은 확인됨 34개, 추가 확인 필요 1개(E21)이며, 해결됨/미재현의 부분 판정을 아래에 명시한다. 개별 E 전체가 해결됐다는 결론은 없다.

| ID | 주 판정·근거(S) | 해결됨/미재현/추가 확인 구분 | 구현 후보 |
|---|---|---|---|
| E01 | 확인됨. `file_service.ensure_browser_file`, `resolve_api_file`, `list_api_entries`의 문자열 prefix·경로/링크 경계 | 실제 운영 경로 침해 미재현. browser와 API의 허용 root가 다름 | P01 |
| E02 | 확인됨. `bot.py` command/callback에 chat/user allowlist 없고 child 직접 실행 | configured chat_id는 발신 설정일 뿐 접근 허용 검사가 아님. 외부 악용 미재현 | P03 |
| E03 | 확인됨. duplicate `_safe_read`가 예외를 []로 바꾸고 refresh가 group/member를 재작성 | decision 테이블까지 모두 삭제한다는 해석은 부정확. 현재 transaction/decision 보존은 유지 | P02 |
| E04 | 확인됨. base의 첨부 anchor/image 및 devices 접속 metadata의 HTML 문자열 조합 | device key **이름 생성**은 이미 textContent 사용(해결됨 부분). video data attribute 일부 escape 존재. exploit 미재현 | P04a |
| E05 | 확인됨. API duplicate 동적 POST가 bulk-status보다 앞에 선언; JSON 객체 확인 전 `.get` 경로 | 웹 manage/{id}/update는 bulk와 경로 구조가 달라 동일 충돌 없음. 실제 HTTP dispatch 미재현 | P05a |
| E06 | 확인됨. start fatal 후 빈 changed로 save/marker/last_sync; manager returncode 무시 | 완료 marker 소비 자체를 제거하면 확장 계약 깨짐. 실제 실패 job 관찰 미실행 | P06 |
| E07 | 확인됨. 일반/선택 start의 process→broadcast→watcher 순서 | **start_rebuild는 watcher 선예약/bind와 broadcast 예외 처리가 있음**. pending launch의 기존 reservation/recovery도 해결된 부분 | P06 |
| E08 | 확인됨. rebuild.pause 상태만 변경, startup에서 유효 lease 건너뛴 뒤 만료 재시도 없음 | **start는 heavy pipeline 전에 영속 job 생성함**. no-job 주장은 해결됨. HTTP heavy work·실제 pause 문제는 남음 | P08b |
| E09 | 확인됨. preflush count scope와 uploader `_CTX_FILTER`의 project/contributor/connection/grant/blocked scope 불일치 | `_sendable_count` 기존 함수 재사용 가능. upload_status wire 의미 변경 금지 | P08a |
| E10 | 확인됨. require_fresh→refresh→evaluate가 실패 때 TTL 600초 cache를 허용 | navigation의 cached gate 허용은 기존 의도. 새 작업에만 strict freshness | P08a |
| E11 | 확인됨. `login_mysafety` false 반환이면 attempts 증가 없음 | 무한 실행 실제 재현 안 함. exception retry도 총 deadline 필요 | P09a |
| E12 | 확인됨. `crawl_titles` 첫 페이지 재요청, pages_ok 중심 완전성 판정 | 정상 total=0을 실패로 바꾸지 않음. 중복 ID/짧은 page/변경 total은 추가 fixture 필요 | P09a |
| E13 | 확인됨. prime guard 안 add_done_callback→즉시 `_finish_prime`가 같은 lock; header length와 tmp ready 분리 없음 | stale callback의 future 제거 guard는 있으나 error 갱신까지 fenced 아님. 실제 deadlock·stream race 미재현 | P10a/b |
| E14 | 확인됨. rating 단독 예약 없음, POST timeout 후 remote 성공 여부 unknown 처리 부족 | **site 사전/사후 확인·known-response posted flag·DB 저장 실패 시 success 금지는 이미 존재**. 무조건 재POST 주장은 축소 | P07 |
| E15 | 확인됨. `_build_stats_tables`의 여러 group마다 mask/date/rating/금액 반복 계산 | **get_stats_page 1회 로딩, 기관표 distinct code/name, 추정금액 unique 조합×weight 계산은 이미 있음** | P12 |
| E16 | 확인됨. duplicate route가 group 조회 두 번, 표시용 inventory lookup이 전체 hash 계산 | 전체 refresh 자체를 제거하지 않음. 단일 요청 조회/대상 hydrate부터 | P11a |
| E17 | 확인됨. resolve ID map/get_unrated/watchlist/editor의 전체 읽기 후 대상 필터 | 실제 N건 부하 수치 없음. public 레코드 필드/순서 유지 | P11a |
| E18 | 확인됨. SQL `처리상태 != 취하`는 NULL 제외, pandas fillna는 포함 | 다른 모집단 결과는 정적 추론; fixture 결과 미실행. intended correctness 변화 분리 | P05b |
| E19 | 확인됨. 8-entry shared LRU lock 아래 deepcopy, single flight 없음, 계산 후 generation 재검증 없음 | DB/WAL/community/registry/date/settings signature는 이미 있음. inode replacement의 probe 동작 추가 확인 필요 | P13 |
| E20 | 확인됨. async version/community token route에서 sync 외부 I/O | 기존 threadpool 사용 중인 다른 action은 유지. event-loop 지연 수치는 미측정 | P14a |
| E21 | 추가 확인 필요. `templating.py cache_size=0`와 Python3.14/Jinja 원인 설명 존재 | unhashable cache-key 원인과 cache 활성화의 실제 호환성/이득 미재현. 값만 바꾸는 구현 보류 | P14b |
| E22 | 확인됨. 목록 전체 JSON, ext.search가 행마다 selector/query 파싱 | deferRender는 DOM 비용 일부만 줄임. full-data search/CSV를 page 일부로 대체 금지 | P15 |
| E23 | 확인됨. stats.js 모든 표 eager init, footer DOM row/attr 반복 | raw weighted average로 바꾸는 일은 별도 의미 변경. 현재 rounded-group 결과 먼저 보존 | P16a |
| E24 | 확인됨. stats-loader가 stats page 전체 교체하고 script를 다시 로드 | mapCard 보존은 이미 있음. listener 중복/focus/flatpickr 실패는 미재현(브라우저 확인 필요) | P16b |
| E25 | 확인됨. checkRange 날짜 문자열 비교에 날짜-only 종료값과 timestamp가 섞임 | 통계 SQL 끝 23:59:59의 소수초/다른 포맷 영향은 추가 fixture 필요. DB 문자열 변환 안 함 | P05b |
| E26 | 확인됨. map list 링크의 범용 agency/exact/reproducibility/location 전달이 불완전, `/data/all` law/agencyExact 전달 차이 | **category agencyKey/lawExact/status 전달과 loader의 listReproducible 판정 일부는 이미 있음** | P05c |
| E27 | 확인됨. settings uploadJson은 fetch 완료를 성공으로 간주; device key 실패 처리 부족 | backend의 정상 redirect는 현재 계약. 모든 응답 JSON 강제는 잘못된 수정 | P05c |
| E28 | 확인됨. rating 로그 textContent 누적·초기 전체 파일 읽기·연결 수명 부족 | **crawl 로그 bounded 2,000줄/256KiB와 초기 64KiB는 해결된 부분**. 모바일 로그 parsing 유지 | P17a |
| E29 | 확인됨. WS gather deadline/queue 없음, thread bridge future 방치/API client TTL 없음 | **WS disconnect metadata 정리는 이미 있음**. pong timeout은 주석과 구현 다름 | P17b |
| E30 | 확인됨. settings direct write/shared mutable snapshot, 일부 form CSRF verify 부재 | community JSON CSRF 검증은 있음. JSON verifier를 그대로 multipart에 적용하면 안 됨 | P04b |
| E31 | 확인됨. memory ZIP/Zip64 비활성, 일부 파일 실패 skip·동일 basename | API single-file temp snapshot 정리 경로는 보존. 오류 처리 의미 변경은 명시 | P01b |
| E32 | 확인됨. enqueue_reports의 단건 append 반복·fsync, uploader batch payload 단건 SELECT | **현재 append의 디스크 실패 시 메모리 rollback/atomic save는 있음**. fsync 제거는 금지 | P11b |
| E33 | 확인됨. Sunwi 다층 retry, uploader run별 간격 기준 reset, manifest cursor/lease heartbeat 빈틈 | **drain의 upload 전/401 retry lease renewal은 이미 있음**. actual remote 요청 실험 없음 | P09b |
| E34 | 확인됨. enabled Sheets import 외부 client 초기화, clear→chunk write retry, notifier 부분 전송 뒤 fallback·무기한 child | Sheets atomic commit 지원 여부 추가 확인 필요; partial/unknown 결과의 실제 원격 재현 금지 | P18 |
| E35 | 확인됨. browser pagination은 page index/행 교체 assert 부족, CSV는 내용 비교 부족 | 기존 24-row/50page fixture로 다음 page 검증했다고 할 수 없음. CSV header 변수만 수집해선 데이터 보존 증거 아님 | P00/P19 |

### 5.1 추가 발견과 보류

| ID | 발견/근거 | 기본안·보류 |
|---|---|---|
| N01 | query `_get_records_from_table` broad exception→[], stats `_read_stats_frame` OperationalError→empty/available_years 실패 continue가 정상 0건처럼 보일 수 있음(S) | P02에서 destructive refresh 우선 방어. 다른 read 경로는 typed failure를 내부 반환하고 public error adapter 마련; 부분 통계=정상 전체 통계로 게시 금지. |
| N02 | stats raw/entry 읽기와 duplicate projection이 별도 연결/transaction 흐름; cache key 계산과 compute의 generation 간격(S) | P13에서 동일 read transaction/connection으로 snapshot 구성, replace 또는 writer generation 변경 시 결과 publish 취소. 실제 혼합 snapshot 발생은 미재현. |
| N03 | 문서의 process-local barrier/bot 역할/옛 schema·통계 설명이 현재 코드 일부와 불일치(S) | §4.1 정정 표. 기존 구현을 과거 의도만으로 되돌리지 않음. |
| N04 | upload_status의 latest upload_runs 표시가 count scope와 다르게 읽힐 여지(S 부분) | 계정 전환 fixture로 표시 누출/오해 여부 추가 확인. 범용 내부 scoped view를 제안하되 wire count를 임의 재정의하지 않음. |
| N05 | feature-matrix의 CR-03 action에 인용되지 않은 쉼표가 있어 14열 header 대비 15열이 됨. SH-12는 서로 다른 기능 두 행에 중복됨(S) | 기존 CSV는 보존. 새 부록은 source row/raw cells를 기록하고 CR-03 action만 재조립해 removed/모바일410을 연결. SH-12는 행 순번으로 구분. 원본 표 정정은 후속 문서 PR. |

## 6. 구현 순서와 PR 단위 설계

**공통 적용:** 아래 신규 모듈/함수/테스트 이름은 모두 **제안 신규**, 지금 존재하거나 작성된 파일이 아니다. 테스트는 product 함수 import 전에 임시 `SAFETYREPORT_DATA_DIR`, fixture runtime, 외부 요청/child 차단을 설정한다. HTTP session/API key/gate/protocol은 정상 fixture로 통과하고 auth bypass를 넣지 않는다. browser는 fixture_server만 사용한다. 각 PR은 관련 feature CSV 행의 계약검사와 실패 경로를 통과한 후 독립 검수한다. 5파일/800줄 부근 또는 고위험 경계마다 검수하고, a/b로 적은 변경은 별도 PR로 나눈다. 기존 DB를 교체하는 rollback을 하지 않고 코드 revert+원자 저장한 자료 유지가 기본이다.

의존성: P00 → P01/P02/P03/P04/P05 → P06/P07/P08/P09/P10 → P11/P12 → P13/P14 → P15/P16/P17/P18 → P19. 병행 구현 권한을 뜻하지 않는다. P07의 최소 rating 예약은 P06의 공통 접수 경계 뒤, P08b는 P06 terminal fencing 뒤, P12는 P05 population oracle 뒤, P13은 P11/P12 query snapshot 뒤다. P10a deadlock 단독 수정은 다른 성능 PR 없이 먼저 가능하다.

### 6.0 전체 범위의 남은 모듈과 변경 경계

전체 tree를 리팩터링한다는 이유로 이미 분리된 모듈을 모두 다시 옮기지 않는다. 아래는 E 항목 외에도 영향 확인이 필요한 경계다. S/N 수준 파일을 실제 변경 대상으로 승격할 때 P00에서 해당 구현·호출자·테스트를 집중 열람하고 이 계획에 파일/함수를 추가한 뒤 구현한다. 현재 정적 inventory만으로 그 내부 동작이 모두 검증됐다고 하지 않는다. 모르는 로직은 삭제 대상이 아니라 계약 확인 대상이다.

| 계층/기존 파일군 | 담당 단계·기본 개선 경계 | 선행 oracle/보류 조건 |
|---|---|---|
| `services/data_service.py`와 query/stats facade | P11/P12/P13의 기존 진입점·외부 adapter 보존; thin facade는 유지 | 모든 service_symbols 호출자와 public 전체 레코드 golden. facade 제거가 import/frozen 경로를 깨면 보류. |
| `core/database/*`, `core/storage/*` | P02/P06/P11/P13의 read transaction·writer/restore 경계만 좁게 변경 | raw/override/entry/merge·첨부 만료·watch flags·변경 판정, 현행 refusal와 sqlite backup 테스트. exchanged schema/ownership 변경은 양쪽 승인 전 제외. |
| `core/parser/*`, `core/processing/*`, `services/fine_estimate.py`, 사진/EXIF 관련 | P09의 completeness 입력/P12의 순수 파생값 재사용. 기본 parser/상태·추정 규칙 변경 없음 | parser/EXIF/rating/stats vectors, 날짜/문자열/촬영수/unknown golden. 새 규칙이나 원문 정규화는 별도 의미 결정. |
| agency registry·`contracts/`·generated JSON | P12 distinct-pair lookup/P13 manifest invalidation; 현행 코드/명칭/agency_key 그대로 | 현존/승계/alias/동일명 다른 코드/(구)/미확정 키·raw field 유지. 공식 자료 갱신은 이번 성능 리팩터링에 포함하지 않음. |
| `geocode_service`, maintenance와 지도 UI/API | P13 snapshot/P16 loader 수명/P19 startup 격리 | 공식 좌표/저장 좌표 불변, display cluster centroid를 DB에 쓰지 않음. backfill 재개·복원 동시성·missing groups·bounded metadata oracle. |
| scheduler/direct login/auth/account services | P06 접수/P08 owner-grant scope/P09 retry/P19 lifespan | 설정 재로드 중 이중 스케줄·계정 전환·대기 인증 poll·token invalidate·cancel/join. 인증/consent/delete/takeover 권한과 기존 수동 확인 유지. |
| community store/policy/ingest/upload-control | P08/P09/P11의 기존 계약 위 내부 scoped view·batch 조회 | canonical JSON/observation/envelope/ACK/epoch/revision/superseded/control-blocked/quota 벡터. scope가 불명확하면 실행 거절, pending 자동 삭제 금지. |
| `main.py`, runtime helpers, router middleware | P03/P06/P14/P17/P19의 lifecycle owner·blocking 경계 | import 부수효과 audit, fixture guard가 네트워크/운영 DB보다 먼저 동작하는지, resource/subprocess mode/종료순서 검증. |
| 템플릿/JS/CSS/vendor·폰트/아이콘 | P04/P15/P16의 기존 DOM·theme/storage와 P19 resource 포함 | 공통 셸 소유자 1명; stack/visual renewal scope 추가 금지. vendor 일괄 업그레이드·asset 삭제는 필요 증거 없이 하지 않음. |
| 테스트/개발 스크립트/CI·패키징·문서 | P00/P19에서 실제 assertion·안전 fixture·플랫폼 evidence 강화 | 기존 사용자 테스트/preview config 보존. CI/release/VERSION 변경 별도 승인. 문서 변경은 실제 작업만 CHANGELOG, 구조는 architecture 정정 표. |

이 경계의 지연/부하 문제가 측정되면 해당 PR에서 phase 단위로 다룬다. 측정되지 않은 모든 파일에 "성능 개선" 커밋을 만들거나 대형 파일 분리를 완료 조건으로 삼지 않는다.

### P00 — 계약 oracle와 검증 설계 고정

- **파일/함수:** 기존 `tests/test_storage_contract.py`, `scripts/dev/db_roundtrip_check.py`, `scripts/dev/logic_parity_check.py`, `contracts/*vectors*`, `tools/web-tests/specs/list-interactions.spec.ts` 및 CSV 다운로드 spec. fixture builder/계약 snapshot assertion은 제안 신규. 현재 테스트 약한 assertion을 먼저 강화하되 지금은 실행/수정하지 않는다.
- **현재→개선:** 함수 이름/응답 존재/클릭 성공 중심 증거 → 필터 후 ID集合·순서·필드/타입·실제 CSV bytes·페이지 행 변화·모든 교환 셀을 비교하는 oracle. 현재 결함을 별도 expected-failure로 적고 다른 assertion을 완화하지 않는다.
- **선행 테스트:** 101행/50page의 3페이지 왕복, 검색과 선택 상태의 page 이동, 다운로드 header+CSV parser/BOM/따옴표/줄바꿈, 인증/protocol/gate 음성 케이스. DB tricky-values와 양쪽 schema 열 차이 검출. 전체 stats JSON의 golden(성능 PR), 의도 버그 수정 기대값은 별도 golden.
- **측정/게이트:** 새 synthetic 규모별 고정 seed/fixture checksum 기록, baseline 미측정 칸 채우기. 기존 helper가 runtime 부수효과 없는지 읽고 실행 승인 뒤 사용. 모바일 코드·환경 없으면 실제 왕복 gate blocked; 서버 정적 계약 gate와 분리. 테스트 skip을 완수로 세지 않음.
- **대안/롤백:** old passed 수치로 oracle를 대신하지 않음. test-only PR revert 가능, production/DB 변동 없음.

### P01a/b — 경로 보호와 ZIP 수명(E01/E31)

- **파일/함수:** `services/file_service.py`의 ensure_browser_file/resolve_api_file/list_api_entries/delete*/build*_zip, `web/routers/file_browser_route.py`/`api_route.py` 파일 endpoints. 공통 `_resolve_allowed_file`와 `ArchiveArtifact`는 제안 신규.
- **현재→개선/조건:** 브라우저·API download/delete에 sibling-prefix·`..`·symlink·root 교체 → 경로를 OS 방식으로 정규화하고 해당 기능의 명시 root 내부 파일인지 commonpath/resolve로 검사, root 자체/보호 파일/활성 로그를 검증. API는 logs/results 하위만, browser의 기존 허용 그룹은 유지한다. 기본 symlink/junction 거절, 꼭 필요하면 root 내부 링크만 별도 정책. Linux TOCTOU는 dirfd/O_NOFOLLOW 가능한 경로 적용; Windows는 final path/reparse 검사와 검증 후 핸들 사용의 플랫폼 증거 전까지 동등 보호 완료를 주장하지 않는다.
- **ZIP 흐름:** memory full-copy/skip → 먼저 모든 선택 경로·중복 arcname 검사, 불가 항목 있으면 전체 요청 명확 거절. streaming spool/temp ZIP+Zip64, 상대경로 arcname으로 충돌 방지. 완성 ZIP만 FileResponse, 완료/실패/disconnect/lifespan에서 임시 파일 청소. 활성 log는 기존 snapshot 방식. ZIP64/파일명 변화는 다운로드 golden에 명시한다.
- **선행 테스트:** sibling root, encoded traversal, UNC/drive/case/separator, symlink/junction/하드링크 정책, rename 경쟁, 보호 로그 aliases, same basename, 읽기 실패, empty selection, 취소/중단, >4GiB 메타/작은 mock stream. 기존 API status/payload와 웹 오류 메시지 확인.
- **성능/완료:** 10/100/1,000 synthetic 파일의 peak RSS/temp bytes/압축 시간, syscall/path check 비용. RSS가 총 ZIP 크기에 비례하지 않을 것, temp 누수 0, 모든 항목 bytes 일치, 탈출 접근/삭제 0. 성능이 느려도 경로 검증 완화 금지.
- **대안/롤백:** 문자열 startswith/RLock·단순 normpath만으로 안전 주장 기각. a는 resolver만, b는 archive 전달을 변경. b revert 시 새 temp 정리 먼저; 취약 resolver로 되돌리는 revert는 사용하지 않고 해당 action 임시 거절을 선택한다.

### P02 — read 실패와 파괴적 재생성의 분리(E03/N01)

- **파일/함수:** `duplicate_group_service._safe_read/_load_inventory/refresh_duplicate_groups`, `report_query_service._safe_read/_get_records_from_table`, `report_stats_service._read_stats_frame/_load_available_years`; `SourceReadResult`와 `DuplicateInventoryError`는 제안 신규. a는 destructive duplicate 경로만, b는 read/UI 오류 adapter다.
- **현재→개선:** 읽기 실패→빈 원천→group/member 재작성 → 필수 원천의 load/검증을 전부 끝낸 후 transaction에서 변경. 0행 정상·필수 table 부재·권한/lock/I/O 실패를 구별. 성공한 전체 빈 inventory는 명시 정책에 따라 rebuild하고, 실패·부분 source는 변경 0/이전 groups 유지. 결정/수동 대표/created_at 등 현행 preservation oracle 유지.
- **선행 테스트:** 각 raw/merge/entry read에 예외, 하나만 empty, truly empty, lock timeout, mid-write failure, 삭제 직전 실패를 주입해 이전 DB byte/행값 상태 비교. query/stats read failure를 정상 0건 또는 성공 CSV로 알리지 않는 assertion. JSON 오류 외부 계약은 status/code/detail adapter로 고정.
- **측정/게이트:** refresh phase 시간/읽기 횟수/peak RSS/transaction duration; 같은 snapshot golden hash. 자료 보호 test 전부 통과하면 완료. 부분-read 속도 개선은 채택 안 함.
- **대안/롤백:** catch-all 제거로 서버 전체 crash를 유도하지 않음; 오류를 명확히 전달. fail-open 빈 목록 revert 금지, 실패 시 refresh endpoint만 일시 거절 가능. schema/data migration 없음.

### P03 — bot 권한과 공통 접수(E02)

- **파일/함수:** `bot.py` start/help/button/검색/export command, `main.py.lifespan`, `crawl_control`, `runtime_mode`; `authorize_bot_update`/bot task adapter는 제안 신규.
- **현재→개선:** token만으로 update 수신, 개인자료 반환과 child 직접 시작 → 모든 command/callback의 최전방에서 configured private chat ID와 user ID 확인. 기본 허용은 설정한 private 계정, group 채팅은 명시 user allowlist가 없으면 거절. callback은 서명/서버 보관 선택으로 권한 재검사하고 자료를 log에 남기지 않음.
- **접수 기본안:** 최소 권한 PR에서는 직접 writer 실행을 막고 차단 사유를 표시. 뒤 PR에서 bot lifecycle를 main의 in-process async task로 통합하여 같은 CrawlManager/gate/restore/job admission에 위임한다. bot export는 공통 읽기·내보내기 서비스로 한정. polling blocking I/O는 worker, 종료 때 await/cancel한다. 별도 프로세스 격리를 유지해야 하면 인증된 loopback IPC가 대안이지만 새로운 public API/키 노출 설계를 승인받기 전 만들지 않는다.
- **선행 테스트:** unknown/private/group user, callback replay/expired selection, chat/user 혼동, gate loss/restore/rating active 상태에서 start 거절, frozen mode/lifespan shutdown, bot error가 main 작업 예약을 누수하지 않는지 mock 테스트.
- **측정/완료:** update→접수 p95, thread/child 수·반복 start 수; unauthorized 자료 반환/작업 시작 0, 한 writer 예약, shutdown 누수 0. 실제 Telegram 전송은 별도 운영 승인 전 blocked.
- **롤백:** allowlist 보호는 유지; 통합 실패면 bot 시작 기능을 닫고 기존 웹/API 이용. bot 단독 child writer로 자동 fallback하지 않는다.

### P04a/b — DOM/CSRF/설정 원자성(E04/E30)

- **파일/함수:** a `web/templates/base.html` 첨부 생성, `devices.html` metadata 렌더, 관련 공통 JS; b `core/utils/csrf.py`, `settings/settings.py.AppSettings.load/update_config/save`, `settings_route.py`/form mutation routes. form verifier와 immutable settings snapshot/save(expected_generation)은 제안 신규.
- **현재→개선:** 문자열 HTML 삽입 → createElement/textContent/setAttribute, URL scheme/host/context policy를 별도 검증. 이미지·영상 원격 허용은 현재 remote validation 정본과 맞추고 일반 다운로드/링크까지 일괄 같은 host로 제한하지 않음. CSV formula escape는 export 경계에서 정책 결정, 원문 DB 수정 안 함.
- **설정/CSRF:** 기존 JSON community verifier 유지, session form/multipart용 Origin/token verifier 추가, API key 인증 요청은 cookie CSRF 경로와 분리. loader는 fresh ConfigParser로 읽고 삭제 key가 snapshot에 남지 않게 함. process lock+다중 writer 파일 lock 안에서 generation 검사·temp flush/fsync/atomic replace 후 snapshot publish. 외부 side effect는 lock 밖, publish 실패 시 기존 snapshot 유지. secret masking 유지.
- **선행 테스트:** 첨부/device 이름에 quote/tag/javascript/data URL, keyboard DOM 정상성, CSP가 없어도 text로 처리; 정상 폼/CSRF 누락·잘못된 Origin·multipart/API client, 두 writer 경합, disk full/replace failure/truncated ini/삭제 key/frozen readonly path. logout 등 기존 destructive GET의 method 전환은 별도 compatibility 결정 후 진행.
- **측정/완료:** config save latency와 concurrent snapshot 일관성; authorized 정상 작업/거절 payload 보존, 부분 ini 0·lost-update 명확 거절, 악성 문자열 실행 0. a/b는 변경 파일을 작게 분리하고 공통 셸 소유자를 하나로 둔다.
- **대안/롤백:** 모든 innerHTML 무조건 제거·JSON verifier 복붙·ConfigParser lock만 추가는 불충분. renderer revert 가능하나 취약 sink는 action disable. settings 실패 시 보존한 config 사본으로 복구하며 사용자 최신 값을 덮어쓰지 않음.

### P05a/b/c — route/body·모집단·필터/응답 정확성(E05/E18/E25/E26/E27)

- **파일/함수:** a `api_route.update_duplicate_group_api/bulk_update_duplicate_group_status_api`, community/기타 JSON endpoint; b `report_query_service._build_records_query/_filter_withdraw`, `report_stats_service._build_stats_query`, `data_table.html.checkRange`; c `web/routers/data.py.view_all/_build_filters/_identity_filters`, `report_map.html` listParams, `stats-loader.js`, `settings.html.uploadJson`, `devices.html.createKey`. shared filter serializer/response reader는 제안 신규.
- **현재→개선:** a literal bulk route를 동적 route 앞에, JSON object와 각 field type/범위/list count를 `.get` 전에 검사. 기존 URL과 응답 유지. b SQL/pandas/브라우저의 NULL·날짜 끝 경계를 같은 intended 규칙으로 맞추되 raw/canonical/lifecycle의 축과 대표건 적용 순서를 바꾸지 않음. 기본 NULL status 포함, 날짜-only 종료일은 parsed local date≤end, 시각 필터는 별도다. persisted timestamp 재작성/UTC 강제 변환 금지.
- **필터 기본안:** c category/all 링크 모두 agency+agencyExact+agencyKey/law+lawExact/status/location/date/dedupe/exclude를 명시 serializer로 전달. 현행 AND/OR 문법, exact와 like의 `%/_` 처리 차이는 golden 검증 후 결정. PC 목록으로 재현 불가능한 조건이면 listReproducible=false로 링크 비활성 및 이유 표시. 조용히 필터를 버리지 않는다. 응답 adapter는 r.ok→content-type/redirect별 성공 조건을 확인, 실패를 성공 alert로 표시하지 않음; 중복 submit 방지/버튼 복구.
- **선행 테스트:** literal bulk authenticated dispatch와 invalid JSON scalar/list/null/empty/large payload; NULL/''/취하/공백/종료일 00:00·23:59:59·소수초/한글날짜, exact/AND/OR/regexlike, all/category/지도링크의 ID集合·order 비교. HTTP409/500/timeout/정상 redirect200/JSONerror 시 성공표시 여부 확인.
- **측정/완료:** SQL plans/row count·filter draw p95·wire payload 비교. a 라우팅·body, b 의도 수정, c 링크/UX 각각 독립 golden. old NULL/date 오류에 맞추어 oracle를 완화하지 않고 기대 변화만 문서화. 모바일 의미가 바뀌는 공통 필터면 양쪽 검증 전 merge 보류.
- **대안/롤백:** 새 endpoint나 API field rename 불필요. a revert 가능, b는 이전 predicate로 revert하되 재발 이슈 명시, c는 재현 불가능 링크를 비활성. 데이터 변환 rollback 없음.

### P06 — 크롤 접수·watcher·outcome 수명(E06/E07)

- **파일/함수:** `crawl_control.start_crawl/enqueue_report/enqueue_reports/_prepare_after_crawl_hook`, `crawl_manager.start_crawl/run_after_crawl/launch_pending_crawl/stop_crawl`, `start.main/_process_and_save_results`, `main.py` frozen dispatch, scheduler/bot adapters. `JobAdmission`/`RunOutcome`와 internal outcome sidecar는 제안 신규.
- **현재→개선:** 일반 start에서 process 시작 뒤 broadcast/후처리 등록 → gate/restore/conflict 점검과 job/attempt 예약→watcher 준비→Popen bind→lock 해제→best-effort broadcast. spawn/등록 실패는 예약/큐 파일 정리; 프로세스가 살아 있으면 종료 확인 전 예약 유지. 기존 pending reservation과 start_rebuild의 선등록을 재사용한다.
- **outcome 기본안:** 내부 `accepted→starting→running→postprocessing→succeeded|partial|failed|cancelled|unknown`으로 기록. 자식은 필수 stage/저장 건수/오류·cancel 이유를 atomic outcome sidecar에 남긴다. exit 0만으로 성공이 아님; nonzero/누락/attempt mismatch는 success 금지. valid 부분 저장은 보존하고 부족 목록만 기존 큐 recovery에 반환. 완료 callback은 job+attempt 기준 exactly-once 로컬 terminal 처리, 외부 exactly-once 보장은 주장하지 않음.
- **marker/last_sync:** 확장/mobile이 소비하는 기존 done/new-ID 형식은 adapter 유지. 실패 종료 알림도 terminal이지만 완료 성공 문구와 분리. last_sync는 성공한 의미만 갱신하도록 계약 결정; 실패 시 내부 last_attempt 기록. 공유 후처리 실패·export 실패는 개인 수집 성공과 분리 표시하고 종료 정책을 명시한다. timestamp semantics의 모바일 영향은 소비자 확인 gate.
- **선행 테스트:** 종료가 watcher bind보다 빠른 fakeprocess, broadcast failure, spawn failure, stop during spawn/post-upload, fatal login/list/detail save/last_sync write 오류, kill 뒤 살아 있음, duplicate callback/stale attempt, restore start 경쟁, pending settlement와 unresolved recovery. 기존 stop 실제 종료 확인/실행별 queue/temp cleanup tests를 보존.
- **측정/완료:** admission lock hold p95·접수 응답·terminal 지연·후처리 queue 수; 모든 process는 watcher 1개/terminal 1개 또는 unknown recovery, failed 성공표시 0, pending 손실 0. acceptance는 heavy flush를 오래 수행하지 않게 예약/실행 경계를 분리한다.
- **대안/롤백:** 모든 background를 asyncio로 재작성하거나 marker를 없애지 않음. 내부 sidecar는 무스키마 기본. 새 예약 읽기 지원을 먼저 배포 후 사용, revert 시 active jobs 종료 확인·새 journal 보존; 기존 성공 오표시 경로 자동 fallback 금지.

### P07 — 별점 상호배제·미확정 결과(E14)

- **파일/함수:** `rating_service.start_batch_rating/_run_rating_worker`, `star_rating_service.run_batch_rating/confirm_success/save_site_values`, rating route/API, P06 admission. `RatingOperationState`는 제안 신규, exchanged DB 필드 추가 제외.
- **현재→개선:** crawling만 확인→thread, POST timeout을 단순 실패로 해석 → 같은 scope의 crawl/rebuild/restore/rating 공통 예약. target별 prepared/submitting/confirmed/failed/unknown 상태를 서버 로컬 journal에 저장. POST 응답 유실이면 unknown→remote readback, remote 아직 0이어도 즉시 재POST하지 않음. bounded 확인 후 unresolved로 남기고 새 사용자 retry 때도 journal 조회. known successful response의 기존 posted flag/site 확인/DB save 보호는 유지.
- **선행 테스트:** 동시 rating 둘, rating↔crawl/restore 양방향, request accepted response-lost/slow remote visibility, known success 후 DB failure, 401/gate loss/cancel/재시작, unchanged already-rated/skip vs success 집계. 원격 전송은 fake transport.
- **측정/완료:** target resolve query·예약시간·readback 횟수·elapsed; unknown 성공수 0, ambiguous 자동 반복 POST 0, 확인된 값만 DB 저장, rating 실패 뒤 reservation release/로그 terminal 확인.
- **대안/롤백:** POST retry 전체 비활성으로 정상 known failure까지 영구 blocked하지 않음. idempotency key는 upstream 지원 확인 전 전제 금지. journal을 보존하고 새 제출 차단 상태로 revert; DB 저장값 덮어쓰기 rollback 없음.

### P08a/b — strict action gate·scope·rebuild worker(E08/E09/E10/N04)

- **파일/함수:** a `community_gate._Gate.require_fresh/_refresh_locked`, `community_crawl_upload.flush`, `community_uploader._CTX_FILTER/_sendable_count/upload_status`; b `community_rebuild.start/_continue_pipeline/pause/resume/resume_on_startup/on_crawl_finished/_commit`, `community_store` job lease helpers, lifespan. scoped upload view/worker loop/attempt fence는 제안 신규.
- **현재→개선:** a 탐색 TTL600을 새 action까지 reuse → action은 refresh 후 verified_at age≤60초(현행 freshness 의도), scope/owner/grant 유효성을 별도 판정. 실패 refresh와 오래된 verified면 새 작업 거절, navigation cached 표시 유지. preflush는 현재 `_CTX_FILTER`와 동일한 sendable/auth-blocked scope의 잔량을 확인; 다른 프로젝트/옛 grant blocked rows는 현재 성공을 막지 않되 별도 상태로 보존. wire pending의 의미는 유지.
- **rebuild:** 영속 job이 이미 먼저 생기는 구조 유지. HTTP는 accepted job/status만 반환하고 heavy backup/manifest를 managed worker로 옮긴다. pause는 요청 상태→cooperative cancel/child stop→실제 종료 확인→lease 해제/paused 확정; DB state만 바꾸지 않음. job/attempt token 없이 item/commit 불가. scope는 required_version/local_dataset/source_account_namespace; 계정 변경·fresh install baseline·accept gaps 명시 확인은 유지. startup 유효 lease는 존중하고 만료 시점 후 bounded scheduler로 다시 판단, 유효한 다른 worker job을 무조건 재시작하지 않음.
- **선행 테스트:** verified age0/59/61/599/601+network failure, crossproject/oldgrant/newaccount outbox, invalid ACK scope, duplicate start, backup/manifest 중 crash, pause 후 늦은 callback/commit, heartbeat lost/restart leaseexpired, fresh install/no reports/사용자 reject gaps. account takeover/restore dataset rotate 시 old jobs fencing.
- **측정/완료:** start HTTP p95, backup/manifest/lease duration, skippedscope count; 60초 초과 새 action 0, pause 이후 late writer commit 0, 하나의 active attempt, 유효 lease 동안 이중 worker 0, orphan 복구 가능. 실제 safeauth/공유 호출 not-run, mock 계약과 실제 운영 검증을 구분.
- **대안/롤백:** TTL 자체600→60으로 줄여 탐색을 깨지 않음. 모든 pending을 flush/삭제하지 않음. schema 변경 필요 시 양쪽 승인 gate. worker revert는 새 시작 막고 cancel/join 후 job 보존; journal/items 삭제 금지.

### P09a/b — 로그인·목록·외부 deadline(E11/E12/E33)

- **파일/함수:** a `core/crawler/login.login_mysafety`, `crawltitle_api.crawl_titles/_fetch_api_page`, title pipeline/start list outcome; b `sunwi_fetcher.build_session/fetch_stats/collect_statistics`, `community_uploader._drain/refresh_server_completed/stop_background`, direct-login retry. `RetryBudget`/manifest heartbeat/cursor guard는 제안 신규.
- **현재→개선:** false login이 retry count 미소모 → 최초1+maxretry 유한 for, monotonic 총 deadline·cancel·명확 terminal. 목록 첫 payload를 page1로 재사용, page별 성공+expected ID/unique coverage+마지막 page validity+total 변동 판단. total0 정상, 페이지 중복/short/total변동이면 bounded 목록 재시도 또는 partial; 실패 목록으로 원래 자료를 지우지 않음.
- **retry 기본안:** adapter retry와 helper retry의 곱을 없애고 budget owner 1개, connect/read/deadline/cancel 분리. Sunwi는 실패 region만 bounded 재시도, 이전 성공 scope 보존. uploader 기존 UC1 25요청/90초와 MIN_INTERVAL1.1초를 run 경계에도 적용(서비스 scope별 monotonic ledger); 계약보다 공격적인 retry 금지. manifest는 page 전 lease renewal, max elapsed/page count/repeated cursor guard; lease loss면 기록 중단. 401 retry lease renewal 기존 방어 유지.
- **선행 테스트:** false/exception login·fakeclock, total0/1/200/201/401·중복/짧은page/429/totalchange, multi-run interval/leaseexpiry, repeat cursor/cancel/hungread, join timeout 뒤 실제 thread 생존. 실제 site/Sunwi/공유 호출 차단.
- **측정/완료:** 요청/attempt 수·page parse 시간·elapsed worst case·coverage; budget 초과 자동 요청 0, completed false-positive0, pause/stop deadline 후 생존 여부 명시. download/parse와 network retry 시간을 따로 측정.
- **대안/롤백:** retry=0 일괄 적용으로 정상 transient recovery 제거 안 함. 성공 전체 목록의 semantics 유지, retry ledger 호환 없이 구형 worker를 중복 실행하지 않음. 차단 상태/이전 완료 자료 보존 후 코드 revert.

### P10a/b — media callback와 stream ready(E13)

- **파일/함수:** `media_proxy_service.prime_cache/_finish_prime/open_stream/iter_stream/ensure_cached/cleanup_cache`와 media route. `PrimeState`/ready Event는 제안 신규.
- **현재→개선:** a future를 guard 아래 등록하고 callback은 guard 밖에 추가, 즉시 완료 callback도 동일 구조. callback은 future/generation이 current인 경우만 error/progress 정리. b total header와 tmp-open/flushed-readable byte를 분리, ready/error/cancel 상태를 기다리고 descriptor 수명 보호. 예상 length만 보고 없는 tmp를 읽지 않음. 기존 URL validation/cache key/인증/Range206/Content-Type 유지.
- **선행 테스트:** 이미완료 future·callback 즉시실행·실패후재시도 stale callback, header-before-open, empty body/truncate/redirect/range/cancel, source timeout, cleanup during read, 여러 reader. race는 barrier/event로 결정적으로 강제하며 sleep 타이밍 의존 금지.
- **측정/완료:** cache hit/miss TTFB·다운로드throughput·open fd/worker/progress entry 수; deadlock0·없는 tmp read0·길이/Range/bytes 정확·reader 종료 뒤 누수0. fallback 전체 ensure_cached 시간도 측정해 TTFB 실패를 숨기지 않음.
- **대안/롤백:** lock만 RLock으로 바꾸는 수정은 ready/lifetime를 해결하지 못함. a 단독 먼저, b 실패 시 completed-cache-only 스트림으로 보수 fallback 가능(지연은 명시), remote/partial content를 성공으로 오표시 안 함.

### P11a/b — 좁은 조회·중복 표시·queue batch(E16/E17/E32)

- **파일/함수:** a `duplicate_route.view_duplicate_groups`, `duplicate_group_service.get_duplicate_groups/_load_inventory_lookup`, `report_query_service.resolve_to_report_numbers/get_unrated_records/get_all_watchlist`, `db_editor_service.list_records`; b `crawl_control.enqueue_reports`, `crawl_manager.append_to_pending/_save_pending_locked`, `community_uploader._build_batch`. `hydrate_group_members`/`append_many`는 제안 신규.
- **현재→개선:** 표시 조회 두 번→한 번 조회/필터, persisted group/member ID로 필요한 레코드만 hydrate; 전체 payload hash는 refresh에서만. ID resolve/watchlist/rating는 대상 WHERE IN+필요 컬럼, batch는 scoped front의 payload 한번 SELECT→id map. public full record 출력은 필요 전컬럼 유지. IN chunk는 실제 SQLite variable limit 확인 후 보수512 기본, 순서는 원래 입력/정렬 계약대로 재조립한다.
- **queue:** append N회 fsync→lock 아래 candidate queue 1회 계산/dedupe→atomic durable save1회→성공 후 memory publish. 실패면 기존 memory/disk 그대로. queue limit/중복 번호/예약중 pending·unresolved 순서·재시작 semantics 유지.
- **선행 테스트:** 0/1/513/중복/없는ID/crosscategory, 수동대표/notduplicate/원문변경 oracle, watcher와 batch append 경쟁, diskfull/replacefailure, payloadrevision superseded/ACK 순서. full refresh oracle와 display hydration 필드 값 비교. 증분 duplicate refresh는 후속 별도 PR이며 영향 graph와 full-oracle 동치 확인 전 보류.
- **측정/완료:** SQL query/rows/bytes/plan, target1/20/500 vs DB24/1k/10k/100k, hash 호출·fsync 횟수·lockhold; 단일 작은 target 비용이 전체 hash에 비례하지 않을 것, appendmany durable save1회, exact output/queue preservation.
- **대안/롤백:** select* 금지만으로 public 필드를 누락하지 않음, fsync 삭제/큐를 memory-only로 하지 않음. 좁은 query는 fullquery fallback으로 revert 가능, batch 형식은 기존 JSON과 동일해 data rollback 없음.

### P12 — 통계 파생값 재사용(E15)

- **파일/함수:** `report_stats_service._load_stats_frames/_build_stats_tables/_summarize_overview_frame/_estimated_fine_totals/_apply_registry_agency_display/get_stats_page`와 fine_estimate. `StatsMetricFrame`/group aggregate adapter는 제안 신규.
- **현재→개선:** shared loaded frame 뒤 각 group에서 반복 mask/date/fine/rating → 동일 filtered snapshot에서 raw/canonical/lifecycle/disposition mask, date parsed/valid 완료일수, ratingvalid/sum/count, 확정 amount/unknown, 추정 input key를 한번 만든다. 기관/담당자/법규 aggregation은 동일 numeric frame+명시 denominator를 재사용한다. 이미 있는 unique 추정 조합×weight를 유지하여 full snapshot에서 한번 계산하고 group에는 lookup/sum한다. registry도 distinct pair lookup 유지.
- **의미 보존:** 처리기관·담당자 미지정 제외, 완료건 처리일수, 일부수용/과태료 overlap, 경찰/보완/취하, 추정eligible, raw/canonical/not_duplicate, Decimal(float) HALF_UP의 현재 rounding을 그대로 oracle로 둔다. vectorized sum/count의 순서·float 차이가 output rounding에 영향을 주면 해당 계산은 현행 방식 유지. 성능 PR에서 footer raw평균 의미 수정 금지.
- **선행 테스트:** 24개 edge fixture와 대규모 fixedseed old/new 함수(JSON 전체 키·타입·값·정렬·NaN sanitize) 비교, 모든 category/year/law/agency/exact/dedupe/excludewithdraw 조합; sum/count/denominator와 채점0/NULL, invalid dates, unknown fine, 중복 대표 바뀐 case. actual 서버↔mobile 계산 parity는 별도 gate.
- **측정/완료:** cold full page CPU/wall/parse/mask/unique estimate call 수·SQL·RSS와 warm separately. 표/차트 실제 최종 data-ready p95가 개선되어야 함; shell만 빨라진 것은 완료 아님. 원본 JSON golden 일치와 measured gain 중 둘 다 필요. proposed 목표 cold aggregate CPU 30% 감소, peak RSS≤baseline1.1배; correctness 먼저.
- **대안/롤백:** 모든 행에 estimate 호출로 현재 grouped 최적화를 후퇴시키거나 pandas→SQL wholesale rewrite 안 함. adapter 선택으로 이전 aggregate에 revert 가능, schema/DB 값 변경 없음.

### P13 — snapshot/cache generation·소유권(E19/N02)

- **파일/함수:** `report_cache.cached/clear`와 probes, stats/query의 read/projection 연결, DB engine/exchange replace hooks, registry invalidation. `ReadSnapshot`/`CacheGeneration`/per-key inflight은 제안 신규.
- **현재→개선:** signature lock+deepcopy, unlock compute, oldkey publish → DB/registry/settings/date scope snapshot token을 얻고 동일 read connection의 명시 read transaction에서 raw/entry/duplicate projection 조회. input generation이 바뀌면 결과를 cache publish하지 않고 bounded1회 재시도 또는 uncached result/error(응답 snapshot 일관성 확인 후). inode 교체면 oldprobe 닫고 새 read-only probe. 외부 child write 탐지는 WAL/stat/data_version 유지한다.
- **cache 기본안:** lock은 metadata/inflight/lru 포인터만, freeze된 내부 결과를 hit 시 포인터 획득 후 lock 밖 deepcopy. consumer mutation이 원본에 닿지 않아야 함. 같은 key miss는 singleflight; 실패·cancel은 waiter깨우고 entry 저장 안 함. 기본 8 entries 유지, byte budget는 64MiB provisional로 측정 후 확정, oversized response는 cache 건너뛰고 complete response는 반환. cache family 분리는 writer invalidation coverage 완성 뒤, 지금 broad invalidation 유지.
- **선행 테스트:** consumer mutation, concurrent misses1compute, cancellation/exception, WAL commit/DB inode replace/restore·registry manifest/date rollover/settings switch, compute-midwrite, probe closed, owner/account scope. deadlock lockorder/crossprocess read txn fixture. auth/gate decision cache 금지 확인.
- **측정/완료:** hit/miss p50/p95/deepcopy outside lock wait·flight waiter count·entries bytes/probe fd/RSS; stale generation publish0, mixed snapshot0, allocation eviction bounded, full response/contract unchanged. high concurrency에서 unrelated key head-of-line block 감소.
- **대안/롤백:** 시간 TTL만으로 freshness나 restore 안전 주장 안 함. custom generation을 allwriter 연결 전 신뢰하지 않음. 가장 안전한 rollback은 cache disable/old pure compute, 오래된 캐시 data 재사용 금지.

### P14a/b — async I/O와 Jinja 우회 원인(E20/E21)

- **파일/함수:** a `main.version_latest`, community account/token route의 `_verify_client_user_token`/`_check_client_user`, 관련 client; b `core/utils/templating.py` Jinja env, runtime resource config. immutable request DTO/worker adapter는 제안 신규.
- **현재→개선:** a async 안 syncHTTP → header/token/session 입력을 immutable 값으로 먼저 추출, 기존 bounded threadpool에서 timeout/deadline 있는 sync helper 호출. DB connection/session을 thread 경계로 운반하지 않는다. request cancel 후 external operation unknown 수명은 기록하며 thread가 즉시 취소됐다고 말하지 않음.
- **Jinja 보류 실험:** Python3.14와 실제 pinned Jinja로 TemplateResponse/include/macro/context globals/source/frozen 렌더 mock fixture. unhashable key 원인을 재현해 upstream/source/version evidence 확인 후 key adapter/적정cache가 대안; 재현 못하면 cache_size0 유지. dependency upgrade가 꼭 필요하면 별도 scope/packaging 승인. 단순 cache_size400 변경 금지.
- **선행 테스트:** 느린 fakeHTTP 동안 WS/ping/health loop tick, helper timeout/error/cancel, invalid auth body, rendering route 전체 snapshot·include/global key에 dict 포함. 실제 version/GitHub/account 외부 요청 차단.
- **측정/완료:** loop lag p95/p99·inflight worker·HTTP response·Jinja cold/warm CPU/compilecount. a loop lag 개선+응답/권한 보존, b 원인 재현과 cache 효능 둘 다 확인해야 완료. E21 실험 실패/미확인은 blocked로 남긴다.
- **롤백:** a sync helper 자체는 그대로 worker adapter revert 가능하나 loopblocking route 제한으로 보호. b env 원래 설정으로 revert, resource 차이는 P19 재검증.

### P15 — 목록 filter snapshot(E22)

- **파일/함수:** `data_table.html` ext.search/checkRange/선택/CSV와 table route; 필요 시 filter controller JS는 제안 신규.
- **현재→개선:** draw 각 row마다 jQuery selector+AND/OR 파싱→적용 버튼/입력 이벤트에서 draft→applied snapshot을 생성하고 draw 시작에 typed predicate를 한번 준비. 기존 direct URL init/applied-vs-draft/time/location/exact syntax와 DataTables ordering/search/export allmatching semantics 보존. DOM 셀은 기존 deferRender로 먼저 유지, 상세 필요시 lazy화는 검색/CSV가 전체 값을 계속 가지는 설계 뒤 별도 결정.
- **선행 테스트:** 101row page/filter/sort/선택/copy/CSV golden, 날짜/NULL P05 case, 빠른 apply/reset/뒤로가기/theme, URL필터/reload saved state, hidden columns와 상세 field search. 실제 download bytes로 전체matching을 확인.
- **측정/완료:** fullJSON bytes/parse/RSS와 filterdraw CPU/queryparsecount/longtask/interactive p95. 각 draw parse1회, rowselector0 목표, 10k행 filterCPU30% 개선 provisional; 작은24행에 10% 초과 회귀 없을 것. full population 유지가 우선.
- **대안/롤백:** 전체API→pageAPI 강제 또는 columns삭제 금지. server-side DataTables는 전체 export/query 계약을 마련한 별도 승인 전 보류. filter adapter를 기존 predicate로 revert 가능.

### P16a/b — active 통계표와 안정적 shell(E23/E24)

- **파일/함수:** a `web/static/ui/stats.js` table init/footer/search; b `stats-loader.js`, `stats.html/stats_fragment.html`, 기존 map controller. typed footer rows/table registry/mount-dispose hooks는 제안 신규.
- **현재→개선:** a 18표 eager init→활성 pane 첫 표시만 init, 나머지 데이터는 전부 보존. footer는 typed rows로 계산하여 DOM attr 반복 제거하되 현재 rounded-group 가중 결과 보존. b page 전체 replace/scriptreload→stable shell/filter/offcanvas/mapCard 유지+data region 교체+명시 mount/dispose 한 번. requestseq+owner generation으로 stale reply 무시, AbortController·focus/flatpickr/listener/DataTables destroy 수명 명시.
- **선행 테스트:** 모든 pane/categorical/law tables 첫open·reopen, footer search/order/rawvsrepresentative, full CSV, rapid filter race A/B/C, network fail/retry, preserved mapInstance/cancel, offcanvasfocus/draft/applied/keyboard/theme/390px. listener count와 oldDT instance cleanup을 확인하고 기존 E24 미재현을 실제 증거로 바꾼다.
- **측정/완료:** shell firstpaint와 data-ready를 따로, allpane-ready·JS init·DOMnode/heap·listener·fetchcount·map network. visible pane render p95 개선+allpane 정확, 20번 refresh 뒤 heap/listener가 계속 증가하지 않음. shell만 개선하고 집계 지연을 숨기면 미완료.
- **대안/롤백:** lazy init로 숨은 표 CSV가 빠지는 안 기각. footer raw평균 correction은 별도 PR, table state IDs/storage key 유지. a eager init fallback, b 기존 fragment 경로 revert하되 listener disposer 유지/active request cancel.

### P17a/b — bounded 로그와 WS backpressure(E28/E29)

- **파일/함수:** a `rating.html`, `rating_route`의 WS initial read, `crawl.html` bounded helper; b `ws_manager.broadcast/broadcast_from_thread/track_api_request/close_all`, `ws_route.ws_events`, main lifespan. per-client sender/queue는 제안 신규.
- **현재→개선:** a crawl의 기존 2,000줄/256KiB·초기64KiB 정책을 rating에 재사용, append debounce·pagehide cleanup·bounded reconnect·autoscroll 유지. b connection별 sender queue로 broadcast를 분리; provisional queue128event/256KiB/send5초, progress는 최신으로 합치되 jobterminal/권한 event를 조용히 drop하지 않음. durable job 상태를 HTTP 재조회하는 recovery 계약을 consumer 확인 후 사용. slow consumer는 명시 overload 종료(1013 후보), 기존 auth closecode 재사용 금지.
- **pong/수명:** 현재 pong 타임아웃 구현은 없음. 기존 모바일/확장이 실제 pong을 보내는지 확인 전 timeout을 추가하지 않음. thread future 예외 관찰, API metadata TTL/provisional10분·max entries의 prune, disconnect 기존 cleanup 유지. 종료는 producers stop→queues drain bounded→senders await/cancel→socket close.
- **선행 테스트:** 빠른/멈춘/끊긴 client 혼합, progressburst+terminal/authloss, duplicate close, threadbroadcast futurefailure, no-main-loop, reconnect+poll recovery, 10MiB ratinglog/invalidUTF8/chunknewline, tab leave. closecode/payload/모바일 parser 비교.
- **측정/완료:** fastclient eventp95·RSS/logdom·queuebytes·sendtask/connection count·shutdown duration. 한 느린 client가 다른 client/producer를 막지 않을 것, bounded memory·criticalstate 복구·clean shutdown. consumer 확인 없으면 1013/pong 강화 부분 blocked.
- **대안/롤백:** asyncio.gather에 timeout만 넣어 모두 취소하는 안은 연결별 수명을 해결 못함. a independent revert, b senders 정리 후 원래 broadcast 제한 모드로 revert; auth/gate 사건은 지속 전달/연결종료.

### P18 — 외부 export/알림의 partial·unknown(E34)

- **파일/함수:** `core/utils/export.py` module init/save_to_google_sheet/_upload_to_worksheet/save_results, `core/utils/notifier.py.main/send_message`의 chunk/fallback, `export.py`/`services/export_service`, P06 outcome. lazy client/staging export journal는 제안 신규.
- **현재→개선:** import network → 호출 시 lazy credentials/client, local file export 성공과 remote success를 분리. Sheets는 먼저 전체 데이터를 준비하고 staging worksheet에 모두 쓰고 검증한 후 live로 전환. 원래 worksheet ID/외부 참조 보존이 기본. 원격 API의 단일 atomic copy/commit 지원은 공식 문서·mock 요청 구조 확인 전 미확정; 불가하면 live backup과 명시 partial/recovery 절차로 설계하고 원자성을 주장하지 않는다. 원본 clear부터 자동 retry 금지.
- **알림:** chunk별 sent/unknown checkpoint, 일부 전송 뒤 전체 fallback 금지, unknown 원격 전달을 success로 세지 않음. child run은 bounded deadline/cancel/stdoutstderr 관리. 개인 DB 저장/수집 terminal이 알림 실패로 소실되지 않게 separate phase status.
- **선행 테스트:** enabled 설정 상태에서도 import 외부 요청0, chunk1성공/chunk2timeout/commitunknown, staging validationfail/취소/cleanup, notifierpartial/fallback/재시작, hungchild kill확인, 비밀 로그 제거. fake transports로만 실행.
- **측정/완료:** externalcallcount/retrybudget/CPU/RSS/exportduration·partialstates, 중복 자동전체 전송0·live 선clear0·unknownsuccess0. 실제 Telegram/Sheets 전송과 API capability live 검증은 별도 승인 전 blocked.
- **대안/롤백:** 원격 sheet를 무조건 rename/swap하여 기존 ID 참조를 깨지 않음. stagingcleanup은 성공/실패 journal 확인 후만. code revert가 이미 발생한 원격 변경을 복구한다고 주장하지 않음; live backup recovery는 별도 운영 승인.

### P19 — 통합·packaging·실제 assertion(E35)

- **파일/함수:** runtime_paths/PyInstaller resource 수집/dev Docker 설정, fixture_server, `tools/web-tests` pagination/CSV specs, 관련 architecture/CHANGELOG. release workflow/build.sh/VERSION/tag는 변경·실행 승인 범위 밖.
- **현재→개선:** source 한 환경/클릭 성공→source+frozen+isolated devDocker의 동일 계약·리소스·job/IPC/temp lifecycle 검증. 자동 updater/외부 startup service는 fixture에서 차단하고 서버 import 전 차단 확인. 각 platform은 실제 해당 runner 결과로만 passed; 이 Linux 머신 결과를 Windows/macOS 통과로 확장하지 않음.
- **선행 테스트:** Chromium/Firefox, dark/light/390px/keyboard, 기능128행/route/auth/WS contract, actual downloadbytes, fault sequences, source/frozen subprocess crawl/rating/bot adapter, restore refusal·pending restart, resource asset path 포함, Docker project/data root 분리. 모바일 실제 양방향 fixture·parity는 양쪽 repo 동시 승인 gate.
- **측정/완료:** §7 before/after 결과와 regression·skips·blocked ledger, peakRSS/cold/warm/end-to-end. functional/type/roundtrip差0, concurrencyfail0, no resource missing, allsupported platforms evidence 또는 명시 blocked. 패키징 빌드 성공만으로 동작 확인이라 쓰지 않음.
- **롤백:** PR별 코드 revert와 feature entry disable, temp/journal 정리·user data 보존. dev image만 이전 hash로 교체; production/main/release는 후속 승인. 실제 변경 후에만 CHANGELOG와 architecture 정정 표를 갱신한다.

## 7. 동등 조건 측정 설계 — 이번 수치 없음

### 7.1 실행 전 준비(승인 후)

현재 HEAD+기존 사용자 diff를 기준 구현으로 보존하고, 승인된 구현은 별도 branch/worktree에서 비교한다. baseline SHA로 reset하지 않는다. fixtures/측정 artifacts는 `.agent-runs/` 아래에만 둔다. Python/pandas/SQLite/Jinja/브라우저/OS/CPU/RAM·dependency lock과 git SHA/diff hash, settings/registry manifest/seed/checksum을 기록한다. 외부 요청과 subprocess는 명시 fake/차단, fixture 계정만 사용한다. dbserver/mobile의 실제 왕복만 각 real converter를 사용한다.

자료 규모는 24행(edge), 101행(pagination), 1k/10k/100k행(fixed-seed 분포), 선택 대상1/20/500, agency/law 고/저 cardinality, 중복률0/20/80%, 첨부/긴본문/NULL edge를 포함한다. 전체 통계와 narrow target, raw/canonical 둘 다. 100k는 stress이며 기본 제품 response를 truncation하는 근거가 아니다. old/new가 동일 output을 만드는 fixture checksum을 먼저 검사한다.

각 case cold isolated worker/process 5회, warm 동일 process 30회, concurrency1/5/20을 기본으로 삼는다. paired 순서를 번갈아 OS/page cache·CPU 상태를 기록; 충분한 반복과 샘플 없으면 p95/p99 주장하지 않는다. cold는 app/cache/SQLite/OS cache를 각각 구분하고 운영 머신 OS cache drop은 하지 않는다. 필요하면 worker restart만 적용해 의미를 명시한다. 외부 지연은 fake controlledlatency 별도 cohort, 실제 site 성능으로 일반화 금지.

측정 장비는 승인 후 선택: Python perf_counter/process_time·tracemalloc(단독 profilingrun)/process peakRSS, SQLAlchemy query counter/rows/`EXPLAIN QUERY PLAN`, phase timer, browser performance marks/longtask/heap/DT draw/mapready, asyncio looplag. profiling overhead가 있는 run과 일반 walltime run 분리. 한 endpoint 평균만으로 전체 개선을 주장하지 않는다.

### 7.2 비교표

| 시나리오 | 주요 before/after 지표 | Before 이번 실행 | After 이번 실행 | 채택 조건/PR |
|---|---|---|---|---|
| 목록 전체 cold load/filter/CSV | SQL/JSONbytes/parse/DOM/CPU/data-ready/fullCSVbytes | not-run | not-run | exactpopulation 유지; filterCPU provisional30% 개선/P15 |
| ID/rating/watchlist/editor narrow | querycount/rows/bytes/latency·1vs100k확장 | not-run | not-run | 필드·순서 동일, 전체hash 제거/P11 |
| 통계 모든 group/coldpage | DBread·parse/mask·estimatecalls·CPU·RSS·finalready | not-run | not-run | fullJSON 동일, aggregateCPU provisional30%↓/P12 |
| 통계 warm/cache동시miss | flightcomputes·lockwait·copy·hitp95·bytes | not-run | not-run | miss동일key1compute·generation정확/P13 |
| stats UI 처음/모든pane/20refresh | init/draw/DOMnode/heap/listeners/mapready | not-run | not-run | 표시 정확, 지속heap증가0/P16 |
| duplicate 표시/refresh | reads/hashcalls·transactiontime·RSS | not-run | not-run | display전체hash0·refreshgolden/P02/P11 |
| queue 1/20/500append | fsynccount/lockhold/response/bytes | not-run | not-run | batchdurablesave1·값순서동일/P11 |
| job start/cancel/fail | admission/terminalduration·lostjobcount | not-run | not-run | terminal/durablequeue 정확/P06~09 |
| media miss/hit/range | TTFB/throughput/fd/task/RSS | not-run | not-run | bytes/length정확·race0/P10 |
| WS fast+slow/ratinglog | fastp95/looplag/queuebytes/heap/shutdown | not-run | not-run | slow隔離·bounded·terminal복구/P17 |
| ZIP 10/100/1kfiles | peakRSS/tempbytes/time/disconnectcleanup | not-run | not-run | RSS가archive총량비례안함·bytes동일/P01 |
| async blocking/Jinja | looptickp95/worker/inflight/compilecount | not-run | not-run | 원인재현·render/protocol동일/P14 |
| Sheets/Telegram fakefailure | attempts/sentchunks/elapsed/unknownstate | not-run | not-run | 부분전송전체재시도0/P18 |
| 실제 양방향DB/계산parity | 각셀value/type/null/keydiff+conversiontime | not-run | not-run | 교환差0; 모바일확보/별도승인필수 |

제안 성능 기준은 baseline 취득 후 고정한다. 작은 자료의 p95 10% 초과 회귀 또는 RSS10% 초과 증가는 설명/재측정 필요이며 무조건 채택하지 않는다. correctness·보안 수정이 비용을 늘리는 경우 그 비용을 공개하고 제품 의미를 지키는 안을 우선한다. 계측되지 않은 항목을 숫자 0이나 passed로 채우지 않는다.

## 8. 최초 구현 요청의 최소 범위와 보류 조건

다음 번 **명시 구현 승인**의 기본 최소 단위는 **P02a만**이다: duplicate 원천 read 실패의 fail-closed 처리, 필수 load 성공 후 group/member refresh, 해당 fault-injection 테스트와 architecture 정정. 범위는 `duplicate_group_service.py`, 제안 신규 duplicate read-failure test, 관련 architecture 문서 및 실제 변경 CHANGELOG로 제한한다. 스키마/모바일 변환/통계/public API/외부 서비스를 건드리지 않는다. 이 PR은 정상 empty와 오류를 구분하는 oracle를 먼저 고정하고, 이전 group/member/decision/대표 값을 유지하는지 확인한 뒤 제출한다. 심각한 경로/권한 우선 배포가 필요하면 P01a 또는 P03 allowlist를 별도 독립 PR로 먼저 선택할 수 있다. 자동 병행/구현은 하지 않는다.

| 보류 대상 | 현재 이유 | 진행을 여는 구체 조건 |
|---|---|---|
| 이번 모든 제품 구현/앱·테스트·벤치 | 사용자 계획-only 명시 | 후속 구현/실험 범위의 명시 승인. 계획 제출은 승인 아님. |
| DB schema/변환 의미·NULL 정규화 정리 | 양쪽 실제 code·모바일 환경 미검토 | 양쪽 repo 동일 작업 단위 승인, 계약列+실제schema비교·두 방향 모든셀/type 테스트 준비. |
| roundedfooter→raw평균 | 현행 출력과 skill 지침 차이 | 통계 의미 결정과 별도 correctness PR/golden, 모바일 표시 영향 확인. |
| Jinja cache 설정 | E21 원인 미재현 | isolated 실제버전 렌더 재현+cache key/packaging 검증. |
| 증분 중복 refresh/선택적 cache invalidation | 모든writer/영향graph 증거 불완전 | full oracle와 fault/concurrent writer 비교, 영향영역 누락0. |
| server-side 목록/새 lazy data endpoint | 현재 전체search/export 계약과 충돌 가능 | full population 검색/선택/CSV·API adapter 설계 승인; 기존전체API유지. |
| bot IPC대안/group support | 새 권한/공개endpoint 가능 | private scope/IPCauth/frozen lifecycle 명세; 기본 in-process·group거절부터. |
| WS overload/pong 강화 | 실제 consumer 구현 미확인 | 모바일/확장 reconnect/pong/closecode 소비자 검토·실제fixture계약검사. |
| Sheets atomic commit/live전송 | upstream 지원/worksheetID보존 미확인, 실제외부금지 | 공식API capability 검토→mockpartial검사→별도운영승인. |
| 플랫폼전체passed/productionrelease | 이번 실행0, 해당runner 미사용 | 각OS/arch+devDocker 실행증거; production/VERSION/main/tag 별도승인. |

### 8.1 이번 계획 제출 완료 기준

입력 3문서 전체 열람, 현재 HEAD·기존 변경 보존, 모든 E의 주/부분 판정, 399개 파일의 검토 수준, 128행 기능 연결, DB/API/WS/권한/작업 수명 계약과 per-PR 테스트·측정·게이트·롤백을 문서화했다. 모든 실행 결과는 not-run이다. 입력 helper나 기존 테스트·benchmark를 이번 실행으로 표시하지 않았다. 문서 제출과 입력 패키지 정리 후 정지하며 구현·운영 변경·배포로 넘어가지 않는다.

제출 전 문서 자체의 E35개/부록 행수/CSV 열수/링크/입력 SHA를 정적으로 확인했다. 기존 433개 파일은 내용 해시가 같았으며, 이후 요청한 `safetyreport_dev_refactoring_plan_package_ko.zip`과 이번 압축 해제 폴더(입력 3문서·MANIFEST·README)만 삭제했다. 나머지 기존 파일의 내용 해시 변화는 0개다. 별도 기관 registry ZIP은 보존했다. 이 확인은 제품 테스트나 실행 벤치마크가 아니다.
