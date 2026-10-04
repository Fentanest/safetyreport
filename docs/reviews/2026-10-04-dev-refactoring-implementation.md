# dev 리팩터링 구현·검증 결과

사용자의 “전체 계획을 검증하며 순차 진행” 승인에 따라 이전에 보류했던 구현과 이 Linux 개발 환경에서 실행할 수 있는 검증까지 진행했다. 원래 `dev e9444aa4e14fa3d3bacc7efcfc32cd6e26f782d0` 이력과 사용자 변경을 보존했고 기준 SHA로 되돌리지 않았다. 앞선 구현 commit은 `91d18b4`다. 실행 전 [계획](../plans/dev-refactoring-plan-2026-10-04.md)의 `not-run` 기록은 당시 기록으로 유지한다. 아래 숫자는 이번 작업에서 실제로 얻은 결과다.

P17b 서버 전달 큐·완료 replay와 모바일 소비자를 함께 구현했고, 다운로드 강제 종료 회수·Windows 핸들 경계·Sunwi 예산·백그라운드 종료 소유권·통계 remount 누수를 추가로 보강했다. P14b는 제품 렌더와 실제 frozen 검증을 수행했지만 E21을 재현하지 못해 계획의 기본안인 `cache_size=0`을 유지했다. 새로운 기능·교환 스키마·VERSION·릴리즈 경로를 변경하지 않았다.

**passed는 표에 명시한 범위에만 적용한다.** 실제 Windows/macOS/ARM 호스트, 물리 모바일 기기, 실제 크롤링·별점·Telegram·Sheets·중앙 계정 작업은 완료 판정에 포함하지 않는다. [기능별 실행 증거 CSV](dev-refactoring-feature-results-2026-10-04.csv)는 원본 128행과 중복 SH-12를 보존하며, 부분 assertion과 실제 외부 동작을 구분한다. 모든 행의 모든 동작 또는 모든 플랫폼 릴리즈를 승인한 문서가 아니다.

## 실제 실행 결과

증거 파일은 별도 표기하지 않으면 `.agent-runs/refactoring-implementation/` 아래에 있다. 중간 실패와 최종 통과 로그를 모두 보존했다.

| 대상 | 결과 | 증거·판정 범위 |
|---|---|---|
| 변경 전 Python 기준선 | 573 실행 / 568 passed / 기존 5 skipped, 152.455초 | `baseline-tests.log`; 처음 확보한 이번 실행 기준선 |
| 마지막 변경을 포함한 Python 통합 | **676 실행 / 671 passed / 기존 5 skipped, 146.878초** | `suite-completion-final-3.log`; 외부 live stack의 기존 skip을 늘리거나 assert를 완화하지 않음 |
| 일반 브라우저 회귀 | **369 passed, 29.3분** | `full-browser-completion-2.log`; Chromium/Firefox/WebKit 각 123건; 목록·통계·지도·설정·권한·로그·작업바·기존 운영 화면, 테마·크기·키보드 검사 |
| 동명 기관·담당자 전용 fixture | **42 passed, 4.3분** | `collision-browser-1.log`; 별도 데이터에서 각 엔진 14건; 선택·목록·지도·CSV·완료 모집단·zoom |
| 보완한 기능 경계 | **15 passed, 54.9초** | `boundary-browser-complete-2.log`; 각 엔진 5건; 외부 링크 no-cors/window.open, 보완 상세·앱 fallback, 실제 감시 추가/해제, 필드 링크·프록시 영상 실제 재생/중단, 경찰·별점·설문·날짜 필터 |
| remount 최종 수정 재검사 | 12 passed, 1.5분 | `remount-browser-final.log`; 각 엔진 4건, 위 369와 겹쳐 독립 426건에 합산하지 않음 |
| 서버↔모바일 실제 DB 변환 | 양방향 교환 컬럼 diff **0** | 독립 worktree의 `.agent-runs/completion/sr_roundtrip_8nyoyyse/db_roundtrip_out.json`, `db_roundtrip_exit.txt`; 서버 S0→모바일 M1→서버 S2→모바일 M3; 실제 Dart importer와 서버 restore 사용 |
| 모바일 native build/unit | debug unit **12 passed**, `assembleDebug` 성공 | `mobile-persistence-final-build.log`; 기존 compatibility7+outcome2+commit failure3, 기존 Android Studio JBR을 명령별 지정, 공용 SDK/Gradle 설정 변경 없음 |
| 실제 Android 35 x86_64 emulator | 실패/취소/unknown 알림·offline replay·cursor 3·재연결 중복 0 | `mobile-native-smoke-atomic.log`, `mobile-native-smoke.json`; 전용 AVD의 native WS foreground service, 정상 protocol3 gate 검증. Flutter 전체 UI·물리 기기 검사는 아님. AVD 실행 뒤 추가된 실패 commit 메모리 복구는 최종 native unit3/build로 검증 |
| Linux x64 PyInstaller ELF | 최종 source snapshot 빌드·Unicode 설치/데이터 경로·인증 HTTP·실제 화면·WS replay passed | `frozen-build-lifetime-final.log`, `frozen-smoke-lifetime-final.log`; 12 HTML/5 JS MIME, Chromium/Firefox 목록 21행·통계 final-ready, 실패/취소 replay, lifespan 종료. 전용 packaging venv 사용 |
| Docker linux/amd64 | 최종 이미지 빌드·정상 인증·Chromium/Firefox 목록 21행·통계 final-ready passed | `docker-lifetime-final-build.log`, `docker-lifetime-final-browser.log`, `docker-completion-http.json`; private project·합성 volume. HTTP 전체 경로 검사는 마지막 종료 소유권 보강 전 실행, 최종 화면 재확인은 이후 실행 |
| Windows 파일 경계 | Linux fake Win32 protocol passed; 실제 Windows **blocked** | `windows_handle_protocol` 회귀; `scripts/dev/verify_windows_file_boundary.py` 실환경 runner 준비. Linux probe는 exit2/blocked이며 Windows passed로 확대하지 않음 |
| 독립 Gemini 추가 검수 | 수명 70, delta 22, 예산 17, media 23, 최종 범위 29, shutdown 80 passed; 엄격 browser 2 passed | 별도 worktree·정해진 모델의 실제 `agy` 실행. 현재 676/426 결과와 중복이므로 합산하지 않음 |

