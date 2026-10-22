# C(PC 핵심) 보고서 · 2026-10-22 1회 전송 재개·업로드 일시 중지·필수 버전 잠금

작업 폴더: `backfill-resume-ws/pc`(safetyreport, feat/backfill-resume, 기준 dda96e7). VERSION 3.6.1 그대로. 커밋·push 하지 않았다. 원격 서버에 접속하지 않았다.
정본: 지도 레포 `docs/implementation/backfill-resume-20261022/CLIENT-RULES.md`(이하 규칙 문서). 규칙 문서와 지시서가 다른 곳은 규칙 문서를 따랐다.

## 1. 바꾼 파일

| 파일 | 내용 |
|---|---|
| `services/community_raw_backfill.py` | `classify_backfill_error(exc)`·`transient_reason(exc)`·`BackfillTransient`. 일시적 오류 → run `paused`(60·300·900·3600초, `transient_failures`), 신고 횟수·커서 그대로. 고정적 3회 → `hold_item`(hold_count·next_attempt_at·first_held_at·stale, 15분·1시간·6시간·24시간). 채우기 slice 시작에 시각 지난 보류를 `pending`(attempts=2). 업로드 확인 retry 3회 → hold(`upload_retry`). 보류만 남으면 marker `next_attempt_at`=가장 이른 보류 시각 + 스케줄러 1회 작업(`community-raw-backfill-wake`). 채우기 slice 에서 새 queued ≥1 이면 업로더 1회 깨우기(run 당 최대 1회, 중지·잠금 중 금지). 업로드 중지면 run `paused`/`maintenance`(재개 시각+지터 또는 null), 잠금이면 아무것도 안 함. `unstick_once`(2-3), `start()` 에서 잠금 검사·1회 초기화. marker 에 `run_started_at`·`updated_at` |
| `services/community_backfill_store.py` | `retry`(보류 필드 초기화), `release_due_holds`, `next_hold_at`, `counts`(진행 보고 개수·stale) — SQLite JSON1 |
| `services/community_raw_backfill_runtime.py` | 공식 사이트 로그인 때 2-7 `official_login_started`·`official_login_ok`·`official_login_failed`(reason). 상세 API 일시 실패를 `BackfillTransient(사유)`로(`detail_retry_reason`: network·timeout·no_result·rate_limited·server_5xx). 2-6: 6개월 지난 사진 보조 값 표시 `expired`. `upload_pause()`·`update_locked()`, 중지·잠금 중 `wake_upload` 안 함 |
| `services/community_upload_pause.py`(새) | 2-9 저장·판정(`meta.upload_pause`), status·503 반영, 재개 시각+0~120초 1회 작업(`community-upload-resume`), 재시작 재예약, 배너 문구(KST), 일찍 재개되면 503 으로 미룬 행 바로 보냄 |
| `services/community_update_lock.py`(새) | 2-10 버전 비교(`version-vectors.json` 규칙)·잠금 판정(규칙 문서 6-2 표)·저장(`meta.client_lock` = `{locked, min_version, url, message, decided_at}`, E 와 합의)·메모리 캐시·화면 요약 |
| `services/community_progress_trigger.py`(새) | 2-1 보내는 때(start·state_change·periodic·daily) 판단과 `backfill`·`optimize`·`official_login` 값 만들기 → E 의 `maybe_send`, 그 뒤 `last_maintenance(store)` 를 읽어 중지로 옮김(같은 seen_at 은 한 번). 2-8 안내 입력값 |
| `services/community_gate.py` | 정상 status(`status_wellformed`)에서만 중지·잠금 반영. 게이트 요약에 `upload_pause`·`update_lock`. `report_setup()`(2-7 호출 지점 모음): 미설정 공식 계정 `official_login_failed`, 연결 확보 `connection_registered`. `UPDATE_REQUIRED` 크롤 차단 |
| `services/community_account_client.py` | 모든 요청에 `X-Client-Version`·`X-Client-App`(`pc/windows|linux|macos|docker`), `setup_progress()`(E 용) |
| `services/community_account_ops.py`, `web/routers/community_route.py` | 동의 성공 `consent_ok`, 카카오 확정 `kakao_ok`(웹·API 둘 다) |
| `services/community_cloud.py` | 503 `maintenance` 는 전역 쿨다운을 걸지 않고 중지를 저장(status 확인은 계속) |
| `services/community_uploader.py` | 잠금 → `blocked_gate`/`update_required`, 중지 → `cooldown`/`maintenance`(요청 0회). 503 maintenance 행: attempt 되돌림, `retry_wait`, 다음 시도=재개 시각. 다음 깨울 시각 처리 |
| `services/community_ingest_client.py` | 중지·잠금 중 manifest 요청 안 함 |
| `services/community_schedule.py` | `schedule_once()`(1회 작업), 60초 poll: 잠금이면 업로더·1회 전송 시작 안 함, 진행 보고 tick, 시작 때 중지 재개 작업 재예약 |
| `services/crawl_control.py`, `web/routers/crawl.py`, `web/routers/api_route.py` | 잠금 중 크롤(예약 포함) 409 `COMMUNITY_UPDATE_REQUIRED` |
| `services/selfhost_compat.py` | 잠금 중 Client API·WS → 기존 `SERVER_UPGRADE_REQUIRED` 409 형식 |
| `main.py` | `update_lock_middleware`(관리자 인증 안·게이트 밖): 화면 GET 은 잠금 화면, 그 밖 409. 게이트 통과 때 진행 보고 start. 공지 경로 3개 `_GATE_ALLOW`(게이트 화면에도 팝업이 뜨므로), 라우터 등록 |
| `web/routers/announcement_route.py`(새) | 관리자 키(=`session['admin_username']`), `GET /api/announcements/pending`, `POST /api/announcements/{id}/snooze|dismiss`, 템플릿 함수 `sr_notices`(update_required=False)·`sr_announcements`(화면 그리기에서 네트워크 안 기다림), 로그인 직후 `on_login` |
| `web/routers/auth_route.py`, `core/utils/templating.py`, `web/routers/community_onboarding_route.py` | 로그인 성공 직후 `app_announcements.on_login(session['admin_username'])`, 두 Jinja 환경에 템플릿 함수 등록 |
| `web/templates/base.html`, `web/static/ui/shell-polling.js` | 점검 배너(`srUploadPauseBanner`, textContent 만), `srClientNotice` 근처에 `partials/notice_banner.html`, `</main>` 바로 뒤에 `partials/announcement_popup.html` include(`{% with notices=… %}`·`{% with announcements=… %}`) |
| `web/templates/update_required.html`(새) | 관리자 웹 전체 잠금 화면(60초마다 해제 확인 후 새로 고침) |
| E 자리표시(새, 빈 구현) | `services/community_progress_report.py`(`maybe_send`, `last_maintenance`), `services/community_setup_report.py`(`report`), `services/user_action_notice.py`(`evaluate`), `services/app_announcements.py`(`on_login`, `pending_for_admin`, `snooze`, `dismiss`), `web/templates/partials/notice_banner.html`·`announcement_popup.html`(빈 파일) — 시그니처는 지시·E 보고서 그대로. 합칠 때 E 파일이 덮어쓴다 |
| `contracts/community-ingest-v2/` | 지도와 같은 바이트(새 4: `progress.schema.json`·`client-requirement.schema.json`·`version-vectors.json`·`vectors/progress.json`, 바뀐 4: README·errors·account-api·MANIFEST) |
| `tests/test_backfill_resume.py`(새, 43), `tests/test_raw_backfill.py`(사진 expired 확인 1줄), `tests/test_app_factory.py`(미들웨어 순서에 `update_lock_middleware` 추가 — 의도한 변경) | 시험 |
| `docs/architecture/community-gate.md`, `community-upload.md`, `CHANGELOG.md`(미출시) | 2-5 한 줄(Client 휴대폰은 서버와 같은 카카오 계정만), 중지·잠금·재개 규칙, 미출시 항목 |
| `docs/implementation/backfill-resume-20261022/evidence/`(새) | 브라우저 증거 스크린숏 4장, `verify_c.cjs`, `report.json`(`.gitignore` 의 `*.json` 에 걸려 추적 안 됨 — 필요하면 검수자가 강제 추가) |

