# dev 리팩터링 구현 검수와 남은 게이트

기준은 `dev e9444aa4e14fa3d3bacc7efcfc32cd6e26f782d0`의 기존 작업트리다. 기준 SHA로 되돌리지 않았다. 사용자 승인 범위는 “전체 계획을 검증하며 순차 진행”이며, 운영 자료·실제 외부 제출·배포·VERSION 변경은 포함하지 않는다. 원래 [계획](../plans/dev-refactoring-plan-2026-10-04.md)의 실행 전 `not-run` 기록은 역사적 사실로 유지한다. 이 문서의 수치는 이번 실행으로 얻었다.

아래의 **passed는 명시한 검사 범위**에만 적용한다. P00~P19 전체 완료, 기능표 128행 전체 승인, 모든 플랫폼 배포 가능이라는 판정은 아니다. P14b/P17b는 계획의 보류 조건에 따라 구현을 보류했고, 나머지 완료 게이트 중 실제 기기·외부 서비스·다중 플랫폼과 성능 일부는 열려 있다.

## 이번 실행 검증

| 대상 | 실제 결과 | 증거·한계 |
|---|---|---|
| 변경 전 Python 기준선 | 573 실행 / 568 passed / 기존 5 skipped, 152.455초 | `.agent-runs/refactoring-implementation/baseline-tests.log` |
| 최신 통합 Python | 649 실행 / 644 passed / 기존 5 skipped, 158.726초 | `suite-9.log`; 외부 Supabase/community live stack의 기존 skip을 제거하거나 늘리지 않음 |
| Chromium·Firefox | 전체 묶음 110 passed / 신규 CSV 절차 2 failed, 9.4분 → 해당 두 검사 수정 후 2 passed, 17.6초 | `browser-full-2.log`, `browser-pagination-fixed.log`; 합계 112개 검사 범위. 검색 패널 닫기 절차를 추가했으며 assertion을 완화하지 않음 |
| fixture 설정 호출자·화면 재확인 | 설정 draft를 save 후 reload하도록 helper 정정; 관련 browser 14 passed, 1.0분 | `browser-fixture-config-final.log`; 기존 112와 겹치는 검사이며 126개 독립 검사로 합산하지 않음 |
| 실제 화면 캡처 | Chromium stats light/dark 1440px, light 390px, list dark 390px 캡처·육안 확인 | `visual-final/screens.json` 및 PNG 4개; 표 내부 가로 스크롤을 페이지 overflow로 간주하지 않음. pixel baseline 비교는 not-run |
| 새 연결 풀 교체 회귀 | 수정 전 1!=2 실패 → cache/snapshot 7 passed, 1.594초 | `pooled-cache-before.log`, `pooled-cache-after-2.log`; sqlite3 직접 open 대신 실제 SQLAlchemy pool을 사용 |
| 마지막 Excel fsync 핸들 보강 | 읽기/쓰기 핸들로 fsync하도록 1행 보강 후 export 6 passed, 0.157초 | `export-final.log`; 전체 649와 중복이며 별도 독립 테스트 수로 합산하지 않음. 실제 Windows 동작은 not-run |
| Docker linux/amd64 | 전용 image/project 빌드·health/login·신규/변경 JS HTTP 200와 MIME, 개발 경로 제외 | `docker-build-pool-final.log`, `docker-assets-complete.json`; 인증 후 전체 UI/다른 architecture는 not-run |
| 정적 검사 | Python AST 212개, JS 6개 syntax, route/DOM inventory | `web-contract-final.json`, `dom-static-final.log`; `allTable`은 Jinja 동적 ID여서 정적 check에는 없고 실제 브라우저에서 확인 |
| 서버 DB 복원·변환 | 기존 synthetic storage/backup 테스트를 최신 통합에 포함 | 교환 schema/컬럼/저장 값 변경 없음. 실제 모바일 변환기를 실행한 양방향 all-column 왕복은 별도 blocked gate |

중간 실패 로그도 보관했다. 존재하지 않는 테스트 모듈을 함께 지정한 초기 명령의 loader error는 최종 통과 결과에 섞지 않았다. SQLite ResourceWarning은 기존 테스트에서도 관찰했으며 제거됐다고 하지 않는다.

