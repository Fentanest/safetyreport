# community-account API v1 (auth 레포 구현, 사용자 전용)

`POST {COMMUNITY_SUPABASE_URL}/functions/v1/community-account/{action}`
헤더: `apikey: <publishable key>`, `Authorization: Bearer <사용자 access token>`, `Content-Type: application/json`.
함수는 `verify_jwt = true` 이고 handler 가 다시 `auth.getUser(jwt)` + claims(`role=authenticated`, `aud=authenticated`, `iss`, `session_id`, `is_anonymous=false`)를 확인한다.
본문은 `{"protocol":1, ...}`, 모르는 필드 거부, 8KB 이하. 응답 `Cache-Control: no-store`. 한도: 사용자별 분당 30 요청.
오류: `{"error":{"code","message","requestTraceId","retryable","retryAfterSeconds?"}}`.

| action | 본문(protocol 외) | 성공 응답(protocol 외) | 주요 오류 |
|---|---|---|---|
| `status` | `connection_id?` | 아래 status | `auth_required`(401) |
| `consent` | `policy_version`, `consent_text_sha256`, `via`(`safetyreport_server`/`mobile_standalone`/`mobile_client`), `accepted: true` | `grant_id`, `policy_version`, `granted_at`, `created` | `kakao_required`(403), `policy_mismatch`(409 + `required_version`), `contributor_suspended`(403) |
| `consent-revoke` | `grant_id`(현재 또는 같은 계보의 이전 grant) | `grant_id`(실제로 철회된 활성 grant), `revoked:true`, `already_revoked`, `lineage_active:false` | `not_found`(404), `stale_grant`(409: 그 계보는 이미 닫혔고 다른 활성 동의가 있음 — status 를 다시 받아 현재 grant 로 요청) |
| `connections` | `source_app`, `source_mode`, `platform`, `device_label`(1~40, relay 규칙), `dataset_key`(64hex), `connection_secret`(base64url 32바이트 — 서버는 sha256 만 저장), `takeover`(bool) | `connection_id`, `writer_epoch`, `superseded_previous` | `kakao_required`, `writer_conflict`(409 + `active_writer:{device_label, platform, source_app, created_at}`), `invalid_request` |
| `connections-rebind` | `connection_id`, `connection_secret` | `connection_id`, `writer_epoch`, `last_accepted_revision` | `not_found`(404, 타인·없음 구분 안 함), `connection_revoked`/`connection_superseded`/`connection_suspended`(409) |
| `connections-revoke` | `connection_id` | `connection_id`, `status:"revoked"` | `not_found` |
| `contributions-delete` | `confirm: "DELETE_MY_SHARED_REPORTS"` | `deletion_id`, `deleted_facts`, `revoked_connections`, `deleted_at` | `kakao_required` |

status 응답:
```json
{"protocol":1,
 "gate":{"kakao":true,"consent":true,"can_enter":true,"reasons":[]},
 "policy":{"required_version":"2026-09-26.1","consent_text_sha256":"…"},
 "consent":{"state":"active|none|revoked|outdated","grant_id":"…|null","policy_version":"…|null","granted_at":"…|null"},
 "contributor":{"status":"active|suspended|deletion_pending|none"},
 "connection":null | {"status":"active|superseded|revoked|suspended","writer_epoch":5,"bound_to_current_session":true,
                      "last_accepted_revision":120,"source_app":"safetyreport","source_mode":"server","dataset_key":"…"},
 "projection":{"ready":false},
 "account":{"fingerprint":"<32hex>","display_name":"…|null"},
 "server_time":"2026-09-26T03:00:00.000Z"}
```
- reasons 값: `user_not_eligible`, `kakao_missing`, `session_missing`, `consent_none`, `consent_revoked`, `consent_outdated`, `contributor_suspended`.
- `fingerprint = sha256("sr-community-account|v1|" + user_id)` 앞 32 hex. user UUID·이메일·토큰은 반환하지 않는다. `display_name` 은 표시용(권한 근거 아님).
- `connection` 은 요청한 connection_id 가 **이 사용자 것**일 때만 채운다(타인 것이면 null — 존재를 드러내지 않음).