첫 전체 browser 실행은 collision fixture를 일반 fixture에 섞어 중단했다. 다음 실행은 일반/동명 기관 fixture를 분리했다. fixture 설정은 draft를 save/reload하고, DataTables 준비·Bootstrap hidden 이벤트를 기다리도록 절차를 고쳤다. 경찰 제외 예상값에는 실제 비경찰 의회 행을 포함했다. API key를 하드코딩하는 검사는 해당 fixture 키를 읽도록 정정했다. CSV/영상 중단·필터 값 assertion을 줄여 통과시키지 않았다. Android의 첫 JDK 선택/FGS 시작 절차 실패도 최종 성공으로 재사용하지 않는다.

기존 SQLite ResourceWarning은 기준선과 통합 검사 모두에 존재한다. 이 작업으로 모든 연결 경고가 제거됐다고 주장하지 않는다. 접근성 검사는 기존 report-only 항목과 키보드/aria assertion 범위를 구분하며, WCAG 전체 인증 또는 pixel baseline 전면 갱신을 수행하지 않았다.

## 단계별 최종 흐름과 확인 범위

변경 파일·함수, 입력 oracle, 단계 의존성의 상세 설계는 원래 계획 §6에 있다. 다음은 실제 채택 결과와 열린 환경 경계다.