전체 649 실행과 Docker 최종 snapshot 뒤에는 fixture의 draft-save 호출자와 Excel fsync open mode 한 행만 보강했다. 각각 관련 browser 14개/export 6개로 확인했다. Docker 이미지의 마지막 확인 SHA는 cache/main의 최신 값이며, 이 마지막 Excel 한 행까지 포함한 재빌드 결과로 확대하지 않는다.

## 단계별 구현과 확인 범위

| 단계 | 변경 파일·함수의 핵심 | 확인한 흐름·선행 검사 | 판정과 열린 완료 게이트 |
|---|---|---|---|
| P00 | 기존 계획·scope/routes/symbols/storage/feature 부록 보존, 격리 baseline | 운영 root와 분리된 fixture를 import 전에 지정; 573 기준선 | baseline passed; 128행의 모든 행동을 개별 승인하지 않음 |
| P01 | `file_service` root/fd/unlink/ZIP/snapshot, `TemporaryFileResponse`, 파일 routes | prefix/link/traversal 거절→검증 fd; memory ZIP→완성 임시 ZIP; 실패/Range/disconnect 정리 | POSIX·ZIP·응답 검사 passed; Windows reparse/핸들, 프로세스 강제 종료 후 잔여 임시파일 회수 not-run |
| P02 | duplicate `_load_inventory/_safe_read`, query/stats 필수 read | 필수 5원천의 오류→재생성 중단·rollback; 정상 empty는 기존 의미 | 원천별 read failure 회귀 passed; 운영 손상 DB 실험 없음 |
| P03 | `bot.authorized_update/start_managed/stop_managed`, `main.lifespan` | chat+user 확인, standalone writer 거절, managed 공통 접수·정리 | 허용/거절·시작 실패 cleanup passed; 실제 Telegram polling not-run |
| P04 | 첨부/device DOM, CSRF+session-requests, atomic/file_lock, `AppSettings.load/save` | 후보 검증→최신 파일과 키 병합→replace→snapshot 게시; draft 실패 제거 | 동시 writer/replace failure·CSRF·XSS fixture passed; Windows 파일락 실행 not-run |
| P05 | API literal bulk route·bounded JSON, query/stat 범위, 목록/지도 URL, upload feedback | 잘못된 body 400/413; NULL 취하 모집단·날짜/분 끝·agency/law/police 조건 보존 | API/통계/드릴다운·browser passed; 큰 운영 데이터 조합 전수 not-run |
| P06 | `crawl_control` watcher 선예약, manager outcome/후처리, `crawl_run_state`, `start` | child 접수 실패도 대기 해제; terminal return code와 수집 기록; 후처리까지 예약 | 빠른 종료/실패 fake·완료 마커 기존 계약 passed; 새 outcome의 실제 모바일 해석 gate 열림 |
| P07 | rating admission/worker stop, `rating_operation_state`, star POST/GET | crawl/restore 상호배제→제출 전 기록→unknown은 조회 확인만 | lost-response fake에서 자동 중복 POST 없음·예약 해제 passed; 실제 제출 not-run |
| P08 | fresh gate·scoped preflush, rebuild managed worker/supervisor/attempt fencing | 영속 접수→백업/manifest→launch; pause/old attempt의 쓰기·완료 거절 | worker/late child·pause stop pending·gate tests passed; 실제 중앙/크롤 child 연동 not-run |
| P09 | login retry/deadline, title first-page/integrity, uploader manifest/interval, Sunwi retry owner | false/exception 유한 retry; total/count/ID 검증; cursor·lease·scope 후 snapshot 교체 | 기존+신규 fake transport tests passed; OS/Selenium 강제 취소와 실제 중앙 latency not-run |
| P10 | media prime callback/ready/generation/reader pins/stop | 완료 future callback을 lock 밖 등록; tmp open 뒤 ready; pin 동안 정리 금지 | 8개 media lifetime·Range 기존 tests passed; 실제 원격 다운로드·장시간 timeout not-run |
| P11 | resolve/watch/rating/editor·duplicate 대상 SELECT, queue append_many, uploader payload batch | public 필드 유지→대상 ID chunk; pending 후보→durable save 1회→게시 | narrow-query/queue rollback/scope tests passed; 100k 대상 행·lockhold 부하 not-run |
| P12 | stats `_prepare_metrics`, date/disposition/fine/rating/estimate 재사용 | 한 snapshot의 파생값→기존 그룹·분모·HALF_UP 출력 | 공개 함수 parity·HEAD JSON 비교 passed; CPU 목표 충족, RSS·실제 final-ready p95 gate 열림 |
| P13 | cache generation/single flight/byte budget/probes/engine pool, read transaction | metadata lock→소유 결과 copy; 실패 waiter 해제; inode 교체시 pool 갱신; old publish 거절 | cache 5개 + 실제 WAL 중간 write snapshot 2개 passed; 대규모 동시 교체 stress not-run |
| P14a | async version/community token helper의 threadpool adapter | request 입력 추출→worker I/O→기존 응답 | fake 느린 helper 동안 health/tick 유지 passed; 운영 loop-lag p95/p99 not-run |
| P14b | Jinja 우회 원인 주석 정정; cache_size=0 유지 | 실제 버전 DictLoader/include/macro/dict globals/TemplateResponse 격리 실험 | E21 미재현; 제품 전체 렌더/frozen 증거 전 cache 활성화 blocked |
| P15 | 목록 appliedSearch snapshot, `list-predicates.js` | draft는 미적용; apply 때 13 query parse, row selector/parse 반복 제거 | draft·날짜·101행 pagination/full filtered CSV passed; VM CPU 목표 충족, 브라우저 10k final-ready/RSS gate 열림 |
| P16 | stats active-pane init, `SrStats.mount/dispose`, stable loader, Sunwi dispose | eager 18표→첫 방문; 표/연도만 hydration→shell/draft/focus 보존 | 18 pane·20 remount bounds·race/error/map/CSV·light/dark/layout passed; 실제 heap/RSS not-run |
| P17a | `web/log_stream`, rating bounded-log, WS metadata TTL/lock/future observer | 64KiB read·회전/축소·gate close; DOM 2k줄/256KiB; HTTP metadata 상한 | log/WS 계약·large Korean DOM passed |
| P17b | 이벤트 큐/deadline/drop/replay 정책은 미구현 | 실제 모바일 소비자에 terminal 재조회/replay 보장이 없는 상태에서 critical 이벤트를 drop하지 않음 | blocked; 느린 socket의 gather/backpressure는 남음. 양쪽 consumer 합의·검증 필요 |
| P18 | lazy Sheets client/staging atomic publish, Excel temp replace, notifier chunk/deadline/journal | stage 실패→live 유지; publish 응답 유실→unknown·재게시 금지; 알림 응답 유실 재송신 금지 | FakeSheets/notification·실제 xlsx 보존 tests passed; 실제 외부 전송 not-run |
| P19 | 기존 build 경로·fixture Docker, static syntax/inventory, stronger browser assertions, lifespan finally | source browser + 전용 Docker 검증; 실패 정리·기존 사용자 파일 보존 | 확인한 환경 passed; 전체 기능표/기기/OS/frozen/ARM 게이트 미완료 |

