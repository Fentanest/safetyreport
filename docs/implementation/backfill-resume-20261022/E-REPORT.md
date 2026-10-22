# E 보고서 (PC 주변, 2026-10-22)

작업 폴더 `pc-ui`(feat/backfill-resume-ui, 기준 dda96e7). 커밋·push 없음. 의존성 파일·게이트·업로더·웹 라우터·기본 템플릿·1회 전송 코드는 건드리지 않았다.

## 만든 파일
| 파일 | 내용 |
|---|---|
| `services/community_progress_report.py` | `maybe_send(*, store, trigger, backfill, optimize, official_login)` (4-1). 보조: `build_body`, `validate_body`, `should_send`, `last_maintenance(store)` |
| `services/community_setup_report.py` | `report(*, stage, reason=None)` (4-2) |
| `services/user_action_notice.py` | `evaluate(*, backfill_reason, official_login_manual, stale_holds, update_required)` (8절) |
| `services/app_announcements.py` | `pending_for_admin`, `snooze`, `dismiss` (9절) + `on_login(admin_key)`, `refresh()` |
| `web/templates/partials/notice_banner.html` | 변수 `notices` |
| `web/templates/partials/announcement_popup.html` | 변수 `announcements`, 선택 변수 `announcement_api_base`(기본 `/api/announcements`) |
| `contracts/app-announcements/` | 지도 계약 사본(스키마·예시·`vectors/links.json`·MANIFEST, 해시 일치 확인). 시험이 읽는다 |
| `contracts/community-ingest-v2/progress.schema.json`, `vectors/progress.json` | 지도 계약 사본 추가(이 폴더의 MANIFEST 는 수정하지 않았다 — 합칠 때 지도 쪽 MANIFEST 로 교체 필요) |
| `tests/test_community_progress_report.py`(13) · `test_community_setup_report.py`(8) · `test_user_action_notice.py`(7) · `test_app_announcements.py`(19) | 새 시험 47개 |

## 동작 요약
- **진행 보고**: 본문을 `progress.schema.json` 과 같은 규칙의 손수 검사(`validate_body`, 계약 벡터 12건 전부 통과, PC 는 `pace` 있으면 거절)로 확인하고 4KiB 이하일 때만 보낸다. `backfill` 의 시각이 epoch 숫자여도 ISO `Z` 로 바꾼다. 형식 밖 값은 null(state 는 `none`). gzip 없음.
  trigger: `start` 항상(60초만 적용) / `state_change` `(state,phase,reason,official_login,optimize.*)` 서명이 마지막과 다를 때 / `periodic` 진행 중 상태에서 마지막 시도 뒤 15분 / `daily` `done` 이고 마지막 전송 KST 날짜가 오늘이 아닐 때. 같은 연결 60초 안 두 번째는 어떤 trigger 도 막는다(실패한 시도도 센다).
  **시도 시각·서명을 전송 전에 먼저 기록**한다 → 실패해도 같은 변화를 재시도하지 않는다(규칙 "재시도하지 않는다"). 모든 예외는 삼킨다. 연결(`active_context().connection_id`)이 없으면 보내지 않는다.
  기록: `community.db meta progress_report`(JSON) = `connection_id, last_attempt_at, last_sig, last_date, last_result{http_status,code,at}, maintenance?`.
