# community-account API v1 (auth 레포 구현, 사용자 전용)

`POST {COMMUNITY_SUPABASE_URL}/functions/v1/community-account/{action}`
헤더: `apikey: <publishable key>`, `Authorization: Bearer <사용자 access token>`, `Content-Type: application/json`.
함수는 `verify_jwt = true` 이고 handler 가 다시 `auth.getUser(jwt)` + claims(`role=authenticated`, `aud=authenticated`, `iss`, `session_id`, `is_anonymous=false`)를 확인한다.
본문은 `{"protocol":1, ...}`, 모르는 필드 거부, 8KB 이하. 응답 `Cache-Control: no-store`. 한도: 사용자별 분당 30 요청.
오류: `{"error":{"code","message","requestTraceId","retryable","retryAfterSeconds?"}}`.

| action | 본문(protocol 외) | 성공 응답(protocol 외) | 주요 오류 |
|---|---|---|---|
| `status` | `connection_id?` | 아래 status | `auth_required`(401) |
| `policy` | (없음) | `policy:{version, consent_text_sha256, consent_text}` — 지금 필수 동의 정책의 **본문**(UTF-8 markdown). 중앙이 보내기 전에 본문 해시 = `consent_text_sha256` 을 확인한다 | `auth_required`(401), `server_error` |
| `consent` | `policy_version`, `consent_text_sha256`, `via`(`safetyreport_server`/`mobile_standalone`/`mobile_client`), `accepted: true` | `grant_id`, `policy_version`, `granted_at`, `created` | `kakao_required`(403), `policy_mismatch`(409 + `required_version`), `contributor_suspended`(403) |
| `consent-revoke` | `grant_id`(현재 또는 같은 계보의 이전 grant) | `grant_id`(실제로 철회된 활성 grant), `revoked:true`, `already_revoked`, `lineage_active:false` | `not_found`(404), `stale_grant`(409: 그 계보는 이미 닫혔고 다른 활성 동의가 있음 — status 를 다시 받아 현재 grant 로 요청) |
| `connections` | `source_app`, `source_mode`, `platform`, `device_label`(1~40, relay 규칙), `dataset_key`(64hex), `connection_secret`(base64url 32바이트 — 서버는 sha256 만 저장), `takeover`(bool) | `connection_id`, `writer_epoch`, `superseded_previous` | `official_account_mismatch`(409 + 본인 `bound_dataset_key`), `official_account_taken`(409, 운영자 문의 안내), `kakao_required`, `writer_conflict`(409 + `active_writer:{device_label, platform, source_app, created_at}`), `invalid_request` |
| `connections-rebind` | `connection_id`, `connection_secret` | `connection_id`, `writer_epoch`, `last_accepted_revision` | `not_found`(404, 타인·없음 구분 안 함), `connection_revoked`/`connection_superseded`/`connection_suspended`(409) |
| `connections-revoke` | `connection_id` | `connection_id`, `status:"revoked"` | `not_found` |
| `contributions-delete` | `confirm: "DELETE_MY_SHARED_REPORTS"` | `deletion_id`, `deleted_facts`, `revoked_connections`, `deleted_at`, `official_account_released:true` | `kakao_required` |

status 응답:
```json
{"protocol":1,
 "gate":{"kakao":true,"consent":true,"can_enter":true,"reasons":[]},
 "policy":{"required_version":"2026-09-26.1","consent_text_sha256":"…"},
 "consent":{"state":"active|none|revoked|outdated","grant_id":"…|null","policy_version":"…|null","granted_at":"…|null"},
 "contributor":{"status":"active|suspended|deletion_pending|none"},
 "connection":null | {"status":"active|superseded|revoked|suspended","writer_epoch":5,"bound_to_current_session":true,
                      "last_accepted_revision":120,"source_app":"safetyreport","source_mode":"server","dataset_key":"…"},
 "official_account":{"dataset_key":"<64hex>|null","bound_at":"<ISO>|null"},
 "projection":{"ready":false},
 "account":{"fingerprint":"<32hex>","display_name":"…|null"},
 "server_time":"2026-09-26T03:00:00.000Z"}
```
- reasons 값: `user_not_eligible`, `kakao_missing`, `session_missing`, `consent_none`, `consent_revoked`, `consent_outdated`, `contributor_suspended`.
- `fingerprint = sha256("sr-community-account|v1|" + user_id)` 앞 32 hex. user UUID·이메일·토큰은 반환하지 않는다. `display_name` 은 표시용(권한 근거 아님).
- `connection` 은 요청한 connection_id 가 **이 사용자 것**일 때만 채운다(타인 것이면 null — 존재를 드러내지 않음).

