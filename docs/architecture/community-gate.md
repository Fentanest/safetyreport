# 커뮤니티 필수 진입 게이트 (PC·Docker)

2026-09-26 도입. 계약: `contracts/community-ingest/gate.md`, `account-api.md`, `interfaces.md`(map 레포 원본의 사본).
관련: 계정 연결 `community-account.md`, 업로드 `community-upload.md`(T4), 초기화 `community-rebuild.md`(T3b).

## 무엇을 막나
`CAN_ENTER = K && C`
- K: 이 서버의 커뮤니티 세션(`data/auth/community_session.enc`)이 유효하고, 중앙 `community-account/status` 가 카카오 연결·세션을 확인함.
- C: 중앙의 **지금** 필수 정책(버전·동의문 해시 — status.policy)과 (버전, 해시)가 둘 다 같은 활성 동의 grant.
  정책·동의문은 이 서버에 넣어 두지 않는다(2026-09-27): 동의 화면은 중앙 `policy` 로 본문을 받아 그 sha256 을 직접 확인한 뒤 보여 주고,
  그 해시로 동의한다. 동의문이 바뀌면 이 서버를 새로 배포하지 않아도 기존 동의가 `outdated` 가 되어 새 본문으로 다시 묻는다.

카카오 로그인은 동의가 아니다. 로컬 파일의 "완료" 값으로는 통과하지 않는다. 중앙 ingest 는 이 판정과 무관하게 저장 트랜잭션에서 다시 확인한다.
`[COMMUNITY] enabled=false`·`upload_enabled` 는 더 이상 게이트를 끄지 못한다(설정 화면에 경고만).

## 판정 (`services/community_gate.py`)
| 순서 | 조건 | 상태 |
|---|---|---|
| 1 | 공개 설정 누락·자리표시자·비밀 키·환경변수 충돌 | `config_invalid` |
| 2 | 세션 없음 / 재로그인 필요 / 읽기 실패 | `kakao_required` / `kakao_reauth_required` / `session_unreadable` |
| 3 | status 없음·무효화됨·10분 초과 | `verification_required`(네트워크로 다시 받음, 실패하면 진입 불가) |
| 4 | status.gate.kakao=false | `kakao_required` |
| 5 | contributor ∉ {active, none} | `suspended` |
| 6 | 동의 없음·철회·정책/해시 불일치 | `consent_required` |
| 7 | 그 밖 | `ok` |
| 8 | 7 을 통과했지만 이 서버 DB 의 주인 카카오 회원번호가 로그인 계정과 다름 / 번호를 확인하지 못함 | `db_owner_mismatch` / `verification_required`(`data_owner_unverified`) |

- 8 (2026-09-27, `services/account_data.py`): 중앙 status 를 받은 뒤 `_check_owner` 가 `current_kakao_id()` 로 확인한다. 주인 표시가 없으면
  (이 기능 전 DB·비운 DB) 지금 계정을 적고 `ok`. 다르면 writer 연결을 만들지 않고 진입을 막는다 — 화면은 "신고 내역 지우고 이 계정으로 시작"
  (`POST /settings/community/db-owner/adopt`) 또는 "로그아웃(신고 내역 유지)". 무효화하면 주인 확인도 다시 한다.

- 캐시: 화면 이동은 10분(`CACHE_TTL`). 새 작업(크롤 시작·큐·별점·업로드·초기화·자정)은 `require_fresh(60)` — 60초 안의 확인이 없으면 동기 재검증, 실패하면 시작하지 않음.
- 무효화: 로그인 확정·로그아웃(카카오 로그아웃·세션 초기화)·자료 주인 전환·설정 저장·동의 저장/철회·삭제 요청·status 401. 네트워크 장애 때는 유효 기간 안의 성공 캐시만 유지.
- 캐시 주인·세대(2026-10-04, 기술일지 D2-01): 받은 status 에는 그 status 를 받은 `user_id` 를 함께 둔다. 지금 세션 계정과 다르면(로그인 확정 직후 complete 재시도 중 등) 무효로 본다.
  무효화마다 세대 번호를 올리고, 조회를 시작한 세대·계정이 그대로일 때만 status·자료 주인 판정·writer 저장·context 활성화를 적용한다 — 늦게 온 이전 계정 응답은 버린다(모바일 로그인 세대 검사와 같은 목적).
- `require_fresh` 는 재검증이 실패해 탐색 캐시를 쓴 경우에도 60초 안의 확인이 없으면 진입 불가(`status_stale`). 모바일 `CommunityGate.requireFresh` 도 같다(2026-10-04, D2-03).
- 온라인 동안 60초 주기 `community-gate-poll` job(T4 `register_community_jobs`)이 원격 철회를 반영(상한 온라인 60초, 오프라인 10분).
- HTTP 요청의 확인(`check_for_request`)은 장애 때 요청마다 막히지 않게 15초에 한 번만 재시도한다.