- **503 maintenance 기록 방법**: `maintenance = {resume_at(epoch 초|null), retry_after(초|null), message(평문|null), seen_at}` 를 위 meta 에 남기고, 성공(200)이 오면 지운다. C 는 `community_progress_report.last_maintenance(store)` 를 읽어 5절 `upload_pause`(source='503')로 옮기면 된다(이 모듈은 `upload_pause` 를 직접 쓰지 않는다).
- **중지·잠금 확인**: `meta upload_pause`(JSON, `paused` 가 참이고 `resume_at` 이 미래이거나 없음) 또는 `meta client_lock`(JSON, `locked` 참)이면 보내지 않는다. **키 이름과 모양은 내가 정한 가정**이다 — C 가 다르게 저장하면 알려 달라(요청 1).
- **설정 단계 보고**: 본문은 4-2 그대로(`source_app='safetyreport'`, `mode='server'`, `client=null`, 성공 단계 reason 은 null 로 강제, 형식 틀린 reason 은 null). 헤더 `X-Client-Version`·`X-Client-App: pc/<platform>`. 같은 `stage|reason` 반복 금지는 **200 을 받은 것만** "보낸 내용"으로 기록(429·실패는 다음 변화 때 다시). 마지막 시도 뒤 60초 안이면 건너뜀. `connection_registered` 200 뒤 `done` 표시 → 이후 호출 무시, 새 `kakao_ok` 가 오면 표시와 기록을 지우고 새 흐름. meta `setup_progress`. 모르는 stage(예: `client_server_checked`)는 보내지 않는다.
- **공지**: 요청은 쿼리 4개(`app=pc&mode=server&platform&version`) + 헤더 `apikey`(+ 앱 이름 User-Agent: Cloudflare 가 기본 UA 를 막은 전례 때문). 서버 전체 하나의 캐시 6시간(실패도 같은 시간 대기, 반복 없음). 응답 64KiB 상한, 리다이렉트 금지. 항목은 스키마 규칙으로 하나씩 검사(키 정확히 8개, id 슬러그, 제목 40·본문 600, 본문 제어문자(LF 제외) 금지, snooze 0–365 정수, 날짜 파싱) 틀린 항목만 버리고 최대 3개. 기간 밖(starts/ends)은 PC 시계로 걸러 숨긴다. `primary_action`: 허용 링크면 `{label,url}`, 밖의 링크·`app_action` 이면 `None`(공지는 남고 버튼만 숨김), 모르는 `app_action` 값이나 다른 모양은 그 공지를 버린다. `snooze_label` 은 7→"일주일 동안 보지 않기", 그 밖 "N일 동안 보지 않기", 0→None.
  보지 않기: `snooze(admin_key,id)` 는 캐시된 공지의 `snooze_days` 로 `지금+N×24h`(캐시에 없는 id 는 7일, 0일 공지는 무시), `data/announcements/snooze.json`(`os.replace` 원자 저장, 관리자 최대 50·관리자별 id 100개). 읽을 때 지난 값·형식 틀린 항목을 걸러 낸다(파일은 쓸 때만 갱신). 파일이 깨졌으면 빈 것으로 보고 다음 저장이 덮는다.
  링크 허용 규칙은 `link_allowed()` 하나이며 `vectors/links.json` 19건 전부를 시험이 돌린다.
- **안내 목록**: 순서 update_required → official_login_required → optimize_blocked_background → stale_holds. 문구는 11절 그대로(시험이 금지어·느낌표·물결표 없음 확인, 화면 용어는 "올리지 못한 신고").

## 판단
1. **하루 1번(8절) vs PC**: PC 는 시스템 알림이 없고 안내는 "조건이 참인 동안 화면 상단에 계속 보이는" 상태 표시라서 **하루 1번 기준이 필요 없다**. 오히려 하루 1번으로 제한하면 사용자가 놓친다. 그래서 `evaluate` 는 상태 함수로만 두고 날짜 저장을 만들지 않았다.
2. **닫기 = "이번 로그인 동안"**: 메모리 `{admin_key: {id}}` 이고 `on_login(admin_key)` 가 비운다. 세션 쿠키 id 를 키에 섞지 않은 이유: PC 는 관리자 한 명(`request.session['admin_username']`)이고, 로그인 때마다 `on_login` 이 호출되면 같은 효과이며 라우터가 단순해진다. 서버 재시작도 비운다. 다른 브라우저에서 같은 관리자로 로그인하면 `on_login` 으로 닫기가 풀려 다시 뜰 수 있다(보지 않기는 파일이라 유지) — 규칙의 "이번 로그인" 해석으로 받아들였다.
3. `admin_key` 는 `request.session['admin_username']` 를 그대로(소문자 변환 등 가공 없음) 쓰길 권한다. 관리자 계정 이름을 바꾸면(`/settings/admin`, 세션의 admin_username 갱신) 보지 않기 기록은 옛 이름 아래 남아 새로 뜬다 — 드문 경우라 수용.
4. 진행 보고의 `periodic`/`daily` 호출은 C 의 스케줄러가 자주 불러도 된다(15분·날짜 판정은 내부). 다만 `state_change` 는 marker 가 바뀔 때마다 부르면 충분하다(서명이 같으면 안 보냄).
5. 공지 시각 필터는 기기 시계를 쓴다. 서버가 이미 기간으로 거르므로 보조 장치일 뿐이다(규칙의 "기기 시계 문제"는 필수 버전에서만 언급).
6. 공지 요청은 `community_cloud.run`(공유 쿨다운)을 쓰지 않았다 — 공지 실패가 업로드 쿨다운을 키우거나 반대로 업로드 쿨다운이 공지를 막는 결합을 피하려는 것. fixture 모드는 `block_if_fixture` 로 막는다.
7. 실패 대기 6시간은 규칙 문구("다음 때를 기다린다")를 따랐다. 관리자 로그인 때는 `on_login` 이 60초 간격 제한만 두고 강제로 새로 받는다.