정책 불변성(N-03): `private.community_policies` 는 (version PK, consent_text_sha256) 이력이고 한 번 쓴 행은 바꿀 수 없다(트리거로 UPDATE/DELETE 거부). 현재 필수 정책은 `private.community_policy_current` 단일 행이 가리킨다. status·ingest 는 grant 의 **(policy_version, consent_text_sha256) 쌍**이 현재 정책과 둘 다 같을 때만 `active` 로 본다.

동의문은 중앙이 내려준다(2026-09-27): 본문은 `private.community_policy_texts`(해시 PK, 본문 해시 검사, 바꿀 수 없음)에 두고 모든 정책 행의 해시는 이 표를 가리킨다 — 어떤 grant 의 해시로든 그때 보인 본문을 다시 읽을 수 있다.
앱(PC·모바일)은 동의문 본문·해시·필수 버전을 **번들에 넣지 않는다**: `policy` 로 받은 본문의 sha256(UTF-8 바이트)이 `consent_text_sha256` 과 같은지 스스로 확인한 뒤 그 본문을 그대로 보여 주고,
`consent` 에는 그 `version` 과 **자기가 계산한 해시**를 보낸다(서버가 알려 준 해시를 그대로 되돌려 보내지 않는다 — 보여 준 본문과 동의 기록이 어긋나지 않게).
그래서 동의문이 바뀌어도 앱을 새로 배포할 필요가 없고, 바뀐 뒤 기존 동의는 `outdated` 가 되어 새 본문으로 다시 묻는다. 서비스 공개 뒤 문구를 바꿀 때는 새 버전을 발급한다(시험 중인 2026-09-28.1 은 같은 버전으로 본문을 교체했다 — 이전 본문도 표에 남음).

동의 규칙: 카카오 로그인은 동의가 아니다. 앱은 동의 체크(기본 해제) + 계속 버튼으로만 `consent` 를 호출하고, 성공 응답을 받은 뒤에만 완료로 표시한다.
같은 활성 grant·같은 정책이면 멱등(`created:false`). 정책 버전이 바뀌어 다시 동의하면 새 grant 가 이전 grant 의 **계보(lineage)** 를 이어받아 이미 공유한 자료가 계속 공개된다.
사용자가 철회한 뒤 다시 동의하면 **새 계보**가 시작되어 이전 계보로 수락된 fact 는 공개되지 않는다(`reshare` 로만 다시 공유).