## 2. 판단

1. **오류 분류표(PC 구현)**: requests·urllib3·curl_cffi 의 Timeout → `timeout`, ConnectionError·ChunkedEncodingError·ProxyError·DNS·SSL → `network`. curl_cffi 의 나머지 `CurlError`(전송 계층)도 `network`, 단 `InvalidJSONError`·`HTTPError`·URL 오류는 고정적. 내장 `ConnectionError`·`socket.gaierror`·`ssl.SSLError`·네트워크 errno 의 `OSError` → `network`(ENOENT 같은 errno 는 고정적). `sqlite3.OperationalError`·SQLAlchemy `OperationalError` 중 locked/busy 만 `db_locked`. 상세 API 일시 실패(`_fetch_envelope` 의 retryable)는 note 로 사유를 가른다(`network:`→network/timeout, `No result key`→no_result, 429→rate_limited, 그 밖→server_5xx). `parse_retry`·`detail_save_retry`·`missing_report`·`raw_event_not_created`·파서 예외·분류 못 한 예외는 고정적.
2. **목록 실패·연속 고정적 5회의 휴지**도 같은 일시적 간격표(60·300·900·3600)와 같은 `transient_failures` 를 쓴다(예전 300초 고정). 목록 성공·한 건 진행·'다시 시도'에서 0.
3. **보류만 남은 run 의 marker `next_attempt_at`**: 확인 대기(queued)가 남아 있으면 0으로 둔다(업로드 확인을 15분 이상 늦추지 않으려고). 보류만 남았을 때만 가장 이른 보류 시각.
4. **보류에서 성공하면** 항목의 보류 필드(hold_count 등)를 지운다(나중에 다른 이유로 실패하면 간격이 처음부터).
5. **2-3 표 밖 경우**: `paused` + `official_login_retry`(수동 아님)·`official_login_required` 지만 `login_fp` 없음(상세 403 등 비영구)은 "로그인 아님"과 같이 휴지를 0으로 풀었다(사용자 로그인이 꼭 필요한 상태가 아님). `holds` 를 `filling` 으로 열 때 `next_attempt_at` 도 0.
6. **업로드 중지와 전송 경계**: 503 `maintenance` 를 `community_cloud.run` 이 알아본다 — 예전에는 503 이면 Supabase 전체를 Retry-After(최대 수 시간)만큼 멈춰 status 확인까지 막고 게이트가 `cloud_unavailable` 로 보였다. 이제 전역 쿨다운을 걸지 않는다. 이 경계를 지나는 진행 보고(E)의 503 도 자동으로 중지를 저장한다.
7. **status 가 끝내 키를 주지 않는 중지**(auth 가 아직 옛 배포 등): 규칙 문서는 "키가 없으면 저장된 판정 유지". 503 으로만 알게 된 중지는 영영 풀리지 않을 수 있어, 재개 시각(없으면 Retry-After, 기본 600초)이 지난 뒤 키 없는 status 를 받으면 저장을 지워 한 번 다시 시도하게 했다(아직 중지면 그 요청이 다시 503 을 받아 새로 저장). status 에서 온 중지는 그대로 유지.
8. **일찍 재개**되면 503 으로 재개 시각까지 미룬 outbox 행(`last_error_code='maintenance'`)의 `next_retry_at` 을 지금으로 당긴다. 503 행의 대기는 업로드 정책의 서버 지시 상한(24시간)에 걸린다 — 재개 시각이 더 멀어도 중지 판정이 요청을 막으므로 실제 요청은 없다.
9. **잠금 화면 범위**: 관리자 화면 GET 은 잠금 화면(HTTP 200), 그 밖 관리자 요청(POST·AJAX·`/media`)은 409 `COMMUNITY_UPDATE_REQUIRED`. 로그인·로그아웃·`/settings/community/gate`·`/settings/community/status`·`/health`·`/version/latest`·정적 파일은 통과(해제 확인용). Client API 는 `/api/v1/server/version` 만 열고 나머지는 기존 `SERVER_UPGRADE_REQUIRED` 409(본문 키 5개 그대로, 문구만 11절 "서버 프로그램 업데이트가 필요합니다. …"). 관리자 세션 WS 는 막지 않았다(잠금 화면이 WS 를 열지 않음). 자동 별점·DB 최적화는 규칙 문서 정지 목록에 없어 막지 않았다.
10. **잠금 판정 입력**: 게이트가 `status_wellformed(raw)`(gate.kakao bool·contributor.status·consent.state 문자열)일 때만 부른다. `client_requirement` 가 객체인데 `min_version` 이 객체가 아니거나 pc 값이 문자열·null 이 아니면 "모양 틀림"으로 유지. 해제할 때 업로더를 깨운다.
11. **진행 보고 trigger 나누기**: C 는 "무엇이 바뀌었나"만 본다 — 같은 프로세스 첫 tick 은 `start`, (state, phase, reason, official_login, optimize 3개) 가 이전 tick 과 다르면 `state_change`, 진행 중 상태면 `periodic`, `done`·run 없음(`none`)은 `daily`. 15분·하루 1번·60초 간격은 E 가 마지막 전송 저장으로 거른다. run 없음(`none`)도 `daily` 로 넘긴다. 개수: 항목 표가 비어 있고 목록이 끝나지 않았으면 null(모름). `reason` 은 `paused` 일 때만, `next_attempt_at` 은 `paused`·`holds` 일 때만. 진행 보고는 활성 연결(context)이 있을 때만 부른다.
12. **설정 단계 보고(PC)**: PC 는 설정 단계에서 공식 사이트에 로그인해 보지 않는다. 그래서 (a) 안전신문고 계정이 설정되지 않아 연결을 못 만들 때 `official_login_failed`(reason `official_account_required`, 게이트), (b) 1회 전송이 공식 사이트에 로그인할 때(`RuntimeBackend.connect`) `official_login_started` → `official_login_ok` / `official_login_failed`(reason `invalid_credentials`=로그인 거부, `official_account_required`, `network`, `timeout`, 그 밖 `login_failed`). (b) 는 보통 연결 등록 뒤라 E 가 거른다(`connection_registered` 뒤 전송 금지). `kakao_ok` 는 `service.confirm`(세션 저장) 뒤, `consent_ok` 는 동의 성공 응답 뒤, `connection_registered` 는 연결 확보 때마다(새 등록·기존 연결 재결합 모두) — 반복 거르기는 E. 크롤 서브프로세스의 로그인은 다른 프로세스라 붙이지 않았다.
13. **관리자 계정 키**: `request.session['admin_username']` 그대로(검수자·E 합의 — 처음에는 해시로 했다가 바꿨다). '보지 않기' 파일(`data/announcements/snooze.json`)에 아이디가 그대로 들어간다. 아이디를 바꾸면 '보지 않기'가 처음부터.
14. **공지 경로**: `GET /api/announcements/pending` → `{"data":[…]}`(동기 라우트 = 스레드풀), `POST /api/announcements/{id}/snooze`, `POST /api/announcements/{id}/dismiss`(관리자 세션 + CSRF 헤더 — `session-requests.js`·`x-csrf-token`, JSON `{}`, id `^[a-z0-9][a-z0-9_-]{0,79}$`, 응답 `{"status":"ok"}`, 잘못된 id 400, 로그인 없음 401). `/api/v1` 밖이라 API 키가 아니라 관리자 인증·CSRF 를 탄다. 게이트 예외(`_GATE_ALLOW`)에 넣었다 — fixture 게이트 화면(base.html)에서도 팝업이 떠서, 넣기 전에는 '보지 않기'가 403 이었다(브라우저로 재현 후 고침). '닫기'는 E 의 `dismiss`(메모리, `on_login` 이 비움)를 부르고 로그인 세션에도 id 를 적는다.
14-1. **화면 그리기와 공지 네트워크**: E 의 `pending_for_admin` 은 캐시가 6시간 지나면 그 자리에서 Edge 를 부른다. 템플릿 함수는 async 라우트에서도 불리므로, E 모듈의 `_cache`·`CACHE_SECONDS` 로 캐시가 오래됐으면 이번 화면은 빈 목록으로 그리고 백그라운드로 `refresh()` 한다(다음 화면에 뜬다). 로그인 직후 `on_login` 이 백그라운드로 받으므로 로그인 다음 첫 화면에서는 아직 안 뜰 수 있다.
15. **2-6 사진 표시**: 지시서 범위표상 E 몫이지만 작업 지시 4번이 C 로 명시해 `community_raw_backfill_runtime.py` photo 블록을 고쳤다.