## 2-16 문의 링크
지도 레포 이슈로 가는 문의 링크가 **PC 웹 템플릿에 없다**. 찾은 것: `web/templates/base.html:155` 깃허브 저장소(PC 레포, 문의 아님)·`:178` 업데이트 다운로드. 지도 바로가기(`report_map.html` safemap.worklazy.net)는 문의 링크가 아니다. 그래서 바꾼 곳 없음. 문의 링크를 새로 넣을지는 검수자 결정(넣는다면 `https://github.com/Fentanest/safetyreport-mobile/issues`, base.html 사이드바 "기타" 항목은 C 영역).

## 요청 (C 가 붙일 곳)
1. **호출 지점**: ① 서버 기동·게이트 통과 뒤 `community_progress_report.maybe_send(store=…, trigger='start', backfill=<marker 요약>, optimize=…, official_login=…)` ② marker 가 바뀔 때 `'state_change'` ③ 스케줄러가 주기적으로 `'periodic'`·`'daily'`(내부에서 판정). `backfill` 은 `{state, phase, reason, next_attempt_at, total, stored, queued, pending, holds, stale_holds, raw_unavailable, not_eligible, run_started_at, updated_at}`(시각은 epoch 또는 ISO Z), `optimize` 는 `{required, running, holds_only}`, `official_login` 은 `'ok'|'retry'|'manual_required'`. run 이 없으면 `{'state':'none'}`.
2. **meta 키 합의**: `upload_pause`(JSON `{paused, resume_at, message, source, saved_at}` — 5절 그대로)와 `client_lock`(JSON `{locked: bool, …}`)을 C 가 쓴다면 이 이름이면 된다. 다르면 `_blocked()` 한 함수만 고치면 된다. 503 은 `last_maintenance(store)` 를 읽어 `upload_pause` 로 옮기고 `paused` 로 1회 전송을 멈출 것.
3. **설정 단계 보고 호출**: `community_setup_report.report(stage=…, reason=…)` 를 ① 카카오 로그인 완료(`kakao_ok`, 세션 저장 뒤) ② 동의 성공(`consent_ok`) ③ 공식 로그인 시작·실패(`official_login_failed`, 앱의 실패 코드)·성공 ④ 연결 등록 성공(`connection_registered`, `register_connection` 성공 직후 `community_gate.py:532` 근처). 토큰이 있어야 하므로 `kakao_ok` 는 세션 저장 이후에 불러야 한다.
4. **웹 라우터** (C): 
   - `GET /api/announcements/pending` → `{"data": pending_for_admin(admin_key)}` (스레드풀에서; 캐시가 오래되면 네트워크 호출). `POST /api/announcements/{id}/snooze`, `POST /api/announcements/{id}/dismiss` (JSON 본문 `{}`, CSRF 는 `session-requests.js` 가 붙인다). 경로를 바꾸면 `announcement_popup.html` 에 `announcement_api_base` 변수를 넘기거나 파일의 기본값만 고친다. `/api/v1/` 아래는 CSRF 자동 부착에서 빠지므로 쓰지 말 것.
   - 로그인 성공 처리(`web/routers/auth_route.py:75` 근처)에서 `app_announcements.on_login(session['admin_username'])`.
   - 모든 템플릿 컨텍스트에 `notices`(= `user_action_notice.evaluate(...)`)와 `announcements` 를 넣고, `base.html` 의 `srClientNotice` 근처에 `{% include 'partials/notice_banner.html' %}`, `</main>` 뒤에 `{% include 'partials/announcement_popup.html' %}` 를 붙인다. 팝업은 bootstrap 번들 이후에 초기화되도록 `DOMContentLoaded` 에서 시작하므로 body 끝 어디든 된다(`window.bootstrap` 없으면 아무것도 안 함).
   - `notice_banner` 의 [다시 시도] 버튼은 `POST /community/upload/raw-backfill/retry`(이미 있는 라우트)를 JSON `{}` 로 부른다. `update_required` 항목은 잠금 화면이 따로 있으면 C 가 목록에서 빼도 된다(`evaluate(update_required=False)`).
   - [로그인] 버튼 링크는 `/settings/`(공식 계정 카드가 있는 곳)로 두었다. 더 정확한 앵커가 있으면 `user_action_notice.LOGIN_URL` 만 바꾸면 된다.
