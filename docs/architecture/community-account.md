# 커뮤니티 계정 연결 (safeauth.worklazy.net) — PC/Docker 서버 쪽

2026-09-25 추가. 커뮤니티 지도에서 쓸 카카오 계정(Supabase Auth)을 **이 서버에** 연결한다. 안전신문고 로그인과 별개다.
중계·중앙 페이지·프로토콜 정본은 `safetyreport-community-map` 저장소의 `docs/safeauth/protocol.md`
(`server/safeauth/protocol.ts`, `relay.ts`)다. 이 문서는 그 프로토콜 1을 이 서버에서 어떻게 구현했는지만 적는다.

## 흐름

```
관리자 화면/모바일 Client ──POST start──▶ 이 서버: verifier·device_secret·delivery_key 생성 → 암호화 저장
                                          └─▶ 중계 requests (code_challenge 만 전송) → 비교코드 + 1회용 연결 링크
사람: 연결 링크를 브라우저로 열기 → 중앙 페이지(safeauth.worklazy.net)에서 비교코드 확인 → 카카오 로그인
이 서버 poll 스레드(5초±20%, 만료까지만) ──poll──▶ 중계 → status=code → /auth/v1/token?grant_type=pkce 한 번만 교환
                                          └─▶ /auth/v1/user 로 계정 확인 → 후보 세션 암호화 저장 → "계정 확인 필요"
관리자/허가된 Client ──POST confirm──▶ 후보 → 현재 세션으로 원자 교체(저장 완료 후) → 중계 complete(Bearer 새 access)
```

- 중앙 페이지는 이 서버에 접속하지 않는다. 사용자별 Redirect URL 등록이 필요 없다(`127.0.0.1:6819`·NAS IP·프록시 뒤 모두 같은 방식).
- 코드 교환 실패는 다시 교환하지 않는다(새 요청). poll 응답이 유실되면 같은 `delivery_key` 로 다시 poll 한다.
- `complete` 가 네트워크 오류면 같은 토큰으로 몇 번 재시도하고(코드 재교환 없음), 끝내 실패해도 로컬 연결은 유지하고 `last_error=complete_failed` 로 알린다(중앙 페이지만 "완료" 표시를 못 함).
- 확정 전에 취소하면 후보 세션을 `logout?scope=local` 로 끝내고 버린다. 기존 연결은 그대로.
- 다른 세션으로 교체되면 이전 세션도 `scope=local` 로 끝낸다(best-effort, 교체 저장 뒤). 브리프는 "다른 사용자일 때"였지만, 같은 사용자라도 버려진 세션이 남지 않게 `session_id` 가 다르면 끝낸다.

## 파일

| 파일 | 역할 |
|---|---|
| `services/community_auth_service.py` | 설정 검증, 상태 DTO, start/poll 스레드/confirm/cancel/disconnect, `get_access_token()`·`is_upload_allowed()` |
| `services/community_auth_client.py` | 중계·GoTrue HTTP 클라이언트(requests, 10초, 리다이렉트 안 따라감), PKCE, 기기 이름·연결 링크 검증 |
| `services/community_auth_store.py` | 암호화 저장소, 파일 락, 원자적 쓰기, 설치 ID |
| `web/routers/community_route.py` | 관리자 웹 `/settings/community/*`, 모바일 `/api/v1/community-auth/*` |
| `core/utils/csrf.py` | 세션 CSRF 토큰·JSON·Origin 확인 (현재 커뮤니티 POST 만 사용) |
| `web/templates/settings.html` | "4. 커뮤니티 계정" 카드(메인 설정 폼 밖) |
| `main.py` | 라우터 등록, lifespan: 시작 때 대기 요청 poll 재개, 종료 때 스레드 정지 |

## 설정 `[COMMUNITY]` (config.ini)