연결 규칙: 연결 비밀(`connection_secret`)은 기기에서 만들고 기기 보호 저장소에만 둔다(PC `data/auth` 암호화 저장소, 모바일 secure storage). 같은 사용자 재로그인 = `connections-rebind`(epoch 유지 → 대기 이벤트 계속 전송).
다른 사용자로 로그인하면 rebind 가 `not_found` → 새 사용자로 `connections` 등록, 이전 사용자 대기 이벤트는 보존·전송 금지.
중앙 manifest(S-04, map 의 ingest 함수): `POST {url}/functions/v1/community-ingest/manifest` 본문 `{"protocol":1,"connection_id":"…","after":null|"<64hex>","limit":5000}`(같은 헤더·인증·연결 검사, `Cache-Control: no-store`, 로그에 남기지 않음) →
`{"protocol":1,"dataset_key":"…","writer_epoch":N,"total":T,"manifest_token":"<10진 세대, 예: \"0\", \"17\">","key_prefixes":["<24hex>",…],"next_after":null|"<64hex>"}` — 호출자 소유·연결의 dataset_key·`public_state='completed'` fact 의 `source_report_key` 앞 24hex, 키 순서, 페이지당 최대 5000.
클라이언트는 manifest 를 받는 동안 자기 업로드 lease(`leases('upload')`)를 잡아 자기 업로드로 세대가 바뀌지 않게 하고, `next_after` 가 null 이 될 때까지 받고, **모든 페이지의 manifest_token(`^[0-9]+$` — 그 dataset 의 완료 key 집합이 바뀔 때마다 같은 트랜잭션에서 증가하는 세대 번호, fact 표 트리거로 유지; 빈 dataset 은 `"0"`)이 같고** 받은 개수 = total 이고 중복이 없을 때만 `server_completed` 를 한 트랜잭션으로 교체한다. 토큰이 바뀌면 처음부터 다시(최대 3회), 그래도 실패하면 교체하지 않고 수집을 시작하지 않는다(`manifest_unavailable`).
철회 규칙: `consent-revoke` 는 주어진 grant 가 속한 **계보의 활성 grant** 를 철회한다(정책 갱신으로 대체된 옛 grant ID 를 보내도 사용자가 보는 동의가 실제로 철회됨). 삭제 규칙: `contributions-delete` 는 공유 fact 삭제 + 신고 identity tombstone + 삭제 fence(그 시각 이전 captured_at 이벤트 거절) + writer 연결 전부 폐기 + 공식 계정 바인딩 해제. 앱은 성공 응답 뒤 로컬 outbox 의 대기 행을 모두 `blocked:deleted_by_user` 로 바꾸고 새 연결 등록부터 다시 시작한다.

`dataset_key = sha256("safetyreport-dataset|v1|" + 공식 로그인 ID 소문자·앞뒤 공백 제거)` — 클라이언트 주장값(증명 아님), writer 충돌 제어와 fact 네임스페이스용.


## 공식 계정 양방향 1:1 바인딩 (2026-10-06)

카카오 사용자 하나에 안전신문고 dataset 하나, dataset 하나에 카카오 사용자 하나만 허용한다.
`status.official_account`는 호출한 사용자 본인의 바인딩만 반환한다. 미바인딩은 실제 JSON null 두 개다.
원문 ID·다른 사용자 식별자·상대 바인딩 정보는 반환하지 않는다.

| 오류 코드 | HTTP / retryable | message (정확한 서버 문구) |
|---|---|---|
| `official_account_mismatch` | 409 / false | `This community account is bound to another official account. Delete shared reports before changing accounts.` |
| `official_account_taken` | 409 / false | `This official account is already bound. Please contact an operator (운영자에게 문의해 주세요).` |

앱 화면 문구는 아래 각 경우의 한국어 안내를 쓴다(서버 문구를 그대로 보이지 않는다).

`connections`는 등록과 같은 트랜잭션에서 다음 조건을 검사한다.
- 사용자와 dataset 모두 미바인딩: 바인딩 생성 후 연결 등록.
- 같은 사용자와 dataset: 기존 writer 규칙 적용(takeover는 같은 사용자/같은 dataset에만 적용).
- 사용자가 다른 dataset에 바인딩됨: HTTP 409 `official_account_mismatch`, `error.bound_dataset_key`는 본인의 키만 반환 가능.
  사용자 문구: “연결된 안전신문고 계정과 설정이 다릅니다. 바인딩된 계정으로 되돌리거나, 기존 데이터를 백업·초기화하고 새 계정으로 시작하세요.”
- dataset이 다른 사용자에게 바인딩됨: HTTP 409 `official_account_taken`. 상대 정보 없이
  “이 안전신문고 계정은 이미 다른 카카오 계정에 연결되어 있습니다. 운영자에게 문의해 주세요.”

변경은 경고·개인 DB 백업 검증 → `contributions-delete` → 로컬 신고 자료 초기화 → 새 설정 저장 → 새 `connections` 순서다.
삭제 응답의 `official_account_released:true` 확인 전에는 개인 DB를 비우지 않는다. 삭제 응답 유실 후 재요청을 허용하며
삭제 fence·연결 폐기·바인딩 해제는 함께 적용한다. `consent-revoke`, 로그아웃, writer revoke만으로 바인딩을 해제하지 않는다.

