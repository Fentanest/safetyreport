# community-ingest v2

정본 계획: docs/implementation/raw-json-ingest-20261021/PLAN.md 3–7절. v1 DTO 계약과 병행한다.

`envelope.schema.json` → `event.schema.json` → `supplement.schema.json` 순서로 검증한다.
원본은 **문자열**이다. 신고자 키 `C_NAME,C_EMAIL,C_PHONE,C_PHONE2,C_ID`만 result에서 기기 내 제거한다.
`raw.body`는 로컬 저장 UTF-8을 그대로 복원한다. 키 정렬·공백 제거·JSON 재직렬화 금지.
sha256와 size는 이 바이트를 대상으로 한다. 숫자 1.0과 1, 키 순서, 공백 차이를 보존한다.

보조 필드와 비교 DTO는 v1 canonical-json 규칙(키 UTF-16 순서, 공백 없음, 안전 정수)을 쓴다.
모르는 보조 값은 null이며 모든 키가 필수다. DTO와 DTO 해시는 둘 다 있거나 둘 다 없다.
최대 20건, gzip 전 1MiB, 전송 512KiB. v1은 identity·256KiB를 유지한다.

프로토콜 판정(gzip): `Content-Encoding: gzip` 본문은 v2 요청으로만 본다(v1은 gzip을 쓴 적이 없다).
- identity 본문은 v1·v2 모두 256KiB 상한이며 넘으면 인증·JSON 해석 전에 413 `payload_too_large`다(v1 의미 그대로).
  256KiB를 넘는 v2 묶음은 반드시 gzip으로 보낸다.
- gzip 본문은 전송 512KiB 상한으로 읽고, 사용자 JWT 확인 → 사용자 rate limit → 공유 설정(contracts에 2) 확인 **뒤에만** 푼다.
  압축 해제는 1KiB 입력 조각 단위로 하며 누적 출력이 1MiB를 넘는 즉시 중단하고 413 `payload_too_large`다.
- v2를 지원하지 않는 배포에서 gzip은 다른 미지원 인코딩(`br` 등)과 같은 415 `unsupported_media_type`이며 풀지 않는다.
  설정으로 꺼진 배포(공유 설정 없음)는 인증 전에 거절한다. 공유 설정(`community_ingest_config`)이 v2 를 끈 경우는 인증·rate limit 뒤
  설정 확인 직후 503 `service_unavailable`(재시도 가능)이며 풀지 않는다 — 클라이언트는 다음 status 확인에서 v1 으로 돌아간다. manifest(v1 전용)의 gzip은 415다.
- 풀린 본문이 JSON이 아니거나 gzip이 깨졌으면 400 `invalid_request`, 풀린 본문의 protocol이 1이면 415다.
- identity v2(256KiB 이하)는 v2가 꺼져 있으면 503 `service_unavailable`다. 공유 설정 RPC가 실패하면 v2는 503(재시도),
  v1은 차단일이 없는 것처럼 계속 처리한다.

원본 문자열 규칙: `raw.body`는 어느 객체에서든 키가 중복되면 안 되며(신고자 키 검사 우회 방지), 짝 없는 UTF-16 surrogate
(문자 또는 `\ud800` 같은 이스케이프)와 U+0000(문자 그대로뿐 아니라 `\u0000` 이스케이프, 키 포함)을 담을 수 없다. 어기면 422 `raw_invalid`다.
보조 필드는 `supplement_invalid`, 비교 DTO·client_version·parser_version은 `schema_invalid`로 같은 surrogate 규칙을 적용하며
비교 DTO의 U+0000도 `schema_invalid`다.
선택 필드 `client_observation`의 **구조·surrogate·U+0000·해시 쌍** 위반은 위와 같이 거절하지만, 구조가 맞는데 **값** 검증(달력 날짜,
좌표 범위·정규형, 금액, 제어문자 등)만 실패하면 원본 보존이 우선이므로 이벤트를 거절하지 않고 서버가 그 비교 DTO(와 해시 쌍)를 버린 채
원본만 저장한다(비교 진단만 빠진다). 해시 쌍은 둘 다 있거나 둘 다 없어야 하며, 해시가 틀리면 값이 잘못돼 있어도
`client_observation_hash_mismatch`로 거절한다.
v2의 `status_correction` 이벤트는 event.schema.json enum 밖이므로 422 `schema_invalid`, reshare와 trigger 불일치는 `event_type_mismatch`다.

서버는 인증·동의·writer·fence·해시를 검사한 뒤 원본과 대기열만 저장한다.
`stored`와 유효 영수증은 전송 완료다. `parse_pending` 문구는 “중앙 저장 완료·지도 반영 대기”.
파서 실패는 이전 fact를 유지하며 서버 대기열에서 재시도한다. 클라이언트 원본을 ACK에 반환하지 않는다.
같은 event_id의 불변 필드가 다르면 conflict다. 원본 메타와 세 콘텐츠 해시도 불변 필드다.
같은 신고는 `(writer_epoch,source_revision)`이 큰 관측을 채택하고, 동률이면 raw가 dto보다 우선한다.

수신 플래그·정책 버전·v1 차단일·client_notice는 private.community_ingest_config 한 행이 정본이다.
account status와 ingest가 같은 RPC를 읽는다. 플래그 기본 false, 차단일 기본 null.
새 동의가 active이고 그 정책이 status.ingest.raw_policy_version과 일치하며 contracts에 2가 있을 때만 전환한다.

client_notice 표시(PC·모바일 같음): status 의 client_notice 가 정본이다. 앱 버전이 해당 플랫폼 min_version 보다 낮을 때만 보이고,
그 플랫폼 min_version 이 null 이면 보이지 않는다. info·warning 은 배너, required 는 하루 한 번 대화상자다. status 가 notice 를
null 로 주거나 키가 없으면 안내와 426 표시를 지운다. 426 을 받은 행은 막지 않고 재시도 대기(1시간)로 둔다.

v1 복귀: 한 번 v2 로 전환한 뒤에도 status.ingest.contracts 에 2 가 없으면 v1 으로 돌아간다. 대기 중인 실시간·수동·재공유
이벤트는 같은 event_id 의 v1 payload 로 보내고, raw_backfill 이벤트는 v2 가 다시 켜질 때까지 보류한다.

재생성: `python3 scripts/raw-ingest/generate_contract.py`.
사본 검사: `python3 scripts/raw-ingest/check_contract.py --copies ../pc ../mobile`.
합성 바이트 벡터: vectors/wire.json. 공통 게이트 벡터: gate-vectors.json.
