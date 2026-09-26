# 공식 관측 → 공유 DTO (observation-v1)

## 1. 확정 시점
공식 상세 응답을 받고 파서가 끝난 직후, **개인 수정값(override)·별점 보강·화면 계산과 합치기 전** 에 DTO 를 만들고 정규 JSON 문자열로 확정한다.
그 문자열을 앱의 `community.db` source journal 에 먼저 commit 한 뒤에 개인 DB 저장을 진행한다. 이후 어떤 경로도 journal 의 payload 를 바꾸지 않는다.
전송·재시도·수동·자정 업로드는 journal 의 문자열을 그대로 보낸다(개인 DB 재조회 금지).

## 2. 플랫폼 중립 입력
각 앱은 자기 파서 출력을 아래 입력으로 옮기는 어댑터만 갖고, DTO 규칙은 공통이다.

| 입력 키 | PC(`services/parser.py` 출력 / 저장 열) | 모바일(`Report`) |
|---|---|---|
| `processing_status` | `processing_status` / 처리상태 | `status` |
| `penalty_amount` | `penalty_amount` / 범칙금_과태료 | `fineInfo` |
| `report_date` | `title_fields['신고일']` | `date` |
| `response_date` | `response_date` / 답변일 | `responseDate` |
| `processing_agency` | `processing_agency` / 처리기관 | `agency` |
| `person_in_charge` | `person_in_charge` / 담당자 | `manager` |
| `car_number` | `car_number` / 차량번호 | `carNumber` |
| `violation_location` | `violation_location` / 위반장소 | `location` |
| `entry_value` | `entry_value` | `entryValueFromDetail(...)` |
| `penalty_points` | `penalty_points` / 벌점 | `penaltyPoints` |
| `geocode` | `_prefetch_derived()` 의 위도·경도·지오코딩상태(공식 주소로 계산, 원본 상세 행 기준) | 공식 주소 정규화 키의 `geocode_cache` 행 |

`geocode` 는 **공식 주소**로 얻은 값만 쓴다. 사용자 override 주소·좌표는 읽지 않는다.

## 3. DTO 규칙
- 문자열 정리 `clean(s, n)`: None/비문자열 → 빈 문자열, 제어문자(U+0000–U+001F, U+007F)와 모든 공백 연속을 공백 하나로, 앞뒤 공백 제거, **코드포인트 기준** n 자로 자름. 결과가 빈 문자열이면 null.
- `status` = 아래 표(입력은 `clean(processing_status, 40)`). 표에 없으면 `other`.

| 처리상태 | status | eligible | 기존 종결여부 |
|---|---|---|---|
| 수용 | accepted | ✅ | Y |
| 일부수용 | partial | ✅ | Y |
| 불수용 | rejected | ✅ | Y |
| 답변완료 | completed_unknown | ✅ | Y |
| 기타 | completed_unknown | ✅ | Y |
| 취하 | withdrawn | ❌ | Y |
| 이송 | transferred | ❌ | Y |
| 보완요청 | supplement | ❌ | N |
| 처리중 | processing | ❌ | N |
| (그 밖·빈 값) | other | ❌ | N |

- `status_raw` = `clean(processing_status, 40)`(빈 값이면 null).
- `eligible` = status ∈ {accepted, partial, rejected, completed_unknown}. **`종결여부=Y` 와 같은 뜻이 아니다**(취하·이송은 완료 답변이 아님).
- `category`: entry_value 에 `자동차·교통위반` 포함 → `traffic`, `불법주정차신고` 포함 → `parking`, 그 밖 → `other`(PC `category_from_entry_value` 와 같음).
- 날짜 `day(s)`: 앞 10자가 `YYYY-MM-DD` → 그대로, `YYYY.MM.DD` → 점을 `-` 로, 8자리 숫자 `YYYYMMDD` → 하이픈 삽입; 실제 달력 날짜가 아니면 null. 시각이 붙은 값은 앞 날짜만(안전신문고 표시 시각은 KST).
- `report_date` = day(report_date). `completed_date` = eligible 이면 day(response_date), 아니면 null. 결측을 업로드일·신고일로 채우지 않는다.
- 금액(확정만, 규칙 추정 금액은 넣지 않음) — `penalty_amount` 전체가 정확히 다음 문법일 때만 확정 금액:
  `^(과태료|범칙금):\s*(<수>)\s*원$`, `<수>` = `[0-9]{1,3}([,.][0-9]{3})*` 또는 `[0-9]+`(구분자 `,`·`.` 은 세 자리 묶음만).
  `confirmed_won` = `<수>` 의 숫자만 이은 정수, 단 100,000,000 초과면 null. 음수·`만`·소수·잘못된 묶음 등 문법 불일치면 null(추측 금지). 0 은 0.
  `kind`: 앞머리가 `범칙금` → `penalty`, `과태료` → `fine`(금액 문법 불일치여도 앞머리로 판정), 그 밖 → `unknown`. `combined` 은 두 앞머리가 한 값에 함께 있을 때만 — 현재 파서는 만들지 않으므로 예약값.
  `penalty_points`: `penalty_points` 입력 전체가 `^벌점:\s*([0-9]{1,4})\s*점$` 이고 값 ≤ 1000 이면 정수, 아니면 null.