## 성능 실측의 정확한 범위

| 측정 | 기존→개선 | 의미·제한 |
|---|---|---|
| ZIP 64MiB(4×16MiB seed 고정 비압축성 파일) | peak RSS 87,140→22,020KiB; wall 2.071→2.069초 | 별도 Linux 프로세스 각 1회. 모든 member SHA 일치, 상대 archive 이름의 의도된 변경 포함. RSS는 imports 포함, hash 검증은 timing 뒤 |
| 통계 24행 pure aggregate | CPU median 0.381585→0.141036초(63.0%↓), wall 0.412588→0.159501초 | 원래 HEAD 집계 코드와 현재 코드, 10회 교대 paired run; 전체 JSON SHA 동일 |
| 통계 2,400행 pure aggregate | CPU median 0.566796→0.266318초(53.0%↓), wall 0.594649→0.280263초 | 24행 fixture를 100번 반복. SQL/cache/browser/cold process/RSS 제외. 처음 부분 최적화의 16~18% 결과를 최종 수치로 재사용하지 않음 |
| 목록 10k행 predicate(empty/text) | CPU median 308.314→53.577ms / 307.281→49.938ms | 실제 HEAD/현재 template callback을 Node VM에서 25회 paired 비교. DOM selector stub이므로 실제 browser draw/transport 성능이 아님. selection 완전 일치 |
| 목록 24행 predicate(empty/text) | CPU median 1.356→0.171ms / 2.026→0.178ms | 작은 fixture의 이 실험에서 회귀 없음. 새 capture가 24 input 접근·13 parse 후, 행별 0 selector/0 parse |
| Jinja 30회 격리 render | cache0 compile91 / cache400 compile3, CPU 0.142383 / 0.036920초 | 렌더 출력·autoescape 일치; dict globals 오류 미재현. 실제 제품 cache 변경 근거로 확대하지 않음 |