| 키 | 기본값 | 환경변수(우선) | 설명 |
|---|---|---|---|
| `enabled` | `false` | `SAFETYREPORT_COMMUNITY_ENABLED` | 기능 켜기 |
| `supabase_url` | 빈 값 | `SAFETYREPORT_COMMUNITY_SUPABASE_URL` | https origin 만(경로 없음). http 는 `127.0.0.1` 만(로컬 검증 스택) |
| `publishable_key` | 빈 값 | `SAFETYREPORT_COMMUNITY_PUBLISHABLE_KEY` | `sb_publishable_…` 또는 role=anon JWT 만. `sb_secret_`·service_role 거부 |
| `site_url` | `https://safeauth.worklazy.net/` | `SAFETYREPORT_COMMUNITY_SITE_URL` | 중계가 준 연결 링크가 이 주소 + `#r=…&t=…` 형식인지 검사 |
| `device_label` | 빈 값 → "이 PC" / "Docker 서버"(`/.dockerenv`) | — | 중앙 페이지에 보이는 이름. 프로토콜 규칙(1~40자, `<>"'\`\\`·제어문자·URL 스킴 금지) |
| `api_key_managers` | 빈 값 | — | 관리 허용 API 키들의 SHA-256 hex, 쉼표 구분. 원문 키는 저장하지 않음 |
| `upload_enabled` | `false` | — | 화면에서 바꾸지 않는다. 업로더가 생기면 별도 동의 화면에서 다룬다 |

배포 입력은 `supabase_url` 과 `publishable_key` 두 공개값뿐이다. 앱에는 비밀(service_role, 중계 pepper/암호화 키)이 들어가지 않는다.
설정되지 않았거나 잘못된 값이면 상태가 `unconfigured`, 꺼져 있으면 `disabled` 이고 어떤 네트워크 호출도 하지 않는다.

## 저장·락·백업

- `data/auth/community_session.enc`: Fernet 암호문 하나(JSON: `current`, `pending`, `reauth`, `last_error`). 비어 있으면 파일을 지운다.
- `data/auth/.community_key`: 설치별 Fernet 키(`os.open(..., 0o600)` + chmod 0600). **`.config_key` 와 따로** 둔다(설정 암호화 키와 수명·노출면을 분리).
- `data/auth/community_installation_id`: 설치 ID(비밀 아님, 중계는 HMAC 만 저장). `data/auth/.community.lock`: 파일 락.
- 쓰기: 같은 폴더 임시 파일 → fsync → `os.replace` → 폴더 fsync. 락: 프로세스 안 `RLock` + POSIX `fcntl.flock` / Windows `msvcrt.locking`.
- 읽을 수 없음(키 분실·손상·다른 설치에서 복사): 상태 `store_unreadable`, 덮어쓰지 않는다. 관리자가 "초기화"(= disconnect)하면 파일을 `community_session.enc.unreadable-<시각>` 으로 옮긴다(삭제 안 함).
- **data.db 에 아무것도 쓰지 않는다** → DB 백업·다운로드(`/api/v1/settings/db`)·모바일 DB 변환에 포함되지 않고, 두 레포 스키마 계약도 바뀌지 않는다. `config.ini` 와 `data/auth/*` 는 원래 백업 대상이 아니다. 파일 API(`/api/v1/files`)는 `logs`·`results` 만 연다.
- 볼륨 전체를 복제하면 세션도 복제된다(같은 키·파일). 복제한 쪽에서는 "연결 해제" 후 다시 연결한다.
- Windows/설치형도 같은 파일 암호화를 쓴다(OS 보안 저장소 미사용). 같은 사용자 권한의 프로그램으로부터는 보호하지 못한다.

## 권한

- 관리자 웹: 기존 세션 로그인(미들웨어) + 모든 POST 에 `X-CSRF-Token`(세션 토큰, 설정 화면 `data-csrf`), `Content-Type: application/json`,
  `Origin` 이 있으면 이 요청의 host 와 같아야 함(`Sec-Fetch-Site: cross-site` 거부). 신뢰 프록시(`trusted_proxies`) 설정 시 `X-Forwarded-Host` 와 scheme 차이를 허용. 실패는 403 `csrf_failed`.
- 모바일 Client(`X-API-Key`): 기존 API 키는 범위 구분이 없어 **기본은 상태 조회만**. 관리(start/confirm/cancel/disconnect)는 관리자가 설정 화면에서
  "커뮤니티 계정 관리 허용"을 체크한 키만(`api_key_managers`). 권한 없으면 403 `permission_required`. 권한 없는 키의 상태 응답에는 연결 링크가 없다.
- 하위 호환: 기존 API·키 동작은 그대로. `/api/v1/app/config` `capabilities` 에 `community_account` 추가.

## 로컬 API

관리자 웹 `/settings/community/`: `GET status` → `{data, config}`, `POST start {device_label?}`, `POST confirm {request_id}`,
`POST cancel {request_id?}`, `POST disconnect` → `{data, result:{server_logout, reset_unreadable}}`, `POST settings {enabled?, supabase_url?, publishable_key?, device_label?, api_key_managers?[]}`.

