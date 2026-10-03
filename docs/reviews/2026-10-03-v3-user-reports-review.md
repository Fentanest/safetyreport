# v3 사용자 제보 수정 검수

기준: `origin/dev a35b7d24e65b1f0bbd2866051fd010ed52989026`, 작업 브랜치 `fix/v3-user-reports-20261003`.
초기 상태/기준 HEAD는 `.agent-runs/v3-user-reports/initial-status.txt`, `base-head.txt`에 기록했다.
기존 VERSION의 `-dev` 미커밋 변경과 기존 untracked 산출물은 보존했다. VERSION/push/배포/태그 변경 없음.
구현: Codex. 독립 검수: `independent_review`(읽기 전용, 테스트 직접 실행 및 실제 캡처 8장 확인).
환경: Linux source Python3.14, Chromium/Firefox Playwright1.63, Docker29.8.1 linux/amd64.

## 제보별 원인·조치·확인

| 제보 | 확인한 원인 / 수정 파일 | 검증 | 잔여 제약 |
|---|---|---|---|
| 1 구형 Client 접속 | 기존 키 인증만으로 일반 API/WS 사용 가능. main.py·selfhost_compat.py·ws_auth.py·api_route.py·ws_route.py에서 인증 다음 protocol3 강제. 버전 probe 기존 필드 보존 | passed: 모든 외부 route method/path에 유효 옛키 요청409, query-key 다운로드/media409, 모든 WS/재연결4406 및 protocol3 mobile2.0.0+31 이벤트 연결. 공용12개 벡터 | 실기기/확장 실제 바이너리 not-run. 헤더는 선언된 capability이며 인증 수단 아님 |
| 2 메뉴·통계 지도 지연 | 대시보드 전체 행 로딩, registry 행별 반복 해석, 지도 주소별 pandas 재집계, HTML이 전체 통계 직렬화를 기다림. report_stats_service.py·report_cache.py·stats router/template/loader·report-map.js·DB index 개선 | passed: 5개 규모 전량 집계, SQL/JSON/RSS cold/warm 비교, 실제 메뉴 클릭/TTFB와 지도 ready 시각. 재진입 캐시, fresh 동시검증 중복 제거 | 아래 성능 표 참고. 최초500k 상세 통계는 여전히 약66초; 0/1/3k cold는 항상 빨라진 것은 아님. 실제 외부 Sunwi latency not-run |
| 3 구형 DB 복원 안내 | 기존 거절은 일반 detail이며 프런트 오류 처리/상태 정리가 불안정. 백엔드 파일/손상/스키마/주인 코드 분리 + 명확한 detail/message. exchange.py·db_backup.py·backup/API routers·backup.html | passed: 실제 브라우저 legacy/future upload409 안내 유지, full SQLite dump로 개인 DB/메타/초기화 job 불변 및 원본 불변. 기존 현행 PC↔모바일 교환 suite 유지 | 구형 자동 변환 허용 없음. 계정 불일치는 기존 별도 suite로 검증 |
| 4 초기화 배너/배경 이동 | hidden 요소에 Bootstrap d-flex !important가 남아 완료 뒤에도 표시. base/onboarding templates 수정, commit 안 scope/items 검증·terminal 멱등·실패 상태 보완 | passed: 실제 completed/reload/reentry UI, 상태기계34개 및 새 pending commit 거절. 진행/일시정지/검증/오류 UI는 명시적 fixture 응답으로 검증 | 실제 크롤링·중앙 업로드는 실행하지 않음. 로컬 완료와 outbox 재시도 분리, gaps는 승인 필요 |
| 5 긴 크롤 페이지/좁은 로그 | 범위 ops-section 닫힘 누락; 로그 무제한 textContent/전체 이력 WS. crawl.html·ops.css·crawl.py에 정상 DOM, 2열/세로, 내부 스크롤,2000줄/256KiB 배치·follow·tail64KiB·이탈 정리 | passed: 두 엔진·두 테마·4폭·200% 확대, 합성 대용량 WS 프레임과 과거 스크롤 유지/follow. 실제 관리자 WS 정상 접속 | 원본 로그 삭제 없음. 표시 잘린 과거 로그는 파일 브라우저 경로 유지 |
| 6 회색 도형 | custom tooltip connector ::before/after와 Leaflet 기본 삼각형 border 충돌. report-map.css에서 해당 tooltip 가상 요소만 제거 | passed: 실제 hover/click/닫기/zoom, 단일·다중 주소와 두 테마. marker/cluster/popup-tip/copyright 유지 | 사용자 원본 PC 캡처/실행파일 미제공: 동일 현상의 정확한 원본 환경 확정은 아님 |
| 7 공개 설정 수동 입력 | 로컬/dev 빌드가 공개 설정 없이 경고만 내고 진행할 수 있었음. 검증된 repo Variables 공개값을 community_public.json에 재사용, 실빌드 기본값/누락 실패·sample fallback·복구 안내 | passed: source/build 단위 검증 및 실제 Docker 번들 설정 configured=True, 새 자산200/MIME/개발파일 제외 | 사용자 실제 사용 이미지/EXE 확인 못함: 그 배포물의 누락 원인은 확정하지 않음. PyInstaller 실제 실행 blocked(전용 venv에 도구 없음), Windows/macOS not-run |
| 8 일부수용·법규 현황 | 처분만 그려 결과 일부수용이 보이지 않음; 유형이 신고명 집계. stats service/js/template·공용 통계 벡터에 결과축/저장법규조합 추가, lawExact drilldown | passed: 일부수용+과태료 두 사실 보존, empty/multi-law 조합 벡터, 실제 chart→목록 숫자 일치, 경찰/복합 기관 조건 재현 불가 링크 비활성화 | API report_types 기존 필드는 보존; 소비자 UI 적용은 별도 세션에서 계약 인계 필요 |