5. **계약 사본 정리**: `contracts/community-ingest-v2/MANIFEST.sha256` 을 지도 쪽 것으로 교체(progress 항목 포함)하고, `contracts/app-announcements/` 를 MANIFEST 대상 폴더로 편입할지 검수자가 결정.

## 시험 결과
- 새 시험 47개: `SAFETYREPORT_DATA_DIR=<임시> /home/better0101/projects/safetyreport/.venv/bin/python -m unittest tests.test_community_progress_report tests.test_community_setup_report tests.test_user_action_notice tests.test_app_announcements` 통과.
- 전체 `unittest discover` 결과는 아래 "전체 시험" 줄에 기록.
- 이 PC 에는 브라우저 검수를 하지 않았다(템플릿은 Jinja 렌더와 이스케이프만 시험). 팝업 모양·모달 연쇄 표시는 C 가 연결한 뒤 실제 브라우저로 확인 필요.

## 2-13 문구 위치 표
| 위치 | 문구 |
|---|---|
| `user_action_notice.py` TEXT_OFFICIAL_LOGIN | 안전신문고 로그인이 필요합니다. 앱에서 다시 로그인해 주세요. (버튼 "로그인") |
| TEXT_OPTIMIZE | 앱을 열어 두면 신고 정리를 마치고 공유를 이어 갑니다. |
| TEXT_STALE | 30일 넘게 올리지 못한 신고가 {N}건 있습니다. 앱에서 확인해 주세요. (버튼 "다시 시도") |
| TEXT_UPDATE | 앱을 업데이트해 주세요. 업데이트하면 신고 공유를 이어 갑니다. (버튼 "업데이트") |
| `app_announcements.snooze_label` / 팝업 | 일주일 동안 보지 않기 / {N}일 동안 보지 않기 / 닫기 |
| notice_banner.html 버튼 상태 | "요청했습니다" / "다시 시도하지 못했습니다" (규칙 문서 밖 문구, 11절 금지어 없음) |

## 전체 시험
`unittest discover -s tests` 1509개: 처음 실행에서 `test_fallback_policy`(내 `_maybe_send` 의 조용한 except 를 로그 남기게 고침 → 통과)와 `test_community_upload_control.test_many_joined_manual_calls_share_one_run_started_after_they_arrived` 가 실패. 뒤 것은 내가 건드리지 않은 업로더 시간 의존 시험으로, 전체 실행(부하) 때만 실패하고 단독 실행은 2회 모두 통과했다(불안정 시험으로 판단, C 영역). 수정 뒤 재실행: 1509개 중 실패 이 1건뿐.