- `disposition`: status=rejected → `none`; 아니면 penalty_amount 가 `범칙금` 으로 시작 → `penalty`, `과태료` 로 시작 → `fine`, `경고` → `warning`, 그 밖(`미확인`·빈 값 포함) → `unknown`. 금액·처분을 추측하지 않는다.
- `agency_name` = clean(processing_agency, 200), `manager_name` = clean(person_in_charge, 160), `vehicle_raw` = clean(car_number, 64), `address` = clean(violation_location, 200).
- `location`: geocode 상태가 `ok` 이고 lat·lng 가 IEEE 754 double 로 해석되며(숫자 또는 10진 문자열) lat ∈ [32, 39.5], lng ∈ [124, 132] 이면
  lat·lng = 그 double 의 **최단 왕복 10진 문자열**(지수 표기 없음, 소수점이 없으면 `.0` 을 붙임 — Python `repr`, Dart `toString`, JS `String()` 후 보정), `source = "geocode"`;
  아니면 lat·lng null, `source = "none"`. 좌표를 반올림·격자화하지 않는다(공개 정책: 입력 좌표 그대로).

payload 키는 항상 모두 있다: `address, agency_name, amount{confirmed_won, kind, penalty_points}, category, completed_date, disposition, location{lat, lng, source}, manager_name, report_date, status, status_raw, vehicle_raw`.
공식 로그인 정보·쿠키·헤더·사진·첨부·신고 본문·처리내용 원문은 넣지 않는다. `source_report_id` 는 envelope 의 event 필드(private)로만 간다.

## 4. event 결정 (앱의 capture 함수)
`prev` = 같은 로컬 데이터셋에서 같은 신고의 **가장 최근 journal 행**(rebuild 중이면 이번 run 의 staging 을 먼저 본다).
로컬 prev 가 없고 그 신고의 `source_report_key` 앞 24hex 가 `server_completed`(중앙 manifest — 이 dataset 에서 이미 completed 로 저장된 신고)에 있으면 prev 를 "eligible, 해시 불명"으로 본다(S-04: writer 전환·재설치 뒤 첫 비적격 관측도 정정을 보냄).
- eligible: prev 가 있고 payload_sha256 이 같으면 새 이벤트 없음(내용 변화 없음). 아니면 `completed_observation`.
- not eligible: prev 가 eligible 이면 `status_correction`(payload = 이번 관측 그대로). 아니면 이벤트 없음.
- 이벤트가 없고 prev 도 없으면(예: 처음 본 처리중·취하 신고) `detail_status` 만 기록하고 report_latest/staging 은 쓰지 않는다(가리킬 journal 행이 없음). 이것은 capture 성공이다(S-06).
- `location_supplement`: 수동·자정·recovery 트리거 때, 신고별 최신 journal 행이 eligible 이고 `location.source="none"` 인데 그 행의 `address` 로 지오코딩 캐시(공식 주소 결과만)가 이제 `ok` 이면, 같은 payload 에 location 만 채운 새 이벤트.
- `reshare`: 재동의·writer 전환 뒤 사용자가 지도 탭에서 **명시적으로** 요청할 때만. 신고별 최신 eligible journal 행의 payload·captured_at 을 그대로 두고 새 event_id·새 source_revision·현재 grant/connection/epoch 로 발급. 자동 실행 금지.
- 개인 편집·백업 복원·DB 변환·가져오기·모바일 Client 는 이벤트를 만들지 않는다.
- capture 가 실패하면(community.db 오류 등) **그 신고의 개인 저장도 하지 않는다**(저장 실패로 집계). 개인 상태가 전진하지 않으므로 다음 수집의 선정 규칙(신규·미종결·목록 상태 변경)이 그 신고를 다시 읽는다 — 공유 사본을 영구히 놓치지 않는다(S-03).
- 상세를 받을 때마다(이벤트 여부와 무관) `detail_status` 에 그 상세의 C_NOW 라벨(`progress_status`)을 같은 트랜잭션으로 기록한다(목록 상태 변경 감지용, S-12).
- `event_id` = 새 UUIDv4(소문자). `source_revision` = `community.db` **전체**에서 단조 증가하는 정수(`meta.next_revision` — 데이터셋 회전으로 초기화하지 않음, 서버 `last_accepted_revision` 보다 작아지지 않게 올림). 그래서 회전 전 대기 이벤트가 회전 뒤 새 관측보다 늦게 도착해도 새 관측을 되돌리지 못한다(S-20). `captured_at` = UTC ISO-8601 `Z`(밀리초).