## 3. 2-3 기기 상태 변화 표 (첫 실행 1회, `raw_backfill_unstick="2026-10-22.1"`)

| 지금 상태 | 바꾼 뒤 |
|---|---|
| 항목 `hold` | `pending`, attempts 0, hold_count 0, stale false, next_attempt_at 없음, event_id 없음 |
| 항목 `not_eligible` + `error='upload_rejected'` | `pending`, attempts 0, eligible 1, error 지움 |
| 항목 `not_eligible`(삭제·`agency_code_too_long`·`not_eligible` 등) | 그대로 |
| 항목 `stored`·`queued`·`raw_unavailable`·`pending` | 그대로 |
| marker `paused` + 로그인 아님(또는 수동 표시 없는 로그인 사유) | `next_attempt_at=0`, `transient_failures=0`, `failures=0` |
| marker `paused` + `official_login_required` + `login_fp`(수동) | 그대로 |
| marker `done`/`holds` + 되돌린 항목 ≥1 | `state='filling'`, `phase='filling'`, `cursor=0`, `next_attempt_at=0` |
| marker `done` + 되돌린 항목 0 | 그대로 |
| marker `waiting_optimize`·`listing`·`filling`·`uploading` | 그대로(되돌린 항목은 다음 slice 가 처리) |
| run 없음 | 표시만 쓰고 끝 |