클라이언트는 시작·포그라운드 복귀·약 5분마다 config의 정규화 dataset과 대조한다. 불일치는 설정에 가두고
크롤링·업로드를 중지한다. PC의 기존 60초 게이트 poll은 유지하며 탐색 때도 300초를 넘긴 바인딩은 재확인한다.
카카오 미연동 사용자는 본인 status 토큰이 없으므로 기존 필수 연결 화면을 사용한다.

클라우드 일시 장애는 계정 불일치와 구분한다(2026-10-07 로컬 사용 정책, 2026-10-11 재시도 개선).
저장된 로그인 계정과 개인 DB 주인이 일치하고 바인딩 불일치·계정 변경 미완료·이용정지 제한이 없으면
로컬 조회·수집을 유지한다. 공유 업로드는 서버의 현재 인증·동의·연결 검증을 통과해야 한다.
장애 대기는 상단 배너에서 안내하며 수동 재시도로 대기시간을 우회하지 않는다. 연속 실패 n의 공통 대기는
기본 300·2^(n-1)초(기본값 상한 1800초)에 ±20% 지터를 적용하고, 서버 Retry-After가 더 길면 그 값을 따른다.
재시작·화면 복귀로 남은 대기를 줄이지 않으며, 정상 응답을 받으면 연속 실패 횟수를 초기화한다.

### 코드 대조 정정
| 이전 설명 | 현행 동작 |
|---|---|
| status 연결 실패면 성공 캐시가 있어도 전체 화면 차단 | 계정·DB 주인이 확인된 로컬 이용은 유지하고 공유 업로드를 별도로 검증 |
| 2/5/10초 자동 재시도 3회와 수동 재시도 | 영속 공통 쿨다운·점증·지터와 남은 시간 배너, 재시작·수동 우회 없음 |

### PC·모바일 구서버 정책과 로컬 DB 교환
`official_account` 필드가 **없으면 원격 바인딩 대조만 생략**하고 기존 카카오 인증·동의·writer 연결 규칙으로 정상 동작한다.
필드가 있는 응답은 구조/키를 검사한다. `{dataset_key:null,bound_at:null}`은 미바인딩이며 현재 config로 `connections` 등록을 시도한다.
`official_account:null`, 필수 키 누락, 잘못된 dataset 형식은 `official_account_protocol_required`로 차단한다.
구서버 status에 클라이언트가 임의 바인딩 필드를 만들어 넣지 않는다. 기존 writer를 재사용하고 등록 오류는 동일하게 처리한다.
구서버라도 계정 변경 경고·백업 검증·초기화 순서는 동일하며, `official_account_released:true`가 없는 삭제 응답으로는 변경을 완료하지 않는다.

안전신문고 ID 원문·해시는 개인 DB(`mysafety_sync_meta`/`sync_meta` 포함)에 기록하지 않는다.
1:1은 config의 ID에서 계산한 dataset과 중앙 status의 대조 및 `connections` 거절로 보장한다.
DB 가져오기·복원의 주인 검사는 **기존 카카오 주인 검사만** 유지한다. 같은 카카오면 다른 안신 계정 시절/안신 정보 없는 백업도 허용한다.
카카오 주인 누락·불일치·로그인 미확인은 거절한다. 개발 중 남은 안신 메타는 읽어 판정하거나 새로 기록하지 않으며,
기존 임의 sync_meta 무손실 교환 규칙에 따라 보존한다(별도 삭제 마이그레이션 없음). 테이블/열/스키마 버전 변경은 없다.