| 단계 | 구현한 파일·함수와 현재→개선 흐름 | 실제 확인·잔여 경계 |
|---|---|---|
| P00 | 격리 baseline·scope/routes/symbols/storage/feature 부록; 기준선→현재 순차 검증 | 계획 당시 기록 유지, 기능 128행 증거 CSV 추가, 기존 변경 보존 |
| P01 | `file_service` root/fd/delete/ZIP/snapshot, `TemporaryFileResponse`, 신규 `windows_file_handle`·`download_artifacts` | POSIX dirfd/O_NOFOLLOW 및 Windows 같은 검증 핸들 읽기/삭제, disk ZIP/Zip64·응답 finally; 죽은 PID 소유 임시만 회수. 실제 child 강제 종료 회귀 passed; Win32 실제 ABI/filesystem blocked |
| P02 | duplicate `_load_inventory/_safe_read`, query/stats 필수 read | 5원천 read 오류→재생성 중단·rollback; 정상 empty만 기존 파생 자료 비우기. 실패 원천별 사용자 판단/그룹 보존 passed |
| P03 | `bot.authorized_update/start_managed/stop_managed`, `main.lifespan` | chat+user 권한·managed writer; 종료 phase 실패에도 나머지 cleanup 시도·공유 deadline. 실제 Telegram polling not-run |
| P04 | 첨부/device DOM, CSRF/session-requests, `AppSettings.load/save`·atomic/file lock | 후보 검증→최신 키 병합→replace→snapshot; 실패 draft 제거; XSS/CSRF·동시 writer 회귀 passed. Windows 실제 파일락 not-run |
| P05 | bulk literal route/JSON 한도, query/stat 모집단, 목록/지도 URL | body 400/413·NULL 취하·날짜 전체/분 끝·agency/law/police/rating/poll/dedupe 조건; 실제 브라우저·API·통계 parity passed |
| P06 | watcher 선예약·`crawl_run_state`·`crawl_manager.start/shutdown/stop_crawl`, 모바일 `CrawlOutcome` | launch 실패도 terminal/durable 상태 보존; shutdown 준비/Popen 이중 fence·자기 retry/child만 종료·살아 있으면 참조 유지. 실패/취소/unknown 실제 native 알림 passed |
| P07 | rating admission/worker·`rating_operation_state`·star POST/GET | 제출 전 durable 기록→lost response unknown→GET 확인만, 자동 재POST 금지; fake 회귀 passed. 실제 별점 제출 not-run |
| P08 | fresh gate/scoped preflush·managed rebuild/supervisor/attempt fencing | 영속 접수·backup/manifest→launch, old attempt/소유자 쓰기 차단; paused/pending 보존. 실제 중앙/크롤러 작업 not-run |
| P09 | direct login/title integrity/uploader manifest·재시도 owner·Sunwi `RequestBudget` | retry false/exception 증가, first page 재사용·count/ID 완전성; 지역 6회/90초와 전체 1800초 예산 공유·취소 backoff·Session close. uploader/auth/direct-login stop은 실제 worker 참조 유지. DNS/slow-drip 엄격 wall 보장은 아님 |
| P10 | media callback/ready/generation/pins·weak URL locks·prefetch32/status256 | tmp open 뒤 ready, active reader pin, callback lock 외부, URL lock/완료 메타 수명 상한. 프록시 합성 영상 실제 decoding/replay/닫기 중단 및 capacity 회귀 passed |
| P11 | ID resolve/watch/rating/editor/duplicate 대상 SELECT·queue append_many·upload batch | public full record 보존→대상 chunk, queue durable save 1회→게시. 100k actual SQL에서 1/20/500 선택 count·모든 필드 SHA 동일 |
| P12 | `_prepare_metrics` date/disposition/fine/rating/estimate 재사용 | 같은 snapshot의 파생값 공유; raw/canonical·분모·HALF_UP·확정/추정 의미 보존. 전체 JSON SHA·실제 10k/24 final-ready 비교 passed |
| P13 | generation/singleflight/64MiB·8entry budget/probes/read transaction/engine pool | old generation publish 거절·실패 waiter 해제·실제 inode 교체 pool dispose. WAL 중간 commit/snapshot·동시 교체 stress passed |
| P14a | async version/community helper threadpool | request 값 추출→bounded worker→동일 인증/응답; 느린 fake 동안 health/tick 유지. 운영 latency는 not-run |
| P14b | 실제 제품 22 HTTP 경로·24 template/include/macro/dict globals·frozen 렌더 실험 | cache0/400 HTML 동일, compile122/20; E21 미재현. 기본안 cache0 유지, 원인 확인 없는 활성화는 계속 보류 |
| P15 | appliedSearch snapshot·`list-predicates.js` | draw마다 DOM/query parse→apply 때 준비, 행별 selector0/parse0. draft·101행 pagination/CSV·10k 실제 filterdraw·작은24행 회귀 검사 passed |
| P16 | lazy 18표·`SrStats.mount/dispose`·stable hydration·map unload | 첫 pane init, shell/draft/focus 유지. 실제 heap에서 table `info.dt`와 pagehide map listener 누수 발견→destroy 후 `.dt .DT` 제거·unload disposer. 모든 pane·20 remount·race/CSV/theme passed; 아래 heap 한계 참조 |
| P17a | bounded log read/DOM·API metadata TTL/future observer | initial64KiB·DOM2k줄/256KiB, rotation/truncation·한국어 로그·WS 권한 검사 passed |
| P17b | `ws_manager` per-client writer·신규 `ws_event_store`, WS `after`, 모바일 `WsService/PrefsInbox` | queue32/4MiB·send5s/close1s·overflow1013, thread bridge 예약32; terminal 먼저 private SQLite에 보존(최근256)→cursor replay. native history와 cursor 한 Editor.commit, 실패 commit의 메모리 cursor/trim 복구 후 reconnect. live/offline recovery·재연결 중복0 passed |
| P18 | lazy Sheets/staging atomic publish·Excel temp replace·notifier chunk/deadline/journal | 실패 live 유지·응답 유실 unknown·자동 재게시/재송신 금지; fake transport·실제 xlsx 보존 passed. 실제 Google/Telegram 전송 not-run |
| P19 | source 3엔진·Linux frozen Unicode·private Docker·실제 모바일 변환/AVD·lifespan finally | 상기 최종 676/426 및 실제 제품 패키징 검사; 자기 project/AVD만 정리. Windows/macOS/ARM와 물리 기기 실환경은 blocked/not-run |