`start()` 가 v2 준비·끝난 run 검사보다 먼저 한 번 부른다(끝난 run 도 풀어야 하므로). 한 트랜잭션(BEGIN IMMEDIATE).

## 4. 새 문구 (2-13)

| 위치 | 문구 |
|---|---|
| 관리자 웹 상단 점검 배너(재개 시각 있음) | 서버 점검 중입니다. 신고 업로드를 잠시 멈췄고, {오후 3시 30분 / 10월 23일 오전 9시}에 자동으로 다시 올립니다. (분이 0이면 "오전 9시") |
| 같은 배너(재개 시각 없음) | 서버 점검 중입니다. 신고 업로드를 잠시 멈췄고, 점검이 끝나면 자동으로 다시 올립니다. |
| 같은 배너 둘째 줄 | 서버 `pause_message` 평문(HTML 해석 금지) |
| 같은 배너 접근성 이름 | 서버 점검 안내 |
| 잠금 화면 제목 | 앱을 업데이트해 주세요 |
| 잠금 화면 본문 | 이 버전은 더 이상 신고를 공유할 수 없습니다. 최신 버전으로 업데이트하면 이어서 쓸 수 있습니다. 기기에 있는 신고 자료는 그대로 남아 있습니다. |
| 잠금 화면 버전 줄 | 필요한 버전: {3.6.2} 이상 |
| 잠금 화면 현재 버전(새) | 지금 버전: {3.6.1} |
| 잠금 화면 서버 문구 | 서버 `message` 평문 |
| 잠금 화면 버튼 | 업데이트 / 로그아웃(기존 말) |
| 잠금 중 관리자 요청·크롤 시작 거절(새) | 앱을 업데이트해 주세요. 업데이트하기 전에는 신고 수집을 시작할 수 없습니다. |
| 잠금 중 Client API 409 `SERVER_UPGRADE_REQUIRED` 의 message | 서버 프로그램 업데이트가 필요합니다. 서버 관리자에게 업데이트를 요청해 주세요. |