모바일 `/api/v1/community-auth/`: `GET status`, `POST start|confirm|cancel|disconnect` (같은 본문). 성공 `{"data": <상태 DTO>}`.
오류 `{"detail": "<한국어>", "code": "<code>"}`: 503 `community_disabled`/`community_unconfigured`/`fixture_blocked`, 409 `no_pending`/`request_mismatch`/`invalid_state`/`store_unreadable`,
410 `expired`, 429 `rate_limited`(`Retry-After`), 502 `relay_unavailable`/`relay_rejected`, 400 `invalid_label`/`invalid_settings`, 403 `permission_required`.
모든 입력은 JSON 본문(비밀·링크를 URL 에 두지 않음 — 접속 로그에 요청 줄이 남는다). 응답 `Cache-Control: no-store`.

상태 DTO(토큰·verifier·비밀값·사용자 UUID·이메일 없음):

```json
{"state": "unconfigured|disabled|disconnected|pending|confirm_required|connected|reauth_required|store_unreadable",
 "can_manage": true,
 "pending": {"request_id": "…", "display_code": "ABCD-2345", "bootstrap_url": "(can_manage 일 때만)", "expires_at": "…",
             "phase": "created|claimed|oauth_started|exchanging|confirm_required"},
 "candidate": {"request_id": "…", "display_name": "…", "has_email": false, "is_different_account": true},
 "account": {"display_name": "…", "connected_at": "…", "session_state": "valid|reauth_required"},
 "last_error": {"code": "…", "message": "…"},
 "upload_enabled": false}
```

실패한 대기 요청은 비밀값을 지우고 `pending` 을 비운 뒤 `last_error` 로만 알린다(브리프 DTO 의 `phase: failed` 는 쓰지 않음).

## 세션 공급 (향후 업로더)

- `community_auth_service.is_upload_allowed()` = 켜짐 + 설정됨 + 연결됨 + `[COMMUNITY] upload_enabled`. **업로드마다 확인한다.**
- `community_auth_service.get_access_token()`: 만료 60초 안이면 락 안에서 한 번만 refresh(`grant_type=refresh_token`), 새 access+refresh 를 함께 원자 저장.
  락을 잡은 뒤 다시 읽으므로 동시 호출자(다른 프로세스 포함)는 이미 갱신된 값을 쓴다.
  400 `refresh_token_not_found|refresh_token_already_used|session_not_found|session_expired|invalid_grant|user_not_found|user_banned` → `reauth_required`(토큰 삭제, 표시 이름만 유지).
  네트워크/5xx/429 → 세션 유지, `auth_unavailable` 예외.
- 연결 해제: poll 중지, 대기 요청 취소, 로컬 세션 삭제(항상), 서버에는 `logout?scope=local`(access 가 만료됐으면 refresh 후, refresh 도 실패하면 생략).
  GoTrue 의 scope 기본값은 global 이므로 늘 `scope=local`. 신고 데이터 등은 건드리지 않는다.

## fixture·수명주기

- fixture 모드(`SAFETYREPORT_FIXTURE_MODE=1`)에서는 `supabase_url` 호스트가 `127.0.0.1` 일 때만 네트워크를 쓴다. 그 밖에는 `fixture_blocked`(503).
- 부팅 때 시작하는 것은 "만료 전 대기 요청의 poll 재개"뿐이다. 교환 도중 재시작됐다면(`exchanging`) 다시 교환하지 않고 `interrupted` 로 끝낸다.
- 종료 때 poll 스레드를 멈춘다(daemon, 이벤트 대기라 즉시 멈춤). FastAPI 이벤트 루프를 막지 않도록 라우트는 `run_in_threadpool` 로 호출한다.

## 검증 범위와 남은 것

- 단위·통합: `tests/test_community_auth.py`(가짜 중계+GoTrue HTTP 서버, 앱 전체 TestClient).
- 로컬 스택: `tests/test_community_auth_live.py`(실제 GoTrue v2.197.0 + 실제 중계 + 가짜 카카오, 선택 실행 — 실행법은 파일 머리말).
- **하지 않은 것**: 실제 카카오·호스팅 Supabase E2E(운영 프로젝트·테스트 계정 필요), 업로더(신고 데이터 업로드) 자체, OS 보안 저장소 연동,
  모바일 앱 쪽 화면(`safetyreport-mobile`, `/api/v1/community-auth/*` 사용).