정책 불변성(N-03): `private.community_policies` 는 (version PK, consent_text_sha256) 이력이고 한 번 쓴 행은 바꿀 수 없다(트리거로 UPDATE/DELETE 거부). 현재 필수 정책은 `private.community_policy_current` 단일 행이 가리킨다. status·ingest 는 grant 의 **(policy_version, consent_text_sha256) 쌍**이 현재 정책과 둘 다 같을 때만 `active` 로 본다. 동의문을 바꾸려면 새 버전을 발급하고 앱 번들 사본·해시를 함께 올린다.

동의 규칙: 카카오 로그인은 동의가 아니다. 앱은 동의 체크(기본 해제) + 계속 버튼으로만 `consent` 를 호출하고, 성공 응답을 받은 뒤에만 완료로 표시한다.
같은 활성 grant·같은 정책이면 멱등(`created:false`). 정책 버전이 바뀌어 다시 동의하면 새 grant 가 이전 grant 의 **계보(lineage)** 를 이어받아 이미 공유한 자료가 계속 공개된다.
사용자가 철회한 뒤 다시 동의하면 **새 계보**가 시작되어 이전 계보로 수락된 fact 는 공개되지 않는다(`reshare` 로만 다시 공유).

연결 규칙: 연결 비밀(`connection_secret`)은 기기에서 만들고 기기 보호 저장소에만 둔다(PC `data/auth` 암호화 저장소, 모바일 secure storage). 같은 사용자 재로그인 = `connections-rebind`(epoch 유지 → 대기 이벤트 계속 전송).
다른 사용자로 로그인하면 rebind 가 `not_found` → 새 사용자로 `connections` 등록, 이전 사용자 대기 이벤트는 보존·전송 금지.
중앙 manifest(S-04, map 의 ingest 함수): `POST {url}/functions/v1/community-ingest/manifest` 본문 `{"protocol":1,"connection_id":"…","after":null|"<64hex>","limit":5000}`(같은 헤더·인증·연결 검사, `Cache-Control: no-store`, 로그에 남기지 않음) →
`{"protocol":1,"dataset_key":"…","writer_epoch":N,"total":T,"manifest_token":"<10진 세대, 예: \"0\", \"17\">","key_prefixes":["<24hex>",…],"next_after":null|"<64hex>"}` — 호출자 소유·연결의 dataset_key·`public_state='completed'` fact 의 `source_report_key` 앞 24hex, 키 순서, 페이지당 최대 5000.
클라이언트는 manifest 를 받는 동안 자기 업로드 lease(`leases('upload')`)를 잡아 자기 업로드로 세대가 바뀌지 않게 하고, `next_after` 가 null 이 될 때까지 받고, **모든 페이지의 manifest_token(`^[0-9]+$` — 그 dataset 의 완료 key 집합이 바뀔 때마다 같은 트랜잭션에서 증가하는 세대 번호, fact 표 트리거로 유지; 빈 dataset 은 `"0"`)이 같고** 받은 개수 = total 이고 중복이 없을 때만 `server_completed` 를 한 트랜잭션으로 교체한다. 토큰이 바뀌면 처음부터 다시(최대 3회), 그래도 실패하면 교체하지 않고 수집을 시작하지 않는다(`manifest_unavailable`).
철회 규칙: `consent-revoke` 는 주어진 grant 가 속한 **계보의 활성 grant** 를 철회한다(정책 갱신으로 대체된 옛 grant ID 를 보내도 사용자가 보는 동의가 실제로 철회됨). 삭제 규칙: `contributions-delete` 는 공유 fact 삭제 + 신고 identity tombstone + 삭제 fence(그 시각 이전 captured_at 이벤트 거절) + writer 연결 전부 폐기. 앱은 성공 응답 뒤 로컬 outbox 의 대기 행을 모두 `blocked:deleted_by_user` 로 바꾸고 새 연결 등록부터 다시 시작한다.

`dataset_key = sha256("safetyreport-dataset|v1|" + 공식 로그인 ID 소문자·앞뒤 공백 제거)` — 클라이언트 주장값(증명 아님), writer 충돌 제어와 fact 네임스페이스용.
