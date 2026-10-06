# 클라이언트 규칙 (protocol 1)

## 1. status DTO 정규화
게이트 판정 전에 status 응답을 아래처럼 정규화한다. 정해진 형이 아니면 없는 값으로 본다(형을 바꿔 읽지 않는다). 나머지 키는 그대로 둔다.

| 경로 | 형 | 형이 다르거나 없을 때 |
|---|---|---|
| `gate.kakao` | JSON `true` 만 참 | `false` |
| `gate.reasons` | 문자열 배열(문자열이 아닌 원소는 버림) | `[]` |
| `contributor.status` | 문자열 | `null`(게이트는 `active`·`none` 밖이므로 정지로 판정 — gate.md 5) |
| `consent.state` · `grant_id` · `policy_version` · `consent_text_sha256` | 문자열 | `null` |
| `policy.required_version` · `consent_text_sha256` | 문자열 | `null` |
| `account.fingerprint` · `display_name` | 문자열 | `null` |
| `connection` | 객체 | `null` |

`gate`·`contributor`·`consent`·`policy`·`account` 가 객체가 아니면 빈 객체로 본다. 최상위가 객체가 아니면 응답 오류(`server_error`)다.

## 2. 오류 분류와 재시도
- HTTP 200 이고 최상위가 객체이며 `error` 키가 없으면 성공. 그 밖(4xx/5xx, JSON 아님, 최상위가 객체 아님, 200 이어도 `error` 있음)은 오류다.
- 코드: `error.code` 가 문자열이면 그 값, 아니면 `server_error`.
- 일시 오류(`transient`): `error.retryable` 이 불리언이면 그 값을 따른다(중앙이 정본). 없으면 **본문의** `error.code` 가 `rate_limited`·`busy`·`server_error` 이거나
  HTTP ≥ 500 이면 일시 오류. 본문에 코드가 없어 `server_error` 로 채운 경우는 HTTP 상태로만 판단한다(본문 없는 401, 깨진 200 은 일시 오류가 아님).
  네트워크 실패·시간 초과는 일시 오류다.
- 재시도 대기(`retry_after_seconds`): `error.retryAfterSeconds` 가 0 이상 수이면 그 값, 아니면 `Retry-After` 헤더가 0 이상 정수 초이면 그 값, 아니면 없음.
- 게이트 처리: `auth_required` 또는 HTTP 401 → 인증 오류(세션 무효화). 그 밖의 일시 오류가 아닌 오류 → 게이트 무효화(다시 확인할 때까지 진입 불가).
  일시 오류 → 마지막 status 를 캐시 기간(10분) 안에서만 유지하고 그 뒤엔 확인 필요.

## 3. 기기 이름
- 검증(중앙·서버): NFC 정규화 → 공백 문자(JS `\s` 집합: 탭·줄바꿈·NBSP·전각 공백·U+2000~200A·U+2028/2029·U+202F·U+205F·U+FEFF 등)를 한 칸으로 → 앞뒤 공백 제거.
  1~40 코드 포인트, 제어 문자(U+0000~001F, U+007F~009F)·방향/폭 제어(U+200B~200F, U+202A~202E, U+2066~2069)·`< > " ' \` \\` 금지, `scheme:` 꼴(영문자로 시작) 금지.
- 정리(모바일, OS 기기 이름 → 표시 이름): 금지 문자를 공백으로 바꾸고 공백을 한 칸으로 줄여 앞뒤를 떼고, 비거나 `scheme:` 꼴이면 기본값, 아니면 앞 40 코드 포인트.
  정리한 값은 항상 검증을 통과해야 한다.

## 4. 캐시와 늦은 응답
- 게이트 캐시: 확인한 지 `age ≤ ttl`(600초)이면 유효, 넘으면 확인 필요. 새 작업 전 확인은 상한 60초(`age ≤ 60`).
- 늦은 응답: status 조회를 시작한 때의 (무효화 세대, 앱 모드, 세션 계정)과 응답을 받은 때가 다르면 그 응답으로 게이트를 바꾸지 않는다.
  세션이 유효하지 않으면 버린다. 시작할 때 계정을 몰랐으면(null) 계정 비교는 하지 않는다.