## writer 연결과 community.db context
게이트가 `ok` 가 되면 업로드 연결을 확보하고 `community.db` 의 `context` 를 활성화한다(업로더·capture 가 읽음).
- `dataset_key = sha256("safetyreport-dataset|v1|" + [LOGIN] username 소문자·앞뒤 공백 제거)` — 증명 아님, writer 충돌 제어용.
- 연결 id·비밀·epoch 는 `data/auth/community_writer.enc`(세션과 같은 Fernet 키, 세션 파일과 분리). **로그아웃해도 남는다** →
  같은 사용자가 다시 로그인하면 `connections-rebind`(같은 epoch), 다른 사용자·다른 공식 계정이면 새 `connections` 등록.
- 다른 기기가 같은 공식 계정의 active writer 면 `writer_conflict` → 진입은 허용하고 업로드만 멈춤(context inactive). 설정의 "이 서버로 업로드 전환" = takeover.
- 공식 계정 미설정이면 `official_account_required`(진입 허용, 업로드 없음).
- writer scope(`dataset_key:writer_epoch`)가 바뀌면 `community_uploader.refresh_server_completed()` 로 manifest 를 먼저 받는다.

## HTTP·WS 적용 (`main.py`)
- 미들웨어 순서: Session → 관리자 `auth_middleware` → `community_gate_middleware` → 라우트. 게이트 미들웨어를 auth 보다 **먼저 선언**해야 안쪽에서 돈다
  (`tests/test_community_gate.py::test_middleware_order_session_auth_gate`).
- 미충족: 관리자 HTML GET → `/onboarding/community?next=<상대경로>`, 그 밖 → 403 `{"code":"COMMUNITY_ONBOARDING_REQUIRED","gate":{state,reasons}}`.
- `/api/v1/**`: API 키가 틀리면 라우트가 401(게이트 상태를 인증 전에 드러내지 않음), 맞으면 게이트 → 403.
- `/media/*`: 예전에는 인증 없이 열려 있었다 → 관리자 세션 또는 API 키(헤더·`api_key` 쿼리) + 게이트.
- WS(`core/utils/ws_auth.py`): `/ws/events`(API 키), `/crawl/ws/logs`·`/rating/ws/rating_logs`(관리자 세션 쿠키 읽기 전용 또는 API 키).
  인증 실패 4001, 게이트 미충족 4403(accept 뒤 close). 게이트를 잃으면 이벤트 WS 는 `ws_manager.close_all(4403)`, 로그 WS 는 5초마다 재확인.
  기기 화면에서 API 키를 지우면 그 키로 열린 이벤트 WS 를 4001 로 닫고(`ws_manager.revoke_api_key`), 로그 WS 는 5초 재확인 때 인증도 다시 보고 닫는다(2026-10-04, A1-02).
  **모바일 `crawl_screen.dart` 의 `/crawl/ws/logs` 연결은 `?api_key=` 를 붙여야 한다**(이전에는 인증 없이 열렸음).
- allowlist(정확한 method+path, 각자 기존 인증·CSRF·manager 권한 유지): `main._GATE_ALLOW`. 라우트 전수 테스트가 나머지 전부 403 인지 확인.
- 크롤 시작(웹 `/crawl/start`·`/crawl/enqueue-selected`, API `/api/v1/crawl/start`·`/crawl/enqueue`): 게이트 60초 재검증 실패 403,
  초기화 필요·진행 중 409 `COMMUNITY_REBUILD_REQUIRED`(API 는 `detail` 이 코드 문자열). 별점 일괄은 `require_fresh` 실패 시 기존 RuntimeError 경로.

## 화면
- `/onboarding/community`: [필수] 1. 카카오 인증(설정 화면과 같은 `components/community_account_card.html`),
  [필수] 2. 신고내용 공유 동의(`components/community_consent_card.html` — 문서 전문, 기본 해제 체크박스, 서버 성공 응답 뒤에만 완료).
  둘 다 끝나면 `next`(같은 서버 상대경로만, `safe_next`)로 이동. `config_invalid` 면 고급 설정이 열린 복구 화면.
- `/onboarding/rebuild`: 1회 초기화 안내·확인. 이전 형식 개인 DB는 서버 시작 때 백업 후 신고 자료를 비우며, 새로 수집해야 한다. 남는 항목과 백업 경로는 [data-contracts.md](data-contracts.md)의 "2026-09-27 이전 버전 DB 처리"를 따른다. 필요·진행 중이면 모든 화면 위에 배너(`base.html`).
- 설정 화면 "5. 신고내용 공유 동의": 상태·동의 문서·철회(→ 필수 설정 화면)·업로드 연결 전환. 공유 자료 삭제 요청 버튼·라우트는 보류(2026-09-27 결정, 주석 처리).
- 되돌릴 수 없는 조작(카카오 로그아웃·다른 계정 자료 지우기·동의 철회)은 공통 확인 창 `web/static/ui/sr-confirm.js`(`window.srConfirm`)로 지워지는 것·남는 것을 보여 준다(2026-10-04, F-09).

