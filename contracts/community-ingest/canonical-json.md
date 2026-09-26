# 해시용 정규 JSON (canonical JSON) v1

`payload_sha256 = lowercase hex(SHA-256(UTF-8 bytes of canonical_json(payload)))`.
클라이언트 해시는 보안 증명이 아니다. 서버는 받은 payload 로 같은 규칙을 다시 계산하고, 다르면 422 `payload_hash_mismatch` 로 거절한다.

## 규칙
1. 값 종류는 object, string, 정수, null 만 쓴다. **부동소수·불리언·배열은 payload 에 없다**(좌표는 10진 문자열).
2. object 키는 UTF-16 코드 단위 순서로 오름차순(모든 키가 ASCII 라 바이트 순서와 같다). 중첩 object 에도 재귀 적용.
3. 구분자는 `,` 와 `:` 이고 공백·줄바꿈을 넣지 않는다.
4. 문자열: 비ASCII 문자를 이스케이프하지 않고 그대로 UTF-8 로 쓴다(Python `ensure_ascii=False`). `"` → `\"`, `\` → `\\` 만 이스케이프한다.
   DTO 생성 단계에서 문자열의 제어문자(U+0000–U+001F, U+007F)와 연속 공백을 공백 하나로 바꾸고 앞뒤를 자르므로, 정규 JSON 에는 제어문자가 나오지 않는다.
   짝 없는 서로게이트는 DTO 단계에서 거절한다. 유니코드 정규화(NFC 등)는 하지 않는다(받은 문자열 그대로 — 세 언어 동작을 같게 하려는 결정).
5. 정수: 부호 `-` 와 10진 숫자만, 앞자리 0 없음, `-0` 없음. 범위는 ±2^53-1.
6. null 은 `null`. 값이 null 인 키도 생략하지 않는다(스키마가 정한 모든 키가 항상 있다).

## 구현 대응
- Python: `json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))` — 규칙 1·4 의 전처리가 끝난 값에서만 같다.
- Dart: 키를 정렬한 `SplayTreeMap` 재귀 변환 후 `jsonEncode`.
- TypeScript: 키 정렬 재귀 복사 후 `JSON.stringify`.
- 세 구현은 `vectors/canonical-json.json` 의 모든 항목에서 같은 문자열·해시를 내야 한다.