WS cursor와 journal은 **교환 DB 스키마와 별개인 private sidecar**다. 기존 HTTP/WS 필드와 protocol3 인증을 유지하고 optional `after`, `event_id`, connected의 replay 정보만 추가한다. `after` 없는 구버전에는 과거 이력을 반복 보내지 않는다. retention 범위 밖은 `replay_gap`, 미래 cursor는 `cursor_reset`으로 명시한다. 자료는 기존 API에서 재조회한다. Android SharedPreferences는 disk commit 실패 때도 메모리를 변경하므로 실패한 history/cursor/trim만 이전 메모리 값으로 복구한 뒤 reconnect한다. 정상 경로는 한 commit이다. rollback의 disk 저장도 실패하는 fixture를 검사했다. OS notification 표시까지 포함한 exactly-once를 보장하지 않는다. pong 요구는 추가하지 않았다. terminal 순서를 위한 publish/replay lock은 느린 terminal 전송의 deadline까지 후속 terminal 순서를 지연시킬 수 있다. critical 순서 보존을 우선하며 진행 이벤트·다른 socket의 writer를 공유하지 않는다.

main의 60초 값은 worker join에 배분하는 **공유 대기 예산**이다. SQLite checkpoint·Requests DNS/slow-drip·협력하지 않는 async cancellation까지 포함한 프로세스 전체 hard wall이라고 하지 않는다. uploader·direct login·Sunwi는 stop 후 실제 살아 있는 참조를 유지하며 중복 start를 막고, auth는 취소한 retiring poll도 공유 deadline으로 정리한다.

## 성능 실측과 자원 수명

원래 SHA는 별도 detached worktree에서만 실행했다. 현재 작업트리를 reset하지 않았다. 모든 자료는 합성 데이터다. 아래 결과로 운영 자료·실제 외부 서비스 속도를 일반화하지 않는다.

| 측정 | 기존→현재 | 방법·정확성·한계 |
|---|---|---|
| 10k 실제 통계 final-ready | median 2550.171→1919.752ms, p95 2779.377→2108.303ms | 실제 Chromium·5 교대 paired run, 서버 cache flush 후 시작; 최종 모든 통계 JSON SHA 동일 |
| 10k 실제 목록 final-ready | median 14985.738→8563.129ms, p95 17095.476→9345.366ms | `aria-busy=false` 및 실제 전체 DataTables ready; 동일 JSON 21,434,530B·10000행 SHA 동일 |
| 10k 실제 DataTables filter/draw CPU | text median 9175.527→5854.960ms; empty 12155.968→5548.354ms | 실제 DOM/Chrome Performance 지표; 선택 ID 전체 동일, 30% 잠정 목표 충족 |
| 10k browser heap | stats median 6,558,652→4,530,024B; list 14,581,572→14,541,128B | 페이지별 실제 heap; process 전체 RSS로 확대하지 않음 |
| 같은 10k 서버 process | VmHWM 563364→535956KiB, 확인 시 RSS 500744→450656KiB | import·seed·여러 요청 포함, 단일 함수 RSS 아님 |
| 24행 실제 페이지 | stats median1121.205→770.385ms/p952226.649→1784.951ms; list680.223→655.864ms/p95782.549→669.211ms | 5 paired run, JSON/선택 동일; 이 작은 cohort에서 10% 초과 회귀 없음 |
| 100k actual SQL resolve(1/20/500 선택) | CPU median207.087/686.992/10720.438→15.627/38.287/101.218ms | 5회, count·모든 반환 필드 SHA 동일; 교통 표 100k/나머지 empty, NULL·한글 synthetic |
| 100k actual SQL watch(1/20/500 선택) | CPU median1264.105/1147.270/958.550→20.351/24.428/43.327ms | 동일 방법; 모든 운영 분포/쿼리 조합 전수 결과는 아님 |
| ZIP64MiB | peakRSS87140→22020KiB, wall2.071→2.069초 | 4×16MiB 비압축성 seed, 별도 process 각1회, 모든 member SHA 동일 |
| pure stats24/2400 | CPU median0.381585→0.141036초 /0.566796→0.266318초 | 10 교대 paired run, 전체 JSON 동일; UI 결과와 따로 계측 |
| Jinja 제품 cache0/400 | compile122/20, CPU2.715959/0.807898초 | 22 HTTP route·19 root template·24개 전체 compile·동일 HTML; 오류 미재현·제품 cache0 유지 |