## 성능 비교

단위 초, **전체 자료**. 같은 Python/SQLite/합성 데이터와 raw projection으로 기존 기준 서비스와 새 서비스를 별도 프로세스에서 cold/warm 실행했다.
DB 읽기·집계 시간이며 HTML/네트워크 시간과 구분한다. fixture는 실제 자료의 분포를 복제한 것이 아니라 결정적 합성 분포다.
500k에서도 통계 total과 지도 점 total 합계가 **500000**임을 assertion으로 확인했다. 표본 추출 없음.

| 건수 | 대시보드 cold 전→후 | 상세 통계 cold 전→후 | 전체 지도 데이터 cold 전→후 |
|---:|---:|---:|---:|
| 0 | .0253→.0186 | .0128→.0208 | .0074→.0114 |
| 1 | .0164→.0533 | .5664→.8939 | .0209→.0744 |
| 3,000 | .0848→.0816 | 2.7329→3.4663 | 17.4777→2.2612 |
| 58,388 | 1.0766→.0709 | 27.1293→8.5907 | 29.4458→2.0920 |
| 500,000 | 9.4856→.4757 | 224.4607→66.0070 | 151.1501→10.4002 |

단일 실행 및 다른 로컬 작업 영향이 있으므로 정밀 p95 SLA로 해석하지 않는다. 작은 자료 cold 퇴행도 숨기지 않았다.
기존/후 대시보드 SQLAlchemy statement 수5→6(전량행 대신 집계+제한 recent), 통계7→7, 지도6→6.
후 warm에서는 파생 결과 캐시로 모두0이다. 캐시의 sqlite data_version observer PRAGMA는 SQLAlchemy statement 수와 별도다.
첫 화면 통계 셸은 이 집계를 기다리지 않는다. 지도/상세/Sunwi는 독립 경로다.

최종 계측 근거: `before-{건수}.json`, `after-final-{0,1,3000,58388}.json`, `after-cached-frame-500000.json`.
전체 지도 JSON bytes 전→후: 3k 1,695,185→1,695,278; 58,388 1,710,187→1,710,281; 500k 1,725,189→1,725,284.
**신규 bounded API/PC**는 각각49,474 /50,251 /50,798 bytes(최대1200개 공간 집계)다. 기존 전체 API를 조용히 자르지 않았다.
캐시는 필터·projection·DB identity/WAL/data_version·community dataset DB·registry manifest·설정·날짜를 구분한다.
정규화 지도 frame도 공유해 pan/zoom이 같은 자료를 매번 재독하지 않는다. 전체 지도 조회 뒤 frame을 재사용한
bounded 조회는58,388건 .2823s/SQL0, 500k 2.5921s/SQL0, 같은 결과 재조회는 각각 .0015s/.0016s다.
58,388건의 다른 viewport 조회는 .2271s/SQL0/28,060bytes다. 이 수치는 DB부터 완전히 cold인 첫 지도 시간과 다르며,
500k 첫 전체 지도10.4002s를 대체한다고 주장하지 않는다.

프로세스 peak RSS(KiB, fixture 준비 포함): 58,388 통계523,684→291,764, 지도545,568→297,908;
500k 통계3,179,320→1,281,480, 지도3,211,124→1,281,480. 함수 단독 peak 또는 모바일 메모리라고 주장하지 않는다.
3k baseline625,072KiB는 다른 회차보다 커서 대표 메모리 개선 수치로 사용하지 않는다.

58,388 세부 단계 계측(`after-phases-58388.json`): 대시보드 app집계 .0864s / cursor execution .045671s / 직렬화 .000045s;
통계9.4179s / .000853s / .001385s. cursor 시간은 fetch 전체 시간이 아니며 나머지에는 fetch/registry/pandas 집계가 포함된다.
일반 메뉴 게이트는 실제 브라우저 Server-Timing에서 .77–11.13ms(유효 fixture 캐시)였고 fresh 검사/동의는 약화하지 않았다.

실제 Chromium1366px·동일24건 fixture·source 기준 메뉴 측정(`browser-timing-before/after.json`):

