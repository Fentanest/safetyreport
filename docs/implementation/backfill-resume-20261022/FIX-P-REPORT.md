# FIX-P 보고 — PC 출시 전 버그 수정 (2026-10-22)

- 작업 폴더: `backfill-resume-ws/pc`(branch `feat/backfill-resume`, 기준 b78c752). **커밋하지 않았다**(작업 트리 변경만). 원격 접속 없음. `VERSION` 은 3.6.1 그대로, 의존성 파일은 건드리지 않았다.
- 근거: scratchpad `BUGS.md`(검수자 결정 D1~D11, "### P PC"), `R2-REPORT.md`, `R5-REPORT.md`, 재현 시험 `r2/r2tests/`.
- 규칙 문서(CLIENT-RULES.md)는 읽기만 했다. 문장 갱신은 S 담당.

## 결과 요약

| 항목 | 결과 |
|---|---|
| 전체 unittest(`SAFETYREPORT_DATA_DIR=<임시>`, `-m unittest discover -s tests`) | 1578건 통과(건너뜀 6). 공지 계약을 다시 복사한 뒤 마지막으로 한 번 더 돌렸고 그것도 통과했다 |
| 새 회귀 시험 | `tests/test_backfill_resume_fixes.py` 23건(버그마다 1건 이상) |
| R2 재현 시험(`r2/r2tests/*.py`)을 수정본에서 실행 | 버그 단언 5건이 모두 반대가 됐다. `test_r2_spin`·`test_r2_lockpage` 도 통과한다. 남은 차이 1건은 아래 B1 판단 참고 |
| 브라우저(headless Playwright, fixture) | 잠금 중 [카카오 다시 로그인]을 누르면 필수 설정 화면으로 간다. 카카오 연결 버튼은 `POST /settings/community/start` 로 라우트까지 닿는다(잠금 409 아님). 크롤 시작·동의는 계속 409 다. 업데이트한 버전에서는 저장된 잠금이 풀린다. desktop·phone 모두 확인 |
| `contracts/app-announcements/` | 지도 작업 폴더(S 의 경계 벡터 반영본)와 같은 바이트(`diff -r` 0). json·md 7개가 `git status` 에 보인다 |

## 버그별

### R2-B1 / D2 — 업로드 확인 retry 보류가 15분마다 끝없이 돌던 문제
- 고친 곳: `services/community_raw_backfill.py`
  - 채우기 성공 시 `HOLD_FIELDS` 전체를 지우던 것을 바꿨다. 이제 지난 재시도 시각(`next_attempt_at`)과 `transient_attempts` 만 지운다.
  - 업로드 확인이 `stored` 일 때만 `hold_count`·`first_held_at`·`stale`·`next_attempt_at`·`transient_attempts` 를 지운다.
  - 업로드 확인 retry 로 생긴 보류도 같은 카운트로 이어서 늘어난다.
- 회귀 시험: `HoldCountKeptTests`
  - 같은 신고가 retry 를 반복하면 hold_count 가 1→2→3→4→5, 간격이 900→3600→21600→86400→86400초다. `first_held_at` 은 하나로 고정된다.
  - 30일 뒤 `stale=true` 가 된다.
  - `stored` 가 되면 보류 기록이 모두 지워진다.
- 기존 시험 갱신: `test_backfill_resume.test_fixed_three_times_hold_then_auto_retry_and_growing_interval` 은 queued 상태에서 hold_count 가 남고 stored 에서 지워진다는 것을 확인하도록 바꿨다.
- 판단:
  - R2 재현 시험은 다섯 번째 보류를 `[…, 4, 4]` 로 기대한다. 규칙 문서 1절은 "다시 보류되면 `hold_count += 1`, 4 이상은 24시간 고정"이라 간격만 고정되고 횟수는 계속 는다. 그래서 `[1,2,3,4,5]` 가 규칙대로다(고정적 오류 보류도 원래 이렇게 늘었다).
  - '다시 시도' 버튼은 지금처럼 hold_count 만 0 으로 하고 `first_held_at` 은 남긴다. 30일 표시는 "처음 보류된 때부터 stored 까지"라는 D2 의 뜻을 따른 것이다.

