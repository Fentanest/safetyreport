# community-ingest 오류 코드 v1

요청 단위 오류는 `{"error":{"code","message","request_id","retryable","retry_after_seconds?"}}`. 이벤트 단위는 ACK `results[].error`.

| code | HTTP | retryable | 앱 처리 |
|---|---|---|---|
| `method_not_allowed` / `unsupported_media_type` | 405 / 415 | no | 버그, dead_letter |
| `payload_too_large` | 413 | no | batch 를 줄여 재시도, 단건도 크면 dead_letter |
| `invalid_request` / `schema_invalid` / `payload_hash_mismatch` / `event_type_mismatch` | 400 / 422 | no | dead_letter |
| `auth_required` | 401 | 토큰 갱신 1회 | 실패면 auth_required |
| `kakao_required` / `session_revoked` | 403 | no | 게이트 무효화, auth_required |
| `consent_missing` / `consent_revoked` / `consent_outdated` / `consent_grant_unknown` | 403 | no | 게이트 무효화, blocked |
| `connection_unknown` / `connection_revoked` / `connection_suspended` / `connection_session_mismatch` / `connection_mode_mismatch` | 403 | no | rebind 시도(같은 사용자) 또는 blocked |
| `writer_superseded` | 403 | no | blocked, UI: 다른 기기로 전환됨 |
| `contributor_suspended` | 403 | no | 게이트 무효화, blocked |
| `rate_limited` | 429 | yes(Retry-After) | retry_wait |
| `busy` | 503 | yes | 백오프 |
| `server_error` / `service_unavailable` | 500 / 503 | yes | 백오프 |
| 이벤트 `conflict`(`event_id_conflict`) | (200 안) | no | dead_letter, 원본 불변 |
| 이벤트 `rejected`(`writer_epoch_mismatch` / `deleted`) | (200 안) | no | blocked 보존 |
| 이벤트 `quarantined`(`status_mapping_mismatch`) | (200 안) | — | durable, outbox 정리, 확인 필요 표시 |

응답에 스택·토큰·SQL·원문을 넣지 않는다. 같은 오류가 타인 ID 존재 여부를 드러내지 않게 한다(`not_found` 통일).