본문 "기기에 있는"은 규칙 문서 11절 원문 그대로 두었다(PC 에서는 "이 PC 에 있는"이 더 정확 — 사용자 검토 대상).

## 5. 시험

| 명령 | 결과 |
|---|---|
| `SAFETYREPORT_DATA_DIR=$(mktemp -d) <safetyreport venv>/bin/python -m unittest discover -s tests -p "test_*.py"`(작업 시작 직후, 코드 수정 전) | Ran 1462, OK(skipped 6) |
| 같은 명령(변경 중간) | Ran 1503, 실패 3 → 고침: 미들웨어 순서 기대값(의도한 변경, 시험 갱신), 공지 경로가 게이트 예외 목록 밖(`_GATE_ALLOW` 에 넣음), `selfhost_compat` 의 기록 없는 대체(`note_fallback` 추가). 세 파일 단독 재실행 OK |
| 같은 명령(최종, C 폴더) | **Ran 1505, OK(skipped 6)** |
| 같은 명령(최종, C+E 합친 사본 — 스크래치 폴더에 C 폴더 + E 의 새 파일 10개·`contracts/app-announcements/`) | **Ran 1552, OK(skipped 6)** (E 시험 47 포함. 처음 한 번은 C 시험 하나가 E 의 실제 공지 캐시 상태를 가정해 실패 → 시험을 캐시 상태와 무관하게 고침) |
| `python -m unittest tests.test_backfill_resume` | Ran 43, OK |
| `python -m unittest tests.test_raw_backfill` | Ran 10, OK(공용 모바일 trace 픽스처 포함, 사진 `expired` 확인 추가) |
| `python3 map/scripts/raw-ingest/check_contract.py --copies ../pc` / `--copies ../pc ../mobile` | PASS 15 files, 1 copies / 2 copies |

`tests/test_backfill_resume.py` 가 덮는 지시서 3절 C 항목:
- 2-2: 분류표 벡터(일시적 19종·고정적 13종), 상세 note 사유, **일시적 오류 40초 연속(5초마다 9회) → 보류 0·attempts 0·커서 그대로·run `paused`/`network`**, 간격 60·300·900·3600·3600 과 진행 시 0, 고정적 3회 → hold + `next_attempt_at=now+900`, 899초 그대로·901초 자동 pending → 재실패 hold_count 2·1시간, 성공 시 queued·보류 필드 삭제, 간격표·30일 stale, 업로드 확인 retry 3회 → hold(`upload_retry`), blocked → not_eligible, '다시 시도' 즉시(SQL·메모리 둘 다), 보류만 남은 run 의 잠듦·깨움, 1회 작업 예약.
- 2-3: 표 7조합(done·done(되돌림 0)·holds·paused 일반·paused 수동 로그인·filling·uploading) 기대 상태, 두 번째 실행에서 다시 안 바뀜, run 없음, 풀린 done run 이 다시 done 까지.
- 2-4: 새 queued 있는 slice → wake 1회, 없음 → 0, 중지·잠금 → 0.
- 2-9: status 정본·키 없음 유지·형식 이상 유지·연장·조기 재개, 지터 0~120초, 재시작(캐시 비움) 뒤 유지, 503 본문 저장·재개 시각 없으면 타이머 없음·키 없는 status 의 fallback, 배너 문구 3종(KST), 전송 경계 503 maintenance 는 전역 쿨다운 없음(일반 503 은 그대로), **실제 업로더 `_run_upload` 중지 동안 realtime·recovery·manual 모두 업로드 요청 0회**, 잠금 동안 0회, manifest 요청 0회, 503 행 attempt 안 늚·다음 시도=재개 시각·일찍 재개하면 바로, 1회 전송 `paused`/`maintenance` 동안 attempts·transient_failures 0, 풀리면 바로 이어 감.
- 2-10: `version-vectors.json` 24건, 판정 표(잠금·키 없음/모양 틀림/해석 불가 유지·플랫폼 요구 없음 해제·null 즉시 해제·같음/높음 해제·해석 불가는 새로 잠그지 않음·재시작 유지), 게이트가 네트워크·5xx·429·형식 이상 응답에서 판정을 부르지 않음(먹통 회귀), 크롤 차단(수동·예약), 버전 헤더(모든 community-account 요청, 4 플랫폼, 버전 없으면 헤더 생략), 실제 앱 `/api/v1/community/gate` 409 `SERVER_UPGRADE_REQUIRED`(본문 키·no-store)·`/api/v1/server/version` 열림, 관리자 화면 3경로 잠금 화면·서버 문구 이스케이프·POST 409·해제 뒤 원래 화면.
- 2-1(PC 쪽): trigger 순서(start → state_change → periodic → state_change → daily, 개수만 바뀐 것은 state_change 아님), 값이 `progress.schema.json` backfill 필수 키·패턴·enum 과 맞음, 중지·잠금·연결 없음이면 부르지 않음, official_login 3값.
- 공지 경로: 관리자 키, CSRF 없으면 403, snooze·dismiss 전달, 잘못된 id 400, 닫은 공지 거르기, 템플릿 함수 실패 시 빈 목록.

