# 커뮤니티 필수 진입 게이트 (PC·Docker)

2026-09-26 도입. 계약: `contracts/community-ingest/gate.md`, `account-api.md`, `interfaces.md`(map 레포 원본의 사본).
관련: 계정 연결 `community-account.md`, 업로드 `community-upload.md`(T4), 초기화 `community-rebuild.md`(T3b).

## 무엇을 막나
`CAN_ENTER = K && C`
- K: 이 서버의 커뮤니티 세션(`data/auth/community_session.enc`)이 유효하고, 중앙 `community-account/status` 가 카카오 연결·세션을 확인함.
- C: 중앙에 현재 정책(`2026-09-26.1`)·동의문 해시(`share-consent-2026-09-26.1.md` 의 sha256)와 같은 활성 동의 grant.

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

- 캐시: 화면 이동은 10분(`CACHE_TTL`). 새 작업(크롤 시작·큐·별점·업로드·초기화·자정)은 `require_fresh(60)` — 60초 안의 확인이 없으면 동기 재검증, 실패하면 시작하지 않음.
- 무효화: 로그인 확정·로그아웃·설정 저장·동의 저장/철회·삭제 요청·status 401. 네트워크 장애 때는 유효 기간 안의 성공 캐시만 유지.
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
  **모바일 `crawl_screen.dart` 의 `/crawl/ws/logs` 연결은 `?api_key=` 를 붙여야 한다**(이전에는 인증 없이 열렸음).
- allowlist(정확한 method+path, 각자 기존 인증·CSRF·manager 권한 유지): `main._GATE_ALLOW`. 라우트 전수 테스트가 나머지 전부 403 인지 확인.
- 크롤 시작(웹 `/crawl/start`·`/crawl/enqueue-selected`, API `/api/v1/crawl/start`·`/crawl/enqueue`): 게이트 60초 재검증 실패 403,
  초기화 필요·진행 중 409 `COMMUNITY_REBUILD_REQUIRED`(API 는 `detail` 이 코드 문자열). 별점 일괄은 `require_fresh` 실패 시 기존 RuntimeError 경로.

## 화면
- `/onboarding/community`: [필수] 1. 카카오 인증(설정 화면과 같은 `components/community_account_card.html`),
  [필수] 2. 신고내용 공유 동의(`components/community_consent_card.html` — 문서 전문, 기본 해제 체크박스, 서버 성공 응답 뒤에만 완료).
  둘 다 끝나면 `next`(같은 서버 상대경로만, `safe_next`)로 이동. `config_invalid` 면 고급 설정이 열린 복구 화면.
- `/onboarding/rebuild`: 1회 초기화 안내·확인(백업·데이터 보존·재개 설명). 필요·진행 중이면 모든 화면 위에 배너(`base.html`).
- 설정 화면 "5. 신고내용 공유 동의": 상태·동의 문서·철회(→ 필수 설정 화면)·공유 자료 삭제 요청(`contributions-delete`, 확인 입력)·업로드 연결 전환.

## 로컬 API
| 경로 | 인증 | 설명 |
|---|---|---|
| `GET /settings/community/gate` | 관리자 세션 | 게이트 요약(토큰·사용자 UUID·연결 비밀 없음) |
| `GET /settings/community/policy` | 관리자 세션 | 정책 버전·해시·동의문 원문 |
| `POST /settings/community/consent` | 세션 + CSRF | `{"accepted":true,"policy_version","consent_text_sha256"}` → 중앙 `consent`(via `safetyreport_server`) |
| `POST /settings/community/consent-revoke` | 세션 + CSRF | `{"confirm":true}` → 현재 grant 철회(업로드 먼저 중단) |
| `POST /settings/community/writer` | 세션 + CSRF | `{"takeover":true}` → 이 서버로 업로드 연결 전환 |
| `POST /settings/community/contributions-delete` | 세션 + CSRF | `{"confirm":"DELETE_MY_SHARED_REPORTS"}` |
| `GET /api/v1/community/gate` | API 키 | 서버 게이트 요약(모바일 Client 표시·fingerprint 비교) |

모바일 Client 의 민감 제어(초기화 시작·수동 업로드)는 `X-Community-User-Token`(폰의 access token)을 `verify_client_user_token` 이 GoTrue `/user` 로 확인해
이 서버에 연결된 사용자와 같을 때만 허용한다.

## 설정·비밀
공개 설정 우선순위: 환경변수(`SAFETYREPORT_COMMUNITY_*` = `COMMUNITY_*` 별칭, 둘 다 있고 다르면 `config_conflict`) > `config.ini [COMMUNITY]` >
빌드가 넣은 `community_public.json`(supabase_url·publishable_key·site_url). 비밀 키(`sb_secret_`·service_role)는 어느 경로로도 거부.
fixture 모드는 `127.0.0.1` 스택에만 연결한다(계정 API·`/user` 확인 포함).