### R2-B2 / D1 — 한 신고의 계속되는 일시적 오류가 1회 전송 전체를 막던 문제
- 고친 곳: `services/community_raw_backfill.py`(`TRANSIENT_HOLD_AFTER=5`, `NOT_REPORT_FAULT`, 채우기 루프)
  - 일시적 오류마다 그 신고의 `transient_attempts`(항목 detail_json)를 1 올린다.
  - 5번째면 `hold_item(error='transient_<사유>')` 로 보류하고 커서를 넘긴다. hold_count 규칙은 그대로다.
  - 다른 신고가 성공하면 지금처럼 run 의 `transient_failures=0` 이다. 신고의 횟수는 성공·보류 때 지운다(`hold_item` 이 지운다).
  - 세지 않는 사유: `sync_busy`·`db_locked`·`maintenance`·`official_login_required`·`official_login_retry`. `sync_busy`·로그인은 `BackfillPaused` 로 바로 run 을 쉬므로 원래 이 경로에 오지 않는다.
- 회귀 시험: `PoisonTransientTests`
  - A 만 계속 `no_result` → A 는 보류(`transient_no_result`, hold_count 1), B~E 는 queued, `transient_failures=0`.
  - 정확히 5번째에 보류된다.
  - db_locked·sync_busy·로그인 재시도는 20번이어도 세지 않는다.
- 기존 시험 갱신: `test_transient_backoff_sequence_and_reset_on_progress` 는 4번(60·300·900·3600초)까지만 run 휴지를 보고, 성공하면 신고 횟수가 지워지는지 확인한다.
- 판단: 필드 이름은 `transient_attempts`, 보류 사유는 `transient_<사유코드>` 다. 모바일(M)과 규칙 문서(S)가 같은 이름을 쓰면 좋다(보고서로 전달).

### R2-B3 / D3 — 업데이트 뒤에도 잠금이 남고, 잠금 중 카카오 재로그인이 불가능하던 문제
- 고친 곳:
  - `services/community_update_lock.py` `read()`: 잠금 판정을 읽을 때마다 `compare(app_version(), min_version)` 을 다시 한다.
    - 0·1 이면 잠금이 아니고 저장값을 `{"locked": false, "cleared_by": "version"}` 으로 바꾼다.
    - 해석할 수 없으면(`None`) 저장된 판정을 그대로 둔다.
    - 잠금이 아닐 때는 비교하지 않으므로 평소 비용은 없다.
  - `main.py` `_UPDATE_LOCK_RECOVERY`: 잠금 중에도 다음 경로만 연다.
    - GET `/settings`·`/settings/`·`/onboarding/community`·`/settings/community/policy`
    - POST `/settings/community/{start,confirm,cancel,logout,reset-session}`
    - 관리자 인증·CSRF·커뮤니티 게이트는 그대로다. 세션이 없으면 게이트가 `/onboarding/community` 로 보낸다.
    - 설정 저장·동의·크롤·업로드 등 그 밖은 계속 잠금(409·잠금 화면)이다.
  - `web/templates/partials/update_lock_banner.html`(새 파일): 잠금 안내 띠. 설정·필수 설정 화면 위에 보인다.
  - `settings.html`: 잠금 중 메인 설정 폼을 `inert` 로 두고 흐리게 한다(저장이 막혀 있으므로).
  - `update_required.html`:
    - [카카오 다시 로그인](`/settings/#communityCard`) 버튼과 한 줄 안내를 더했다.
    - 기존 [로그아웃]은 [관리자 로그아웃]으로 이름을 분명히 했다.
- 회귀 시험:
  - `LockRecheckTests`:
    - 3.6.3 으로 바뀌면 status 없이 풀리고 저장값도 지워진다.
    - 해석 불가·낮은 버전은 잠금을 유지한다.
  - `LockRecoveryRouteTests`:
    - 복구 경로는 라우트까지 닿고, CSRF 없는 POST 는 403 이다.
    - 설정 저장·크롤·동의·커뮤니티 설정 저장은 409 다.
    - 잠금 화면에 카카오 버튼이 있고 서버 문구는 이스케이프된다.
    - 띠 부분 템플릿을 확인한다.