## 로컬 API
| 경로 | 인증 | 설명 |
|---|---|---|
| `GET /settings/community/gate` | 관리자 세션 | 게이트 요약(토큰·사용자 UUID·연결 비밀 없음) |
| `GET /settings/community/policy` | 관리자 세션 | 중앙 `policy` 로 받은 정책 버전·해시·동의문 원문(본문 해시 확인). 카카오 연결 전이면 409 `not_connected`. 화면은 계정 카드가 연결됨을 알린 뒤에만 부른다(2026-10-04, F-03) |
| `POST /settings/community/consent` | 세션 + CSRF | `{"accepted":true,"policy_version","consent_text_sha256"}` — 화면이 보여 준 본문의 버전·해시 그대로 → 중앙 `consent`(via `safetyreport_server`). 그 사이 중앙 정책이 바뀌었으면 409 `policy_mismatch` |
| `POST /settings/community/consent-revoke` | 세션 + CSRF | `{"confirm":true}` → 현재 grant 철회(업로드 먼저 중단) |
| `POST /settings/community/writer` | 세션 + CSRF | `{"takeover":true}` → 이 서버로 업로드 연결 전환 |
| ~~`POST /settings/community/contributions-delete`~~ | — | **보류**: 라우트·버튼 주석 처리(2026-09-27 결정). 되살릴 때 `{"confirm":"DELETE_MY_SHARED_REPORTS"}` |
| `POST /settings/community/logout` | 세션 + CSRF | `{"confirm":"DELETE_MY_REPORTS"}` → 카카오 로그아웃. 신고 자료를 지운 뒤 로그아웃(주인이 다른 계정으로 **확인된** 경우만 남김). 크롤링·지도 변환 중이면 409, 아무것도 지우지 않고 로그인 유지. 결과 `reports_wiped` |
| `POST /settings/community/reset-session` | 세션 + CSRF | 세션 파일을 읽을 수 없을 때(`store_unreadable`)만: 옆으로 옮기고 다시 로그인(자료 유지 — 다음 계정이 주인과 다르면 8 이 막음). 키 파일 자체가 망가졌으면 키·세션·writer 파일을 함께 `*.unreadable-<시각>` 으로 보관하고 새 키로 시작한다. 세션이 없어도 키가 망가졌으면 `store_unreadable` 로 보인다(2026-10-04, D2-04) |
| `POST /settings/community/db-owner/adopt` | 세션 + CSRF | `db_owner_mismatch` 일 때만 `{"confirm":"DELETE_OTHER_ACCOUNT_REPORTS"}` → 자료를 비우고 지금 계정을 주인으로 적음 |
| `GET /api/v1/community/gate` | API 키 | 서버 게이트 요약(모바일 Client 표시·fingerprint 비교) |

모바일 Client 의 민감 제어(초기화 시작·수동 업로드)는 `X-Community-User-Token`(폰의 access token)을 `verify_client_user_token` 이 GoTrue `/user` 로 확인해
이 서버에 연결된 사용자와 같을 때만 허용한다.

## 설정·비밀
공개 설정 우선순위: 환경변수(`SAFETYREPORT_COMMUNITY_*` = `COMMUNITY_*` 별칭, 둘 다 있고 다르면 `config_conflict`) > `config.ini [COMMUNITY]` >
빌드가 넣은 `community_public.json`(supabase_url·publishable_key·site_url). 비밀 키(`sb_secret_`·service_role)는 어느 경로로도 거부.
fixture 모드는 `127.0.0.1` 스택에만 연결한다(계정 API·`/user` 확인 포함).

## 업로드 연결 자동 전환 (2026-09-28)
이 PC·서버(또는 모바일 기기)에서 카카오 로그인을 확정하거나(`/settings/community/confirm`, `/api/v1/community-auth/confirm`) 공유 동의를 저장하면
게이트가 `claim_for_this_device()` 를 세우고, 다음 writer 확인 한 번에서 `superseded` 연결·`writer_conflict` 를 takeover 로 등록한다.
재시작·60초 주기 확인만으로는 세우지 않는다. `suspended` 는 가져오지 않는다. 모바일 `CommunityGate._claimRequested`(로그인 단계가 브라우저·교환·계정 확인에서 연결로 바뀔 때, 온보딩 동의 저장 때)와 같은 규칙.
설정 화면의 '이 기기로 업로드 전환' 버튼은 그대로 둔다.

## 코드 대조 정정 (2026-10-04 기술일지)
| 문서에 있던 내용 | 코드 | 정정 |
|---|---|---|
| 공유 자료 삭제 API 를 현행처럼 표기 | `contributions-delete` 라우트·버튼 주석 처리 | 보류로 표기(D2-09) |
| reset-session 은 세션 파일만 옮김 | 키가 망가진 경우 복구되지 않았음 | 키·writer 함께 보관하도록 코드 수정 후 문서 갱신(D2-04) |