원본은 `zip-measurement.json`, `stats-measurement-2.json`, `list-measurement.json`, `jinja-probe.json`이다. 통계·목록의 **순수 계산** 잠정 CPU 목표는 충족했으나, 계획의 cold full-page p95/RSS 및 동시성 부하 목표는 이 측정으로 통과시키지 않는다.

## E01~E35 재판정

“해결됨”은 아래 서버/fixture 증거 범위의 해당 원인을 제거했다는 뜻이다. 실제 악용·외부 실패·모든 플랫폼까지 재현/해결했다는 뜻이 아니다.

| E | 현재 분류 | 근거·잔여 경계 |
|---|---|---|
| E01 | 해결됨 | root/fd/link 회귀; Windows 핸들 동등성 추가 확인 |
| E02 | 해결됨 | 모든 개인 handler 권한 검사·managed 접수 |
| E03 | 해결됨 | 5원천 read failure 시 자료 보존 |
| E04 | 해결됨 | DOM text/property·script URL 거절 fixture |
| E05 | 해결됨 | 실제 bulk dispatch·bounded object/type 검사 |
| E06 | 추가 확인 필요 | 서버 terminal/failed 기록은 해결; 실제 모바일의 새 outcome 해석 미검증 |
| E07 | 해결됨 | watcher 선예약·launch failure/빠른 종료 검사 |
| E08 | 해결됨 | worker/supervisor·pause·old attempt fencing; read-only backup 협력 취소 한계 명시 |
| E09 | 해결됨 | uploader와 같은 scoped sendable count |
| E10 | 해결됨 | strict action freshness와 navigation cache 분리 |
| E11 | 해결됨 | false/exception retry 증가·deadline |
| E12 | 해결됨 | first-page 재사용·total/count/unique ID 검사 |
| E13 | 해결됨 | 즉시 완료 callback·tmp ready·pins/generation tests |
| E14 | 해결됨 | reservation·lost-response GET reconcile/재POST 금지 |
| E15 | 해결됨 | date/mask/금액 재사용·HEAD JSON 동일; 전체 화면 성능 gate 별도 |
| E16 | 해결됨 | persisted member 대상 hydrate, full refresh 유지 |
| E17 | 해결됨 | targeted public full-record SELECT·editor summary |
| E18 | 해결됨 | SQL/pandas NULL 취하 모집단 회귀 |
| E19 | 해결됨 | singleflight/byte budget/generation·실제 SQLAlchemy pool inode 교체 |
| E20 | 해결됨 | threadpool offload·느린 fake helper health 검사 |
| E21 | 미재현 | 실제 버전 격리 cache key에 dict globals 없음; cache0 유지 |
| E22 | 해결됨 | applied snapshot·행별 query parse/selector 제거 |
| E23 | 해결됨 | 첫 방문 표만 초기화·기존 footer 의미 유지 |
| E24 | 해결됨 | stable shell·20회 mount/dispose 자원 수·focus 검사 |
| E25 | 해결됨 | 날짜/분 정밀도 inclusive 끝, 저장값 유지 |
| E26 | 해결됨 | agency/law/exact/police/dedupe 드릴다운 보존 |
| E27 | 해결됨 | upload/device HTTP 실패에 success 표시 안 함 |
| E28 | 해결됨 | log read/DOM bound·rotation/truncation/gate 검사 |
| E29 | 확인됨 | metadata TTL/future 관찰은 해결; 무한 gather/큐 정책은 consumer gate로 보류 |
| E30 | 해결됨 | atomic candidate/file lock/draft·session CSRF |
| E31 | 해결됨 | disk ZIP/Zip64·전체 실패·상대 이름·응답 정리 |
| E32 | 해결됨 | append_many durable save 1회·payload batch SELECT |
| E33 | 추가 확인 필요 | retry 중첩 축소·manifest/lease·drain 간 간격 구현; 실제 외부 종합 budget/latency 검증 없음 |
| E34 | 해결됨 | import side effect 제거·staging/unknown·bounded 알림; 실제 원격 smoke 없음 |
| E35 | 해결됨 | 101행 페이지 ID/쪽 수·BOM/헤더/행/쉼표/인용/줄바꿈/full filtered CSV 실제 assertions |