### 실제 브라우저 증거 (fixture 서버, 합성 자료, Chromium headless, 1366×900·390×844)
`node evidence/verify_c.cjs <out> <api-key>`(`PLAYWRIGHT_MODULE` 로 @playwright/test 위치 지정, fixture 포트 18671·18672, 끝난 뒤 PID 로 종료).
- 잠금(`meta.client_lock` 을 넣고 기동): `/`·`/data/all`·`/settings/`·`/crawl/` 모두 잠금 화면, 서버 문구의 `<b>` 가 글자 그대로, 업데이트 버튼 `https://github.com/Fentanest/safetyreport/releases/latest`(새 탭), 가로 넘침 없음, 화면 안 `POST /crawl/start` → 409 `COMMUNITY_UPDATE_REQUIRED`, 외부 요청 0. 콘솔 오류는 그 409 하나(의도한 요청).
  실제 앱 Client API `/api/v1/community/gate` → 409 `SERVER_UPGRADE_REQUIRED`, `/api/v1/server/version` → 200. 스크린숏 `evidence/lock-desktop.png`·`lock-phone.png`.
- 점검(`meta.upload_pause`, 재개 KST 19:30): `/onboarding/cloud`(fixture 는 커뮤니티 세션이 없어 게이트 화면 — base.html 을 쓰는 화면) 상단 배너 "서버 점검 중입니다. 신고 업로드를 잠시 멈췄고, 오후 7시 30분에 자동으로 다시 올립니다." + 둘째 줄 서버 안내, `<script>` 가 글자 그대로(스크립트 노드 0), 가로 넘침 없음. 콘솔 오류 1건은 `GET /maintenance/status` 403 — fixture 가 게이트에 막힌 상태의 기존 동작(이번 변경과 무관). 스크린숏 `evidence/pause-desktop.png`·`pause-phone.png`.
- **C+E 합친 사본**(스크래치 폴더, E 파일은 복사만, `pc-ui` 에 쓰지 않음, 포트 18673): 공지 캐시에 계약 예시 공지 1건, `upload_pause`, 1회 전송 marker(`paused`/`official_login_required`/수동) 를 넣고 기동.
  게이트 화면에서 점검 배너 + 사용자 조치 안내 "안전신문고 로그인이 필요합니다. 앱에서 다시 로그인해 주세요." [로그인] + 공지 팝업(제목·본문 두 줄·[플레이스토어에서 보기]·[일주일 동안 보지 않기]·[닫기]).
  '일주일 동안 보지 않기' → `POST /api/announcements/example-store-link/snooze` 200, 팝업 닫힘, 새로 고침해도 안 뜸, `GET /api/announcements/pending` → `{"data":[]}`. 다른 브라우저 문맥(휴대폰 폭)에서도 안 뜸(관리자 계정별 파일 저장). 스크린숏 `evidence/merged-desktop.png`·`merged-phone.png`, `merged-report.json`.
  콘솔 오류는 `GET /maintenance/status` 403(게이트에 막힌 fixture 의 기존 동작)뿐.
- Muse 브라우저 검수 아님(Claude headless Playwright).

## 6. NOT RUN

- 실제 Supabase(로컬 스택 포함)와의 status·503·진행 보고 왕복 — 로컬 스택을 띄우지 않았다(서버 쪽은 A 의 스택 시험). PC 쪽은 가짜 응답 단위 시험.
- 게이트를 통과한(카카오 세션·동의가 있는) fixture 화면에서의 배너·공지 팝업 — `community_qa_session.py` 는 로컬 Supabase 스택이 필요하다. 배너는 게이트 화면(base.html)에서 확인했다.
- E 의 실제 진행 보고·설정 단계 보고·안내·공지 구현과의 합친 시험(합칠 때 검수자).
- PyInstaller·Docker 빌드(릴리스 경로 승인 없음). 새 템플릿은 `web/templates` 통째 포함 경로, 새 모듈은 정적 import 로 잡힌다.
- `tools/web-tests` Playwright 스위트(이 작업 폴더에 node_modules 없음) — 위 단독 스크립트로 대신했다.

## 7. 남은 위험