- 기존 시험 갱신: `test_admin_web_is_one_lock_screen` 은 이제 `/settings/` 가 열리고 `/settings/save` 는 409 인 것을 확인한다.
- 실제 앱 렌더: 게이트를 통과시킨 상태로 실제 `main.app` 의 `/settings/` 를 그렸다(fixture 데이터, `scratchpad/fixp_settings_check.py`). 띠·inert 폼·카카오 카드가 있고 잠금 화면은 없다. 잠금이 아니면 띠도 inert 도 없다.
- 판단:
  - "로그아웃"은 카카오 로그아웃(`/settings/community/logout`, 신고 자료 삭제 확인을 거침)과 관리자 로그아웃(`/logout`, 원래 열림) 둘 다로 해석했다.
  - 동의(`consent`)는 열지 않았다. 잠금 해제에는 카카오 세션만 있으면 된다(confirm 뒤 `regate('login')` 이 status 를 받는다).

### R2-B4 / D5 — 60초 규칙에 막힌 상태 변화가 사라지던 문제
- 고친 곳:
  - `services/community_progress_trigger.py`: `decide_trigger` 는 상태를 바꾸지 않는다. 새 `mark_sent(key)` 를 실제로 보낸 tick 에서만 부른다. `start` 도 보낸 뒤에야 끝난 것으로 본다.
  - `services/community_progress_report.py`: `maybe_send` 가 보냈으면(시도를 기록했으면, 응답 실패 포함) True, 조건이 안 맞아 안 보냈으면 False 를 돌려준다.
- 회귀 시험: `ProgressStateChangeTests`
  - 59.5초에 filling→uploading 으로 바뀌면 다음 tick 에 `uploading` 이 나간다.
  - `done` 도 다음 tick 에 나가고, 그 뒤에는 `daily` 다.
  - `start` 가 막히면 다음에도 `start` 다.
- 기존 시험 갱신: `ProgressTriggerTests` 의 `maybe_send` 가짜가 True 를 돌려주게 했다.

### R2-B5 / D6 — 옛 서버(404)에 설정 단계 보고를 1분마다 다시 보내던 문제
- 고친 곳: `services/community_setup_report.py`
  - 4xx(429 제외: 400·404 등)는 "보냄"으로 기록한다(`sig` + `sig_quiet_until = now+24h`). 24시간 동안 같은 내용은 다시 보내지 않는다.
  - 200 은 지금처럼 영구로 기록한다.
  - 429·5xx·네트워크는 기록하지 않아 다음 호출 때 다시 보낸다.
  - `connection_registered` 의 `done` 표시는 200 에서만 둔다.
- 회귀 시험: `SetupReportOldServerTests`
  - 404 를 30분 동안 받아도 1번이고, 25시간이면 2번이다. 400 도 1번이다.
  - 429·500·503·네트워크는 매번 다시 보낸다.
  - 200 은 다시 보내지 않는다.
- 판단: D6 문장의 "400·404"를 "429 를 뺀 4xx"로 넓혔다. 401·403·409·413 도 같은 내용을 다시 보내 봐야 결과가 같고, 규칙의 "429·5xx·네트워크만 재시도"와도 맞는다.

### R2-B6 / D4 — 재개 시각이 지났는데 중지가 남으면 업로더가 1초마다 헛돌던 문제
- 고친 곳: `services/community_upload_pause.py`
  - `next_attempt(value)`: 재개 시각(+지터)이 지났으면 `now+60`, 아니면 그 시각이다.
  - `check_overdue(store)`: 재개 시각이 지났는데 아직 중지면 status(`check_resume` → 게이트 `refresh_now`)를 백그라운드로 한 번 부른다. 60초에 한 번까지다.
  - `clear()`: `paused=false` 를 받으면 저장이 이미 없어도 maintenance 로 미뤄 둔 outbox 행의 `next_retry_at` 을 지금으로 되돌린다. 되돌린 행이 있으면 업로더·1회 전송을 깨운다.