| 화면 | 액션→요청 ms 전→후 | TTFB ms 전→후 | DOM 준비 ms 전→후 | 준비/지도 표시 ms 전→후 |
|---|---:|---:|---:|---:|
| 대시보드(메뉴 클릭) |86→69|96.1→65.2|330→188|330→188|
| 통계(메뉴 클릭) |104→72|1145.1→129.0|1909→430|2059→1081|
| 전체 지도(링크 클릭) |88→61|208.6→286.7|461→556|528→614|
| 크롤(직접 탐색, 메뉴 클릭 아님) |43→39|32.5→35.6|117→181|117→181|

통계 HTML 실제 transfer는 **203624→99630 bytes**, 대시보드63094→63980, 지도126509→127503, 크롤54222→58399.
외부 tile/Sunwi 및 지도 request/init marks는 브라우저 JSON에 분리 기록한다. 교차출처 ResourceTiming 제한 때문에
외부 처리시간/전송량을 정확히 분해했다고 주장하지 않는다. Chromium heap은 양쪽10MB로 양자화되어 정밀 메모리 비교 not-run.
운영 중앙/Sunwi 응답 시간을 측정하려고 실제 수집을 실행하지 않았다.

## 실행 및 결과

- passed: 전체 `unittest discover` 최신 회차563개 실행, 실패0, skipped5. 기본 명령에 별도 SAFETYREPORT_DATA_DIR와 FIXTURE_MODE=1을 지정.
- passed: Chromium/Firefox browser38개 + 법규/추가 API4개. 360/768/1366/1920, 라이트/다크,200% 확대(각 엔진).
- passed: 독립 검수 compatibility6, rebuild34, exchange45, user-owned DB regression1 및 새 제보 회귀9개.
- passed: source before/after route/DOM inventory 및10개 DOM id 확인, git diff --check.
- passed: 전용 Docker project `safetyreport-v3reports-20261003`, image `safetyreport:v3reports-20261003`, loopback18704.
  실제 빌드·health200·stats-loader.js200 text/javascript·template 존재·공개 설정configured=True·개발경로 제외 확인.
- blocked: Linux 실제 frozen 실행(PyInstaller 없음; 공용 venv 변경 안 함).
- not-run: Windows/macOS/arm64 번들, WebKit 이번 회차, 실제 모바일1.5.3 및 새 모바일/Chrome extension, 운영 사용자 이미지/프로그램.
- failed→수정/재확인: 최초 map hover는 같은 좌표의 하단 마커가 가려 검수 액션 실패(최상단 마커 직접 조작으로 재현 정확화).
  미래 DB 안내 테스트의 예상 문구는 실제 "더 새 버전…" 계약으로 정정했고 두 엔진 재확인.
  단위시험 seed가 기존 title 때문에 건너뛰어진 것을 발견해 실제24건 빈 fixture로 고치고 full row 동등성을 검증했다.

## 독립 검수 발견 처리

최초 지도 응답의 분류 역전/필터 누락, 재현 불가 법규 링크, 자정 캐시, bounded meta.address_groups,
페이지 count/page 뒤 재projection 경쟁을 모두 수정·재확인했다. 마지막 경쟁은 대표건1→2 동시 변경을 주입해
`total:2,count:0,next_offset:0` 재현 후 `total:2,count:1,next_offset:1`로 전진함을 별도 실행자가 확인했다.
독립 검수자는 직접 브라우저 조작은 not-run이며 실제 캡처8장의 시각 검수와 Node/서버 시험만 수행했다.

## 인계

모바일/확장은 [selfhost-compat 정본](../../contracts/selfhost-compat/README.md)과 vectors.json 및 갱신한
stats-overview-vectors.json을 자기 작업 세션에서 동기화한다. 서버 probe의 top-level 메타데이터,
정확한 제품 VERSION 헤더, protocol3, query WS 필드,409 code/4406, 페이지/지도 추가 API를 그대로 구현한다.
다른 저장소 파일은 수정하지 않았다. 전통적인 구버전 접근 허용 설명보다 protocol3 차단이 우선이다.
증거/캡처/trace/비밀 없는 fixture 로그는 `.agent-runs/v3-user-reports/`(Git 미추적).

## 부작용·판정

정상 게이트를 loopback fake 중앙 세션/동의로 확인했다. fixture 가드에서 GitHub/Sunwi/크롤러/별점/Telegram/Sheets/외부 업로드를 차단했다.
운영data/계정/컨테이너는 변경하지 않았다. 본 작업 전용 test 컨테이너/volume만 정리하며 합성 자료는 fixture로 재생성 가능하다.
조건부: 위 source/fixture/브라우저·Docker 범위에서 통과. 실배포물·실기기·cold500k 상세 통계의 남은 비용을
통과했다고 확대 해석하지 않는다. 사용자의 시각 승인이나 제품 릴리즈 승인을 대체하지 않는다.