## 독립 검수와 보존

실제 `agy`, 고정 `gemini-3.1-pro-high`, 별도 detached worktree를 사용했다. 최종 conversation은 `70f154a0-d48a-4675-a34d-5f1ce7162352`. 초기 최종 snapshot에서 640개 실행(635 passed/5 skipped, 171.992초), 이후 수정 범위별 49개(8.875초), 11개(1.457초), shutdown 14개(0.417초), 실제 pool 5개(0.116초)를 실행했다. review worktree에는 기존 사용자 미추적 DB regression을 넣지 않았다. 이 수를 현재 원래 작업트리의 649개 결과와 혼합하지 않는다.

원본 `REVIEW*.md`/raw stdout/stderr/테스트 로그는 `.agent-runs/refactoring-implementation/`에 보관한다. “완벽/100% 보장” 등의 검수 서술은 채택하지 않았다. 첫 일반 보고서의 P10 인용이 media 대신 cache여서 media/Excel/WAL delta를 별도 재검수했다. 실제 파일·행과 테스트를 대조한 범위에서 중대 결함 미발견이며, 자기 구현을 전체 독립 승인한 것으로 기록하지 않는다.

기존 사용자 `VERSION`, DB backup regression, preview config, issue/testresults 문서·기관 registry ZIP은 보존한다. 이번 계획 입력 ZIP 및 압축 해제 폴더 삭제 요구는 계획 단계에서 처리됐다. 임시 build/test 산출물은 `.agent-runs/`에만 두며 추적하지 않는다. 전용 Docker project의 container/network/volume만 정리한다. 운영 compose/data/계정·외부 실제 제출·main push/태그/릴리즈는 실행하지 않는다.

## 롤백과 보류 조건

- 변경은 로컬 commit의 파일/단계 단위로 되돌린다. 데이터 schema/version 변경이 없어 DB downgrade가 필요하지 않다. 기존 사용자 변경은 rollback 대상이 아니다. unknown 작업 기록은 보존하고 자동 제출 재시도로 전환하지 않는다.
- cache 문제는 cache disable/순수 계산으로 되돌리되 새 inode의 pool 갱신과 snapshot 보존은 유지한다. 통계 metric adapter를 제거하면 기존 pure group 계산으로 돌아간다. 성능 때문에 분모/반올림/NULL 의미를 바꾸지 않는다.
- UI lazy mount 문제는 이전 eager init으로 되돌릴 수 있지만 기존 instance/listener를 dispose한 뒤 전환한다. 파일·CSRF·원천 실패 방어는 기능 회귀 없이 성능만 되돌리는 대상과 구분한다.
- P14b: 원인·제품 전체 render/frozen 검증 전 cache 활성화 보류. P17b: 모바일 terminal 재조회/replay·critical event 정책 합의 전 socket timeout/drop queue 구현 보류.
- DB: 모바일 actual converter로 양방향 모든 컬럼 왕복/모르는 컬럼·값 보존을 확인하기 전 교환 schema/변환 경로 변경 보류. 이번 변경은 해당 schema를 수정하지 않았다.
- P12/P15/P19: browser cold final-ready p95·RSS/heap·100k 부하, 전체 기능표, Windows/macOS/frozen/ARM, 실제 기기 계약은 not-run/blocked로 남긴다. Linux venv에 PyInstaller가 없으며 공용 venv/패키지는 변경하지 않았다. 이 조건이 열린 동안 전체 계획/릴리즈 완료를 선언하지 않는다.