### 서버 구현·운영 경계
기존 연결·fact에서 바인딩 backfill을 수행하며 사용자당 dataset/ dataset당 사용자 충돌은 migration 실패로 처리한다.
충돌을 덮어쓰지 않는다. 운영자 수동 해제는 service_role 전용 SQL 함수만 허용하고 감사 기록을 남긴다.
함수 이름·호출법·검증 절차는 auth 구현의 운영 런북에 명시한다. 이 PC 변경은 운영 호출이나 migration을 실행하지 않는다.
map ingest는 연결 dataset과 사용자 바인딩 불일치를 방어적으로 거절한다. 신고가 다른 dataset에 이미 있으면
`report_owned_elsewhere`로 거절하고 감사 기록을 남기며 상대 정보를 반환하지 않는다.

운영자 수동 선점 해제는 선점자의 해당 dataset 공유 fact 삭제·identity tombstone·사용자/dataset 삭제 fence·
해당 dataset 연결 폐기·바인딩 해제를 원자적으로 수행한다. 삭제 건수와 운영자/사유/시각은 private 감사에 남긴다.
삭제 증거는 선점자 사용자 범위이므로 진짜 주인은 같은 dataset과 같은 신고를 새로 연결·업로드할 수 있다.
운영자 함수·감사·backfill·적용 순서는 지도 레포 `docs/implementation/official-account-binding-20261006/MIGRATION.md` 참조.


## v2 수신 가능 여부와 업데이트 안내 (2026-10-21 추가)

status의 최상위 `ingest`는 `{contracts:[1],raw_policy_version:"2026-10-21.1",last_contract,last_client_version,last_parser_version,last_seen_at}`이다.
설정이 켜진 서버만 contracts에 2를 포함한다. last_*는 해당 계정의 가장 최근 실제 ingest 연결에서 얻는다.
미전송 계정은 last_*가 없을 수 있다. client_version과 parser_version은 v1·v2 모두 연결 표에 기록한다.

새 클라이언트는 동의 active, 동의 policy_version=ingest.raw_policy_version, contracts에 정수 2를 모두 만족할 때만 v2로 전환한다.
이 신호는 새 동의의 필요성을 대신하지 않는다. 기존 status 프로토콜은 여전히 1이다.

최상위 `client_notice`는 null 또는 다음 객체다.

```json
{"level":"warning","message":"PC 3.6.0 / 모바일 2.5.0 이상으로 업데이트해 주세요.","min_version":{"pc":"3.6.0","mobile":"2.5.0"},"url":{"pc":"https://github.com/Fentanest/safetyreport/releases/latest","mobile":"https://play.google.com/store/apps/details?id=com.fentanest.mysafetyreport"},"deadline":null}
```

level=info/warning/required. deadline은 ISO 시각 또는 null이다. 메시지는 일반 텍스트로 표시하며 HTML로 해석하지 않는다.
URL은 HTTPS만 연다. v2 ACK도 같은 client_notice를 포함한다. HTTP 426 client_upgrade_required는 재전송 반복 대신 업데이트를 안내한다.
설정의 단일 정본은 private.community_ingest_config이며 두 Edge가 같은 내부 RPC를 읽는다.

실패 허용: `internal_community_ingest_capabilities`·`internal_account_ingest_state` RPC가 실패하거나 아직 없으면
(지도 migration 보다 auth 를 먼저 배포한 경우 등) status 는 500 이 아니라 200 으로 `ingest:{contracts:[1],raw_policy_version:null}`·`client_notice:null`
(v1 전용)을 돌려주고 경고 로그(`level:"warn"`, `outcome: ingest_capabilities_unavailable | ingest_state_unavailable`)만 남긴다.
한쪽만 실패하면 성공한 쪽 값은 그대로 싣는다. capabilities 의 `contracts` 가 양의 정수 배열이 아니면 실패로 본다.

브라우저 경계: community-account 는 어떤 action 에도 CORS 허용 헤더를 내지 않는다(이전과 같음). 지도 웹은 이 API 를
부르지 않는다(지도 레포 PROJECT_RULES 7 — 지도 로그인과 동의·writer·업로드 정책을 섞지 않음). 지도 웹의 업데이트 안내는
지도 레포 `my-analytics/client-status`(지도 세션 토큰, 지도 origin 허용 목록, 지도 rate limit)가 같은 설정을 읽어 보여 준다.