원본: `browser-perf-summary.json`, `browser-perf-24-summary.json`, `targets-summary.json`, `zip-measurement.json`, `stats-measurement-2.json`, `jinja-product-probe.json`. 브라우저 p95는 5표본 nearest-rank여서 해당 표본의 최대값이다. OS cache drop이나 매회 process restart를 하지 않았으므로 완전한 OS/process cold p95라고 하지 않는다. 동일한 CDN bytes·지도 tile 통제와 warmup 조건을 양쪽에 적용했다.

추가 controlled 수명 측정(`lifetime-metrics.json`): 50ms fake helper40개에서 직접 sync 호출 cohort의 loop lag p95/p99는 2000.391ms(9 tick), 실제 threadpool version route는19.518ms(18 tick)였다. 멈춘 socket의 실제 기본5초 send deadline 동안 fast peer 첫 진행 이벤트는2.825ms에 도착했다. slow 연결 종료 후100건 broadcast p95/p99는0.373/0.634ms, close_all은0.102ms였고 terminal replay를 함께 확인했다. sleep·in-memory fake socket 측정이며 표본이 작고 실제 네트워크/운영 p95·RSS가 아니다. 처음 멈춘 socket 처리와 이후100건을 같은 cohort p95로 섞지 않았다.

실제 heap snapshot에서 수정 전 20회 remount 뒤 `statsSearch/saveView/renderShell/schedulePoll`의 이전 mount closure가 각각21개 남았다. 수정 후 20회와60회 모두 각각2개로 안정됐다. object self bytes162624→162200, closure215672→215528, native10570505→10614163(+0.4%)다. 반면 V8 code2455360→2649832B와 전체 live heap은 증가했으므로 **cold 기준 절대 heap10% 실험은 failed**다. 이를 통과로 바꾸거나 이전 실패 로그를 삭제하지 않았다. 계속 쌓이던 제품 mount context 원인은 제거했고, 20→60 반복의 retained object/listener 경계를 별도로 확인했다(`remount-retainers.json`, `remount-fixed-heap.json`, `remount-fixed-60-heap.json`). 제품 수명 판정과 브라우저 JIT 전체 메모리 판정을 구분한다.

## E01~E35 현재 코드 재판정

“해결됨”은 현재 코드 및 명시한 fixture/실행 범위에서 해당 원인을 제거했다는 뜻이며 모든 호스트·운영 외부 동작을 보장하는 뜻이 아니다.