- 이미 배포된 3.6.1 이하는 이 잠금·중지 코드가 없다(서버 503·426 과 지도 웹 안내만 듣는다 — A 보고서 3절 표).
- 업로드 정책의 서버 지시 상한(24시간) 때문에 재개 시각이 24시간보다 멀면 503 행의 `next_retry_at` 이 재개 시각보다 이르다. 중지 판정이 요청을 막으므로 실제 요청은 없지만, 판정이 지워진 뒤에는 그대로 보낸다(의도).
- 잠금 판정은 메모리 캐시를 쓴다. `community.db` 를 다른 프로세스(수집 서브프로세스)가 바꾸는 경로는 없지만, 손으로 meta 를 고치면 재시작 전까지 반영되지 않는다.
- 진행 보고 trigger 의 "이전 상태"는 프로세스 메모리라, 재시작하면 항상 `start` 부터다(규칙 ①과 같은 뜻).
- 2-8 안내·2-11 공지의 템플릿 함수는 관리자 화면을 그릴 때마다 `user_action_notice.evaluate`·`app_announcements.pending_for_admin` 을 부른다. E 의 `pending_for_admin` 은 네트워크를 화면 그리기에서 직접 기다리지 않아야 한다(아래 요청).

## 8. 요청

### E(PC 주변) — E 보고서 "요청 (C 가 붙일 곳)" 반영 결과
- 반영함: meta 키 `upload_pause`(`{paused, resume_at(epoch|null), message, source, saved_at, wake_at, fallback_at}`)·`client_lock`(`{locked, min_version, url, message, decided_at}`), `last_maintenance` → `upload_pause`(source `503`), `on_login(session['admin_username'])`, 공지 경로 3개, `notices`(update_required=False)·`announcements` include 위치, 설정 단계 호출 지점.
- `maybe_send` 는 60초마다 불린다(trigger 는 C 가 정함, E 의 `should_send` 가 시간 판정). `backfill` 은 run 이 없어도 14키 전부(state `none`, 나머지 null)를 넘긴다 — E 의 `build_body` 가 받는 모양.
- E 의 `_blocked()` 는 `upload_pause.resume_at` 이 지났으면 막지 않는다. C 는 status 로 확인될 때까지 업로드를 막는다(규칙 5절) — 진행 보고만 먼저 한 번 나갈 수 있고 서버가 503 이면 다시 기록된다. 문제는 없다고 봤다.
- 진행 보고 요청이 `community_cloud.run` 을 지나지 않아 503 이 전역 쿨다운에 들어가지 않는다 — 그대로 좋다(C 가 `last_maintenance` 로 옮긴다).
- [로그인] 버튼 `/settings/` 는 그대로 두었다(공식 계정 입력 칸이 설정 화면에 있고 더 정확한 앵커가 없다).

### 검수자
- `evidence/report.json` 은 `.gitignore`(`*.json`)에 걸린다 — 남기려면 `git add -f`.
- 잠금 화면 본문 "기기에 있는 신고 자료"(규칙 문서 원문)를 PC 에서 그대로 둘지 사용자 확인.

## 9. 운영 적용 순서 (제안만)
지시서 4절 그대로: auth·지도 migration → Edge → **PC 3.6.2**(이 변경) → 모바일 2.5.1 → 파서 이미지 → 운영 콘솔 → `client_notice` 최소 버전 → 공지 게시.
PC 3.6.2 를 내기 전에 E 의 실제 구현을 합치고 `pytest`(unittest discover) 전체와 게이트 통과 fixture(로컬 스택) 화면 확인을 한 번 더 한다.

## 10. 통합 후속 (검수자가 E 파일을 이 폴더에 합친 뒤)

### B(모바일)와 동작 맞추기
| B 규칙 | PC 이전 동작 | 조치 |
|---|---|---|
| ① 업로드 확인 단계 끝에 `pending` 이 남아 있으면 채우기(cursor 0)로 돌아간다 | 다름 — 업로드 확인 `retry`·`rebind` 때만 채우기로 돌아갔다. 1회 초기화가 `uploading` run 에서 되돌린 항목(`upload_rejected`→pending)은 영영 다시 채우지 않았다 | `community_raw_backfill.py` 업로드 단계 끝에서 `repo.counts(run)['pending']` 이 있으면 `phase='filling', cursor=0`. 시험 `test_uploading_run_with_pending_goes_back_to_filling`(uploading 그대로 → 다음 run 채우기 → 다시 보냄 → done). 공유 사례 파일 `raw_backfill_cases.json` 은 그대로 통과 |
| ② 2-3 초기화: `official_login_required` 인데 수동 표시(`login_fp`)가 없는 paused 는 대기 0 | **이미 같음** — `unstick_once` 는 `reason=='official_login_required' and login_fp is not None` 일 때만 그대로 두고 나머지 paused 는 `next_attempt_at=0`(보고서 판단 5) | 고치지 않음. 근거 시험 `test_login_reason_without_manual_mark_is_released` 추가(기존 표 시험도 수동 표시 있음 → 그대로를 확인) |