## 5. 서버 검증·파생(ingest)
- 한 요청 안에서 같은 신고(`source_report_id`)의 이벤트는 하나만(S-11-B). 업로더는 같은 신고의 다음 이벤트를 앞 요청의 ACK 뒤에 보낸다.
- 같은 event_id 재전송 판정: 불변 필드(event_type, source_report_id, source_revision, writer_epoch, captured_at, payload_sha256, 연결의 dataset_key)가 모두 같으면 `duplicate`, 하나라도 다르면 `conflict`. grant·connection·trigger·request_id 는 전송 문맥이라 비교하지 않는다(재로그인 rebind·정책 재동의 뒤 재전송 허용).
스키마(`observation.schema.json`)가 형식·enum·길이·상한을 검사하고, 서버 코드가 아래 **값 규칙**을 추가로 검사한다(위반 = 422 `schema_invalid`, 쓰기 0):
- 날짜: 실제 달력 날짜(`2026-02-30` 거부). `completed_date` 는 payload 가 eligible 일 때만 값을 가질 수 있다(not eligible 인데 값이 있으면 거부).
- 좌표: `source="none"` ⇒ lat·lng 둘 다 null. `source="geocode"` ⇒ 둘 다 문자열, double 로 해석되고 lat ∈ [32, 39.5]·lng ∈ [124, 132], 그리고 **정규형**(해석한 double 의 최단 왕복 표기 + `.0` 규칙)과 문자열이 정확히 같아야 한다(`37.000`·`+37.5` 거부).
- 금액: `confirmed_won` ≤ 100,000,000, `penalty_points` ≤ 1000(스키마), kind=unknown 이면 confirmed_won 은 null.
- `status == map(status_raw)` 가 아니면 그 이벤트는 `quarantined:status_mapping_mismatch`(durable, fact 반영 안 함).
- event_type 일관성: `completed_observation`·`location_supplement`·`reshare` 는 eligible payload 만, `status_correction` 은 not eligible payload 만, `location_supplement` 는 `location.source="geocode"` 만. 어기면 422. `reshare` 이벤트는 envelope `trigger="reshare"` 에서만 허용.
- 서버가 `source_report_key = sha256(utf8("safetyreport|" + source_report_id))` 를 계산한다(클라이언트 값 받지 않음). fact 키는 (contributor, 연결의 dataset_key, source_report_key).
- 삭제 tombstone 은 (contributor, source_report_key) — dataset_key 와 무관 — 이고, 그 신고의 이벤트는 captured_at·dataset_key 와 무관하게 영구 `rejected:deleted`.
- grant 귀속: 기존 fact 의 grant 계보가 **사용자 철회로 비활성**이면, `reshare` 가 아닌 이벤트는 내용·순서만 갱신하고 fact 는 옛 (비공개) grant 에 남긴다 → 공개되지 않음(`projection_status=held`). `reshare` 이거나 계보가 활성(정책 갱신 재동의 포함)이면 현재 grant 로 귀속(S-02).
- ACK `projection_status`(실제 공개 조건 기준 — 공개 RPC 와 같은 함수 `community_fact_publicly_listed`: completed ∧ 신고일·처리완료일 중 하나 이상 ∧ contributor active ∧ 계보 활성):
  `published` = 커밋 뒤 익명 API 가 이 fact 를 보여 줌(ready ∧ generated_at 존재) · `removed` = 보이던 fact 가 이번 변경으로 안 보이게 됨 ·
  `held` = 보일 조건이지만 공개 스위치 꺼짐(ready=false) 또는 계보 비활성 · `not_public` = 저장만 되고 목록에 안 나옴(미완료, 날짜 둘 다 없음) · `not_applicable` = fact 변경 없음.
- 삭제 보장 범위(S-02-F): 서버가 보장하는 것 — ① 삭제 시점의 모든 writer 연결 폐기(그 연결의 대기 이벤트는 captured_at 과 무관하게 거절), ② 이미 공유된 신고 identity 의 영구 tombstone, ③ 삭제 시각 이전으로 **주장된** captured_at 의 이벤트 거절. captured_at 은 클라이언트 시각이므로 ③은 정상 앱을 위한 보호다. 정상 앱은 삭제 성공 뒤 삭제 이전 journal 을 새 연결로 승계·재발급(reshare 포함)하지 않는다(계약 local-store). 조작된 클라이언트가 미래 시각으로 자기 자료를 다시 올리는 것은 막지 못한다(사용자 자신의 자료, 보안 경계 밖).
- 파생: `public_state` = eligible ? `completed` : `not_completed`; lat/lng = 문자열을 double 로(원 문자열도 `lat_text`/`lng_text` 로 보존); `point_key = "v1:" + lat + "," + lng`(문자열 그대로);
  `region_code` = 주소 앞 두 토큰(공백 분리, 시·도 약칭 정규화) — 공개 필터용 표시 키, 행정코드가 아님;
  `agency_key = "a1:" + sha256(NFC(agency_name))[:24]`, `manager_key = "m1:" + sha256(agency_key + "|" + NFC(manager_name))[:24]`(기관이 다르면 같은 이름도 다른 키).