- 고친 곳: `services/community_uploader.py`
  - 중지 확인 분기에서 `check_overdue` 를 부르고, `next_attempt_at` 은 `next_attempt()` 로 정한다.
  - 503 maintenance 를 받은 행도 같은 시각을 쓴다. 지난 시각이면 60초 뒤다.
- 회귀 시험: `OverdueResumeTests`
  - R2 의 spin 조건에서 `_next_due` 가 지금+55초 이상이고, 60초 안 두 번째 실행에서는 status 를 다시 부르지 않는다.
  - `check_overdue` 는 60초에 한 번만 부른다. 재개 시각이 아직 미래면 status 를 부르지 않는다.
  - 저장이 없을 때 `paused=false` 를 받으면 미뤄 둔 행이 즉시로 바뀌고 한 번 깨운다. 두 번째는 `unchanged` 다.
- 판단: status 를 업로더 스레드 안에서 동기로 부르지 않고 백그라운드 스레드로 부른다. 게이트 갱신이 업로더를 다시 부를 수 있어 재진입·잠금 대기를 피하려는 것이다. 풀리면 `clear()` → `wake()` 가 바로 업로드를 깨운다.

### R2-S5 — 공지 id·보고 코드 검사가 끝 줄바꿈을 받던 문제
- 고친 곳:
  - `services/app_announcements.py`: `_ID` 를 `fullmatch` 로만 쓰고 `^…$` 를 없앴다(4곳).
  - 같은 모양의 검사도 `fullmatch` 로 바꿨다: `community_progress_report.py`(`_UUID`·`_CODE`·`_INSTANT`·`_VERSION`·`_PARSER`), `community_setup_report.py`(`_REASON`).
- 회귀 시험: `TrailingNewlineTests`
  - `"…standalone\n"` id 항목은 버린다.
  - 설정 보고 reason 의 `"captcha_required\n"` 은 null 이 된다.
  - 진행 보고 `_code('network\n')` 는 None 이다.