### PC 화면 문구: "앱" → 프로그램(검수자 결정)
| 위치(파일) | 바꾸기 전 | 바꾼 뒤 |
|---|---|---|
| 상단 안내 공식 로그인(`services/user_action_notice.py` TEXT_OFFICIAL_LOGIN) | 안전신문고 로그인이 필요합니다. 앱에서 다시 로그인해 주세요. | 안전신문고 로그인이 필요합니다. 설정에서 다시 로그인해 주세요. |
| 상단 안내 30일 보류(TEXT_STALE, 바로 옆에 [다시 시도] 버튼) | 30일 넘게 올리지 못한 신고가 {N}건 있습니다. 앱에서 확인해 주세요. | 30일 넘게 올리지 못한 신고가 {N}건 있습니다. 다시 시도를 눌러 주세요. |
| 상단 안내 업데이트(TEXT_UPDATE) | 앱을 업데이트해 주세요. 업데이트하면 신고 공유를 이어 갑니다. | 프로그램을 업데이트해 주세요. 업데이트하면 신고 공유를 이어 갑니다. |
| 상단 안내 최적화 대기(TEXT_OPTIMIZE, PC 에서는 이 사유가 생기지 않음) | 앱을 열어 두면 신고 정리를 마치고 공유를 이어 갑니다. | 프로그램을 켜 두면 신고 정리를 마치고 공유를 이어 갑니다. |
| 잠금 화면 제목·탭 제목(`web/templates/update_required.html`) | 앱을 업데이트해 주세요 | 프로그램을 업데이트해 주세요 |
| 잠금 화면 본문 | … 기기에 있는 신고 자료는 그대로 남아 있습니다. | … PC에 있는 신고 자료는 그대로 남아 있습니다. |
| 잠금 중 크롤·관리자 요청 거절(`community_gate.BLOCK_MESSAGES[UPDATE_REQUIRED]`) | 앱을 업데이트해 주세요. 업데이트하기 전에는 신고 수집을 시작할 수 없습니다. | 프로그램을 업데이트해 주세요. 업데이트하기 전에는 신고 수집을 시작할 수 없습니다. |

- 그대로 둔 것: 점검 배너 2종("앱" 없음), Client API 409 "서버 프로그램 업데이트가 필요합니다. …"(휴대폰이 보는 문구), 공지 팝업 버튼, 잠금 화면 "지금 버전"·"필요한 버전 … 이상"·[업데이트]. 기존(이번 작업 전) 문구 `community_notice.GENERIC_MESSAGE` "앱을 최신 버전으로 업데이트해 주세요." 는 이번 범위 밖이라 두었다(바꿀지 검수자 판단).
- 시험 기대값 갱신: `tests/test_user_action_notice.py` 3줄, `tests/test_backfill_resume.py`(잠금 화면 제목·"PC에 있는" 본문, 안내·크롤 문구에 "앱" 없음 `test_pc_notice_wording_uses_program_not_app`).

### 시험(합친 폴더)
| 명령 | 결과 |
|---|---|
| 전체 `unittest discover`(1차, fixture 서버 3개·브라우저를 같이 돌린 부하 중) | Ran 1555, 실패 1: `test_community_upload_control...test_many_joined_manual_calls_share_one_run_started_after_they_arrived`(스레드 합류 타이밍 시험). 단독 3회 모두 통과 — 부하 때문의 흔들림으로 판단, 이번 변경과 무관(업로더 합류 코드 미변경) |
| 전체 `unittest discover`(2차, 다른 작업 없이) | **Ran 1555, OK(skipped 6)** |
| E 새 시험 4파일 | progress_report 13·setup_report 8·user_action_notice 7·app_announcements 19, 모두 OK |
| `tests/test_backfill_resume.py` | Ran 46, OK |
| `python3 map/scripts/raw-ingest/check_contract.py --copies pc mobile`(backfill-resume-ws 에서) | PASS 15 files, 2 copies |

### 브라우저 증거 갱신(합친 폴더 그대로, fixture 18671~18673, 끝난 뒤 PID 로 종료)
- 잠금: `/`·`/data/all`·`/settings/`·`/crawl/` 모두 "프로그램을 업데이트해 주세요", 본문 "PC에 있는 신고 자료는 그대로 남아 있습니다.", 가로 넘침 없음(1366·390), 화면 안 POST 409, Client API 409 `SERVER_UPGRADE_REQUIRED`. `evidence/lock-desktop.png`·`lock-phone.png`, `report.json`.
- 점검 배너: 그대로(문구 변경 없음). `evidence/pause-*.png`.
- 상단 안내·공지(E 실제 파일): "안전신문고 로그인이 필요합니다. 설정에서 다시 로그인해 주세요." [로그인], 공지 팝업 → '일주일 동안 보지 않기' 200 → 새로 고침해도 안 뜸, 휴대폰 폭 문맥에서도 안 뜸(관리자별 파일 저장). `evidence/merged-*.png`, `merged-report.json`, 스크립트 `evidence/verify_merged.cjs`(서버 준비 `evidence/serve_merged.py <레포> <데이터폴더> <포트>` — 공지 캐시에 계약 예시 1건·중지·공식 로그인 필요 marker 를 넣고 fixture 를 띄운다).