| E | 분류 | 현재 근거·남은 한계 |
|---|---|---|
| E01 | 해결됨 | root/fd/link 회귀·Windows 같은 핸들 구현 및 fake protocol; 실제 Win32 추가 확인 필요 |
| E02 | 해결됨 | handler chat+user 권한·managed writer 및 실패 cleanup |
| E03 | 해결됨 | 5원천 읽기 실패 시 원래 그룹/판단 보존 |
| E04 | 해결됨 | DOM text/property·script URL 거절·실제 브라우저 |
| E05 | 해결됨 | bulk route dispatch·bounded body/type 검사 |
| E06 | 해결됨 | durable terminal outcome·실제 native failed/cancelled/unknown 알림, 구버전 기본 성공 의미 유지 |
| E07 | 해결됨 | watcher 선예약·launch failure/빠른 child 종료 |
| E08 | 해결됨 | managed owner/attempt fencing·pause/pending 보존; 협력하지 않는 I/O 강제 취소 보장 없음 |
| E09 | 해결됨 | uploader와 동일 scoped sendable count |
| E10 | 해결됨 | action freshness와 navigation cache 분리·정상 gate 인증 |
| E11 | 해결됨 | false/exception 증가·deadline/유한 retry |
| E12 | 해결됨 | first-page 재사용·count/unique ID 완전성 |
| E13 | 해결됨 | callback/ready/pins/generation·prefetch/meta 상한·실제 합성 영상 |
| E14 | 해결됨 | reservation·lost-response GET reconcile·재POST 금지 |
| E15 | 해결됨 | metric 재사용·동일 JSON 및 실제10k/24 성능 |
| E16 | 해결됨 | persisted member targeted hydration, refresh 유지 |
| E17 | 해결됨 | public full record 대상 SELECT·실제100k parity |
| E18 | 해결됨 | SQL/pandas NULL 취하 모집단 회귀 |
| E19 | 해결됨 | generation/singleflight/bytes·실제 pool inode 교체·동시 snapshot |
| E20 | 해결됨 | threadpool offload·fake 지연 동안 health/tick; 운영 latency 미측정 |
| E21 | 미재현 | 실제 제품·dict globals/include/macro·frozen 렌더에서 미재현, cache0 유지 |
| E22 | 해결됨 | applied predicate·행별 query/selector 제거·실제 full-population draw |
| E23 | 해결됨 | 첫 방문 18표·footer/CSV/카테고리 의미 유지 |
| E24 | 해결됨 | stable shell·DataTables info handler/map pagehide 누수 수정·20/60 retained mount 안정; 전체 heap10% 실험은 실패 |
| E25 | 해결됨 | 날짜/분 inclusive 끝·저장 값 불변 |
| E26 | 해결됨 | agency/law/exact/police/dedupe·동명 fixture 실제 드릴다운 |
| E27 | 해결됨 | upload/device HTTP 오류를 success로 표시하지 않음 |
| E28 | 해결됨 | bounded read/DOM·rotation/truncation·gate |
| E29 | 해결됨 | per-client writer/bytes/deadline·terminal durable replay·actual mobile reconnect; terminal 순서 lock 지연 상한은 명시 |
| E30 | 해결됨 | atomic 후보/file lock/draft·session CSRF |
| E31 | 해결됨 | diskZIP/Zip64·부분 실패 중단·response finally·죽은 process artifact 회수 |
| E32 | 해결됨 | append_many durable save1·payload batch SELECT |
| E33 | 해결됨 | retry owner 축소·manifest/lease·drain 간격·Sunwi 공유 attempt/time 예산, worker 종료 소유권; 실제 외부 latency와 DNS wall 추가 확인 필요 |
| E34 | 해결됨 | import side effect 제거·Sheets staging/unknown·bounded 알림; 실제 외부 smoke not-run |
| E35 | 해결됨 | 실제101행 page index/row ID·BOM/header/data/쉼표/인용/줄바꿈/full-filter CSV |

## DB 교환·독립 검수·보존

실제 Dart importer와 서버 restore를 함께 실행한 all-column 왕복에서 교환 계약 대상 값·NULL/빈 문자열·한글/줄바꿈·좌표·중복 판단/override·sync_meta 차이가0이었다. 서버 내부 auth/log/map_backfill_state와 신규 WS journal은 교환 대상이 아니다. watchlist의 sync_meta 표현 및 raw/entry의 조건부 매핑은 기존 계약을 따랐다. 보존 manifest의 기존 사용자 파일34개와 교환 계약/schema/converter31개는 원본 해시와 같다. statically 추출한 route131개는 원래 HEAD 대비 추가/삭제0이며 변경 Python AST25개·JS syntax2개, 기능CSV128행/중복SH-12 두 행을 최종 확인했다(`preservation-completion-final.json`, `web-contract-completion-final.json`). converter/schema/공동 정본은 이번 추가 구현에서 바이트를 바꾸지 않았다. 이를 모든 서버 내부 테이블을 그대로 모바일에 복사했다는 뜻으로 확대하지 않는다.