### R5-1 — `contracts/app-announcements/` json·md 가 `.gitignore` 에 걸려 커밋에서 빠지던 문제
- 고친 곳: `.gitignore` 에 `!/contracts/app-announcements/**` 를 더했다.
- 마지막에 지도 작업 폴더 `map/contracts/app-announcements/` 를 다시 복사했다(S 의 경계 벡터 반영본, D10 스키마·README 포함). `diff -r` 결과 같은 바이트이고, 지도 쪽 `sha256sum -c MANIFEST.sha256` 도 모두 OK 다.
- `git status`: README.md, announcement.schema.json, response.schema.json, examples/*.json 3개, vectors/links.json 이 `??`(추적 가능)로, MANIFEST.sha256 이 `M` 으로 보인다.
- 회귀 시험: `AnnouncementContractCopyTests`
  - MANIFEST 의 목록·해시가 실제 파일과 같다.
  - `git check-ignore` 로 이 폴더의 모든 파일이 무시되지 않는지 확인한다. git 작업 트리가 아니면 건너뛴다.
- 함께 고침(R5-5 의 PC 쪽): S 가 넣은 경계 벡터를 PC 가 돌리면 예전 PC `link_allowed`(URL 을 해석해 비교)는 쿼리·조각·특수문자·한글·하이픈 호스트에서 서버와 결과가 달랐다.
  - 그래서 `link_allowed` 를 SQL `private.app_announcement_url_allowed`·Edge `announcementUrlAllowed` 와 같은 정규식 세 개(`fullmatch`)로 바꿨다.
  - `tests/test_app_announcements.py` 의 벡터 시험이 새 `links.json` 전체로 통과한다.

### R5-의심3 — PC 에서 생기지 않는 `optimize_blocked_background` 안내 코드
- 고친 곳: `services/user_action_notice.py` 에서 분기와 `TEXT_OPTIMIZE` 를 지우고, 왜 없는지 모듈 설명에 적었다.
- 회귀 시험: `test_user_action_notice.test_optimize_blocked_background_is_not_a_pc_notice` 는 그 사유로 안내가 0건이고 코드에 문자열이 없는지 확인한다. `test_backfill_resume` 의 문구 시험에서 `TEXT_OPTIMIZE` 참조를 뺐다.

### VERSION
- 3.6.1 그대로다. 출시 체크리스트 항목이다(R2 "출시 확인 사항": 3.6.2 로 올리지 않고 `min_version.pc=3.6.2` 를 켜면 새 PC 가 스스로 잠긴다).
- D3 재비교 뒤에도 마찬가지다. VERSION 이 3.6.1 이면 3.6.2 요구에 대해 계속 잠긴다(올바른 동작).

## 브라우저 증거 (D3)
- 스크립트: `evidence/serve_fixp.py`, `evidence/verify_fixp.cjs`(기존 `verify_merged.cjs` 와 같은 방식).
- 서버 두 개를 띄웠다:
  - 18681: `client_lock` min 3.6.3, 지금 버전 3.6.1 이라 잠금이 유지된다.
  - 18682: `client_lock` min 3.6.1 이라 읽을 때 풀린다.
  - 확인 뒤 PID 로 종료했다.
- 실행: `PLAYWRIGHT_MODULE=/home/better0101/projects/safetyreport/tools/web-tests/node_modules/@playwright/test node verify_fixp.cjs <out> http://127.0.0.1:18681 http://127.0.0.1:18682`
- 결과(`evidence/fixp-report.json`, desktop·phone 같음):
  - 잠금 화면: 제목 "프로그램을 업데이트해 주세요", [카카오 다시 로그인] href `/settings/#communityCard`. 서버 문구 `<b>` 는 `&lt;b&gt;` 로 이스케이프된다.
  - 버튼을 누르면 `/onboarding/community?next=/settings/` 로 간다(카카오 세션이 없어 게이트가 보냄). 잠금 화면은 0개, 잠금 띠는 1개, 카드 상태 배지는 "연결 안 됨"이다.
  - [카카오 계정으로 연결]을 누르면 `POST /settings/community/start` 가 503 `fixture_blocked` 로 끝난다(fixture 모드는 외부 인증 서버에 연결하지 않음). 잠금 409 가 아니므로 라우트까지 닿았다.
  - 잠금 중에도 막히는 것: `POST /crawl/start` 409, `POST /settings/community/consent` 409(`COMMUNITY_UPDATE_REQUIRED`).
  - 가로 넘침은 없다. 브라우저 밖 외부 요청은 0건이다.
  - 콘솔 오류 3건은 위에서 의도한 503(fixture 차단)과 409 검사 두 건의 리소스 로드 메시지뿐이다.
  - 업데이트한 버전(18682): 잠금 화면 0개, `/settings/community/gate` 의 `update_lock = {locked:false}`.
- 스크린샷: `evidence/fixp-lock-{desktop,phone}.png`, `evidence/fixp-recovery-{desktop,phone}.png`.
- 한계: fixture 에는 카카오 세션이 없어 잠금 띠가 붙은 `/settings/` 화면 자체는 브라우저로 찍지 못했다. 그 렌더는 실제 `main.app` 으로 확인했다(위 B3 "실제 앱 렌더").

## 검사 기록
- 전체 unittest:
  - 첫 실행: 1578건 OK(skipped=6), 417초.
  - 최종 실행(공지 링크 정규식·계약 사본·CHANGELOG 갱신 뒤): 1578건 OK(skipped=6), 434초.
- 관련 묶음(`test_backfill_resume`·`test_backfill_resume_fixes`·`test_app_announcements`·`test_community_progress_report`·`test_community_setup_report`·`test_user_action_notice`·`test_raw_backfill`·`test_community_uploader`)은 각각 통과했다.

## 다른 담당에게 넘길 것
- M(모바일)·S(규칙 문서):
  - D1 의 신고별 필드 이름은 PC 에서 `transient_attempts`, 보류 사유는 `transient_<사유>` 다.
  - D2 에서 업로드 확인 retry 보류도 `hold_count` 를 이어서 올리며, 4 이상은 간격만 24시간으로 고정되고 횟수는 계속 는다.
- S: CLIENT-RULES 6-2 에 "잠금 중 열어 두는 PC 경로" 목록을 적으면 좋다. 위 B3 의 `_UPDATE_LOCK_RECOVERY` 와 같은 목록이다.
