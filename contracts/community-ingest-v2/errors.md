# v2 오류

v1 오류와 재시도 규칙을 상속한다. 503 을 뺀 아래 오류는 자동 재시도로 고칠 수 없다.

| HTTP | 코드 | 처리 |
|---|---|---|
| 422 | raw_hash_mismatch | UTF-8 바이트·크기·해시 불일치, 원본 재수집 |
| 400 | invalid_request | JSON 아님, gzip 손상, protocol 이 정수 1·2 아님 |
| 413 | payload_too_large | identity 256KiB 초과(인증 전), gzip 전송 512KiB 초과, 압축 해제 후 1MiB 초과 |
| 415 | unsupported_media_type | identity·gzip 외 인코딩, v2 를 지원하지 않는 배포의 gzip, gzip 안의 v1 본문, manifest 의 gzip (공유 설정이 v2 를 끈 경우는 503 service_unavailable) |
| 422 | schema_invalid | envelope/event 스키마 위반(v2 의 status_correction, 짝 없는 surrogate·U+0000 이 든 비교 DTO·버전 문자열 포함; 비교 DTO 의 값 검증 실패는 오류가 아니라 비교 DTO 를 버리고 저장) |
| 422 | event_type_mismatch | reshare 이벤트와 trigger=reshare 불일치 |
| 422 | raw_invalid | JSON/result/메타/신고 식별자 오류, raw.body 의 중복 키·짝 없는 surrogate·U+0000(`\u0000` 이스케이프 포함), 원본 재수집 |
| 422 | raw_reporter_keys_present | 신고자 키가 남음, 전송 차단 |
| 422 | supplement_invalid | 보조 필드 형식 오류 |
| 422 | supplement_hash_mismatch | 보조 필드 해시 오류 |
| 422 | client_observation_hash_mismatch | 비교 DTO 해시 오류 |
| 426 | client_upgrade_required | 업로드 중지·업데이트 안내. v1_sunset 뒤 v1, 또는 (2026-10-22) 필수 버전 미만의 v1·v2. 본문 `error.required_version`·`update_url`·`update_message`(필수 버전일 때). 재시도로 풀리지 않는다 |
| 503 | service_unavailable | identity v2 비활성, 공유 설정 RPC 실패(v2 만, 재시도 가능) 또는 일시 장애, Retry-After 뒤 재확인 |
| 503 | maintenance | (2026-10-22) 운영자가 업로드를 일시 중지함. v1·v2·manifest·progress 모두, DB 쓰기 전에. `retryable:true`, 본문 `error.resume_at`(ISO 또는 null)·`pause_message`, `Retry-After` = 재개 시각까지 남은 초(재개 시각 없으면 600). 신고별 실패 횟수에 넣지 않는 일시적 오류 |
| 422 | schema_invalid | (progress) progress.schema.json 위반 |
| 413 | payload_too_large | (progress) 본문 4KiB 초과 |
| 429 | rate_limited | (progress) 같은 연결에서 60초 안에 두 번째 보고. `Retry-After` 는 남은 초 |

ACK stored/durable=true/유효 receipt_id는 완료이며 projection_status=parse_pending을 별도로 표시한다.

progress(2-1)의 인증·연결 오류는 ingest 와 같다(`auth_required`, `kakao_required`, `session_revoked`, `consent_missing`, `consent_revoked`,
`connection_unknown`, `connection_revoked`, `connection_session_mismatch`, `connection_mode_mismatch`, `writer_superseded`, `contributor_suspended`).
progress 는 동의가 outdated 여도 받고, 필수 버전(426)을 적용하지 않는다. 앱은 progress 실패를 조용히 넘기고 재시도하지 않는다.