실제 `agy --model gemini-3.1-pro-high`를 별도 detached worktree·포트·데이터에 호출했다. 이번 후속 검수 conversation은 `cebc60dc-bb85-4625-88af-adfab26dd059`(DB), `3dd52ac3-25d4-4c29-b68b-7dfc3c4a4629`(수명/예산/모바일/종료)다. 원본 보고서와 실행 stdout/stderr는 그대로 보관했다. 첫 기능 CSV의 자동 연결, 실패한 browser의 SUCCESS 서술, 준비 전에 table0을 출력한 browser 보고는 채택하지 않았다. 엄격 Playwright assertions Chromium/Firefox2passed로 재검수한 증거만 채택했다. shutdown 검수의 “전체 hardwall60초” 서술도 코드와 달라 기각하고 worker join 공유 예산으로 정정했다. raw80tests/24.353s/exit0은 확인했다. 독립 검수의 서술을 무비판적으로 정답 취급하지 않는다.

마지막 모바일 실패 commit 복구도 같은 독립 conversation에 별도 복사본으로 검수했다(`MOBILE-PERSISTENCE-REVIEW.md`). 검수자는 직접 재실행했다고 하지 않고 실제 XML의 신규3건/실패0과 build 로그를 확인했다. 원 보고서의 “완벽한 디스크 일관성” 표현은 채택하지 않는다. SharedPreferences의 별도 Flutter 소비/읽기 사이클과 rollback 순간의 관찰까지 동기화하는 계약은 없으며, 알림 표시와 디스크 저장 사이의 exactly-once도 주장하지 않는다. 부모가 확인한 실제 native 전체 unit12건(기존7+신규5)과 build 성공만 실행 판정으로 사용한다.

기존 `VERSION`, 사용자 미추적 DB regression/preview config/문서·issue/testresults·기관 registry ZIP, 운영 자료의 기존 해시를 보존한다. 계획 입력 ZIP과 압축 해제 폴더 삭제는 이미 처리됐으며 관계없는 registry ZIP은 유지한다. 모바일의 원래 미추적 자료도 보존했다. 테스트 도구가 변경한 원래 clean pubspec.lock은 그 도구 변경만 원복했다. 별도 venv·worktree·build/AVD/heap artifacts는 `.agent-runs/`에 두고 commit하지 않는다. 전용 Docker project/volume·자기 fixture PID·자기 AVD만 종료한다. 운영 서비스·외부 제출·push·태그·릴리즈는 수행하지 않는다.

## 롤백·열린 환경 게이트

- 로컬 구현 commit의 단계/파일을 대상으로 되돌린다. 기존 사용자 변경을 대상으로 reset하지 않는다. 교환 schema/version downgrade는 없다. rating/export unknown 기록과 pending 작업은 유지하고 자동 재제출로 전환하지 않는다.
- WS는 서버/모바일 변경을 같은 배포 단위로 검토한다. 구버전은 optional 필드를 무시한다. 전달 큐를 되돌릴 때도 durable terminal journal을 남기고 소비자 cursor/history를 함께 검토한다. 새 sidecar를 교환 DB로 병합하거나 복원 과정에서 지우지 않는다.
- cache를 끄거나 pure aggregate/predicate로 성능 부분만 되돌릴 수 있다. inode pool 교체·snapshot·필수 원천 fail-closed·권한·CSRF·원자 저장은 유지한다. 통계 분모/반올림/확정·추정 의미는 성능 rollback으로 바꾸지 않는다.
- UI eager init fallback은 기존 instance/listener를 dispose한 다음 적용한다. 보존한 DataTables state/storage key를 사용한다. mount/map disposer 누수 방어는 유지한다.
- Windows source/frozen 실제 파일 boundary runner, Windows Excel/file lock, macOS x64/arm64 source/frozen, Docker ARM은 해당 호스트 runner 부재로 blocked/not-run이다. Linux WebKit을 실제 Safari/macOS passed라고 하지 않는다.
- 실제 크롤링·별점·Telegram·Sheets·중앙 계정 작업은 PROJECT_RULES.md의 별도 운영 승인 범위다. fake에서 검증한 retry/unknown/권한을 실제 제출 성공이라고 하지 않는다. 물리 모바일 기기와 Flutter 전체 account UI·크롬 확장 기기 검사는 not-run이다.
- 절대 heap10% 실험은 failed로 남는다. retained mount/listener 지속 증가 원인은 수정했으며 추가 실환경 장기 heap 관찰 시 JIT/code와 제품 객체를 함께 기록해야 한다. OS/process 완전 cold 및 운영 p95/p99는 이번 5표본 controlled 결과와 구분한다.
