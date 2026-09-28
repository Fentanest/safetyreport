# 공식 관측 → 공유 DTO (observation-v4)

## 1. 확정 시점
공식 상세 응답을 받고 파서가 끝난 직후, **개인 수정값(override)·별점사유 보강·화면 계산과 합치기 전** 에 DTO 를 만들고 정규 JSON 문자열로 확정한다.
그 문자열을 앱의 `community.db` source journal 에 먼저 commit 한 뒤에 개인 DB 저장을 진행한다. 이후 어떤 경로도 journal 의 payload 를 바꾸지 않는다.
전송·재시도·수동·자정 업로드는 journal 의 문자열을 그대로 보낸다(개인 DB 재조회 금지).

## 2. 플랫폼 중립 입력
각 앱은 자기 파서 출력을 아래 입력으로 옮기는 어댑터만 갖고, DTO 규칙은 공통이다.

| 입력 키 | PC(`services/parser.py` 출력 / 저장 열) | 모바일(`Report`) |
|---|---|---|
| `processing_status` | `processing_status` / 처리상태 | `status` |
| `report_number` | `title_fields['신고번호']`(없으면 상세의 신고번호) | `reportNumber` |
| `penalty_amount` | `penalty_amount` / 범칙금_과태료 | `fineInfo` |
| `report_date` | `title_fields['신고일']` | `date` |
| `response_date` | `response_date` / 답변일 | `responseDate` |
| `processing_agency` | `processing_agency` / 처리기관 | `agency` |
| `person_in_charge` | `person_in_charge` / 담당자 | `manager` |
| `car_number` | `car_number` / 차량번호 | `carNumber` |
| `violation_location` | `violation_location` / 위반장소 | `location` |
| `entry_value` | `entry_value` | `entryValueFromDetail(...)` |
| `penalty_points` | `penalty_points` / 벌점 | `penaltyPoints` |
| `rating` | `title_fields['별점']`(공식 상세의 만족도 점수) | `rating` |
| `violation_law` | `violation_law` / 위반법규 (2026-09-28, v2) | `law` |
| `geocode` | 공식 상세 응답의 `C_A_W/E` 또는 완료된 보완의 `SPLMNT_C_A_W/E` | 같은 공식 상세 응답의 `C_A_W/E` 또는 완료된 보완의 `SPLMNT_C_A_W/E` |

`geocode` 는 기존 전송 형식의 좌표 있음 표시값이다. 실제 좌표는 공식 상세 응답에서 읽고, 주소를 카카오 REST API로 변환하지 않는다. 사용자 override 주소·좌표는 읽지 않는다.

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
  `agency_name` 은 선택된 답변의 기관명 **원문**이며 현행 표시명으로 재정의하지 않는다(원문·현행 분리 — handoff §4).
- `rating` = 공식 상세의 숫자 별점이 정수 1..5이면 그 값, 아니면 null (v4, 2026-09-28). `별점사유`는 입력·payload·해시에 넣지 않는다. 나중에 공식 상세에서 별점이 확인되면 변경된 payload 해시로 `completed_observation`을 발급한다.
- `violation_law` = clean(violation_law, 60) (v2, 2026-09-28). 파서가 처리내용에서 뽑은 **법 이름·조항만**(예: `도로교통법 제32조`) — 처리내용 원문은 보내지 않는다. 못 뽑았으면 null.
- `source_agency_code` = 원문(32자 이내, 2026-09-28) (v3).
  선택된 답변의 `C_MANAGE_ORG` **원문**(TEXT, 7자리 영숫자·선행 0 보존, 정수 변환 금지).
  HTML fallback 등 코드를 얻지 못하면 null(후보 코드를 원문 필드에 써넣지 않음). 검증되지 않은 신규 형식도 원문 그대로 보존한다.
  32자를 초과하면 앱이 조용히 null 로 버리지 않는다 — 수집 단계에서 `blocked:source_agency_code_too_long` 사유로
  전송 제외하고 journal 에 명시 기록한다(명시적 거절, REVIEW3 낮음-1). 차단된 코드를 null 또는 정상 코드로 고치면 payload 해시가 같더라도 새 완료 관측을 발급해 전송 가능 상태로 복구한다(REVIEW5). 서버 edge 는 같은 값을 422 `schema_invalid`
  (사유 `source_agency_code_too_long`)로 거절한다. 세 층(서버·PC·모바일)의 규칙·사유 문자열은 동일하다.
  서버는 7자리 영숫자만 기관 해석에 쓰고 나머지는 미확인으로 둔다. `agency_name` 과 같은 답변에서 가져온다(섞지 않음).
  v1/v2 payload 에는 이 키가 없다 — **키 부재는 명시적 null 이 아니다**.
  중앙은 키가 없을 때 저장된 코드를 NULL 로 지우지 않고 보존한다. 단 키 없는 payload 가 기관명이 바뀐 새 답변을
  보내면 옛 기관코드는 붙이지 않는다(같은 기관명이면 보존, 바뀌면 NULL — REVIEW3 중간-4).
- `location`: geocode 상태가 `ok` 이고 lat·lng 가 IEEE 754 double 로 해석되며(숫자 또는 10진 문자열) lat ∈ [32, 39.5], lng ∈ [124, 132] 이면
  lat·lng = 그 double 의 **최단 왕복 10진 문자열**(지수 표기 없음, 소수점이 없으면 `.0` 을 붙임 — Python `repr`, Dart `toString`, JS `String()` 후 보정), `source = "geocode"`;
  아니면 lat·lng null, `source = "none"`. 좌표를 반올림·격자화하지 않는다(공개 정책: 입력 좌표 그대로).

payload 키는 항상 모두 있다: `address, agency_name, amount{confirmed_won, kind, penalty_points}, category, completed_date, disposition, location{lat, lng, source}, manager_name, report_date, status, status_raw, vehicle_raw, violation_law, source_agency_code, rating`.
v1(12키)·v2(13키)·v3(14키, `rating` 없음) payload 도 서버가 받는다(구 앱 호환) — 키가 없는 관측의 해당 값은 null(명시적 null 과 구별은 journal 의 parser 버전으로 한다).
공식 로그인 정보·쿠키·헤더·사진·첨부·신고 본문·처리내용 원문은 넣지 않는다. `source_report_id` 는 envelope 의 event 필드(private)로만 간다.
`report_number` 역시 Observation 밖의 private event 필드다. 공백을 잘라 빈 값은 null로 보낸다. 중앙은 `^SPP-[0-9]{4,6}-[0-9]{6,8}$`만 받는다. 기존 이벤트의 필드 생략도 null로 해석한다. 번호만 뒤늦게 채워졌으면 payload_sha256이 같아도 새 이벤트를 발급한다. Observation 해시와 parser/contract 버전은 그대로이며 기존 전체 신고를 일괄 update하지 않는다.

## 4. event 결정 (앱의 capture 함수)
`prev` = 같은 로컬 데이터셋에서 같은 신고의 **가장 최근 journal 행**(rebuild 중이면 이번 run 의 staging 을 먼저 본다).
중앙 manifest(`server_completed`)는 prev 합성에 쓰지 않는다 — 2026-09-28 결정으로 답변 완료가 아닌 관측은 정정 이벤트를 발급하지 않으므로,
writer 전환·재설치 뒤 첫 비적격 관측도 이벤트 없음이 된다. `server_completed` 표 자체는 manifest 신선도 증명용으로 유지한다(local-store.md).
- PC·모바일 Standalone에서 별점 제출 뒤 사이트가 1..5점을 확인하면 로컬 capture 재조회 의도(`rating_confirmed_refetch`)를 남긴다. 다음 증분 수집은 이 신고의 공식 상세를 다시 읽는다. 확인 전 입력 점수나 자유 텍스트를 공유하지 않는다.
- eligible: 별점의 null→1..5 또는 점수 변경은 payload 해시 변경이다. prev 가 있고 payload_sha256 이 같으면 새 이벤트 없음(내용 변화 없음). 아니면 `completed_observation`.
- 단, 새로 얻은 non-null `report_number`가 최신 journal 번호와 다르면 같은 해시여도 `completed_observation`을 발급한다(레거시 백필). 로컬 이전 journal은 불변이다.
- not eligible(처리중·보완요청·취하·이송·other 전부): **이벤트 없음**. prev 가 eligible 이었어도 `status_correction` 을 발급하지 않는다.
  중앙은 마지막 답변 상태를 유지한다(드물게 답변이 비종결 상태로 돌아가도 중앙 fact 는 바뀌지 않는다).
- 이벤트가 없으면 `detail_status` 만 기록하고 report_latest/staging 은 쓰지 않는다(가리킬 journal 행이 없음). 이것은 capture 성공이다(S-06).
  처음 본 처리중·취하 신고도 마찬가지다.
- `location_supplement`: 기존 자료와의 호환을 위해 서버가 받는 이벤트 종류로 남긴다. 새 앱/서버는 사용하지 않는다. 재조회에서 공식 좌표가 새로 생기거나 바뀌면 변경된 payload의 `completed_observation`을 보낸다.
- `reshare`: 재동의·writer 전환 뒤 사용자가 지도 탭에서 **명시적으로** 요청할 때만. 신고별 최신 eligible journal 행의 payload·captured_at 을 그대로 두고 새 event_id·새 source_revision·현재 grant/connection/epoch 로 발급. 자동 실행 금지.
- 개인 편집·백업 복원·DB 변환·가져오기·모바일 Client 는 이벤트를 만들지 않는다.
- capture 가 실패하면(community.db 오류 등) **그 신고의 개인 저장도 하지 않는다**(저장 실패로 집계). 개인 상태가 전진하지 않으므로 다음 수집의 선정 규칙(신규·미종결·목록 상태 변경)이 그 신고를 다시 읽는다 — 공유 사본을 영구히 놓치지 않는다(S-03).
- 상세를 받을 때마다(이벤트 여부와 무관) `detail_status` 에 그 상세의 C_NOW 라벨(`progress_status`)을 같은 트랜잭션으로 기록한다(목록 상태 변경 감지용, S-12).
- `event_id` = 새 UUIDv4(소문자). `source_revision` = `community.db` **전체**에서 단조 증가하는 정수(`meta.next_revision` — 데이터셋 회전으로 초기화하지 않음, 서버 `last_accepted_revision` 보다 작아지지 않게 올림). 그래서 회전 전 대기 이벤트가 회전 뒤 새 관측보다 늦게 도착해도 새 관측을 되돌리지 못한다(S-20). `captured_at` = UTC ISO-8601 `Z`(밀리초).

## 5. 서버 검증·파생(ingest)
- 한 요청 안에서 같은 신고(`source_report_id`)의 이벤트는 하나만(S-11-B). 업로더는 같은 신고의 다음 이벤트를 앞 요청의 ACK 뒤에 보낸다.
- 같은 event_id 재전송 판정: 불변 필드(event_type, source_report_id, report_number, source_revision, writer_epoch, captured_at, payload_sha256, 연결의 dataset_key)가 모두 같으면 `duplicate`, 하나라도 다르면 `conflict`. grant·connection·trigger·request_id 는 전송 문맥이라 비교하지 않는다(재로그인 rebind·정책 재동의 뒤 재전송 허용).
스키마(`observation.schema.json`)가 형식·enum·길이·상한을 검사하고, 서버 코드가 아래 **값 규칙**을 추가로 검사한다(위반 = 422 `schema_invalid`, 쓰기 0):
- 날짜: 실제 달력 날짜(`2026-02-30` 거부). `completed_date` 는 payload 가 eligible 일 때만 값을 가질 수 있다(not eligible 인데 값이 있으면 거부).
- 좌표: `source="none"` ⇒ lat·lng 둘 다 null. `source="geocode"` ⇒ 둘 다 문자열, double 로 해석되고 lat ∈ [32, 39.5]·lng ∈ [124, 132], 그리고 **정규형**(해석한 double 의 최단 왕복 표기 + `.0` 규칙)과 문자열이 정확히 같아야 한다(`37.000`·`+37.5` 거부).
- 금액: `confirmed_won` ≤ 100,000,000, `penalty_points` ≤ 1000(스키마), kind=unknown 이면 confirmed_won 은 null.
- `status == map(status_raw)` 가 아니면 그 이벤트는 `quarantined:status_mapping_mismatch`(durable, fact 반영 안 함).
- event_type 일관성: `completed_observation`·`location_supplement`·`reshare` 는 eligible payload 만 받는다.
  payload 가 적격이 아니거나 event_type 이 `status_correction` 이면 그 이벤트는 `rejected:non_final_not_accepted`(durable=false, 재시도 불가)로
  개별 거절하고 배치의 나머지 이벤트는 정상 처리한다(요청 전체 422가 아님). `status_correction` 이름은 envelope 스키마에 남겨
  구버전 앱 배치가 전체 422 대신 이벤트별 거절을 받도록 인식만 유지한다(새 앱은 발급하지 않는다).
  `location_supplement` 는 `location.source="geocode"` 만. 적격 payload 가 이 조건을 어기면 422.
  적격이 아닌 payload 의 `location_supplement`(구버전 잔여 포함)는 위 개별 거절로 처리하고 배치 전체를 422로 만들지 않는다(Sol 2026-09-28).
  `reshare` 이벤트는 envelope `trigger="reshare"` 에서만 허용.
- 서버가 `source_report_key = sha256(utf8("safetyreport|" + source_report_id))` 를 계산한다(클라이언트 값 받지 않음). fact 키는 (contributor, 연결의 dataset_key, source_report_key).
- 계정별 기여와 전역 중복 제거(2026-09-28 사용자 규칙 — 소유 이전 대체): 같은 신고를 다른 카카오 계정이 올려도 먼저 올린 계정의 fact·연결을 지우거나 옮기지 않는다. 업로더의 fact만 만들거나 갱신하고 `accepted`(또는 변경 없음 `no_change`)로 수신한다. 기관명만 달라도 정상 수신이며, 신고번호가 다르거나 없어도 거절하지 않는다(구 `cross_account_mismatch`·`report_identity_mismatch`·`ambiguous_existing_owners`·`transferred`는 폐기 — errors.md).
  신고번호 격리(2026-09-28 REVIEW2 높음-1): `report_identity = source_report_key || '|' || coalesce(자기 번호, 키의 최초 번호, 'legacy')`.
  둘 다 번호가 있는데 다르면 별도 identity 로 격리해 각각 공개 집계한다(서로 다른 실제 신고). 번호가 없는 구버전 관측은 같은 키의 최초 번호 그룹에 붙고, 번호가 하나도 없는 키는 `legacy` 그룹 하나로 묶는다.
  키의 번호(최초 번호)는 동의 유효 행의 전체 이력에서 확정하므로 조회 기간과 무관하다(조회 창 안의 키만 번호를 매겨 창을 넓혀도 기존 키의 번호가 바뀌지 않음 — REVIEW3 중간-3).
  공개 projection(`internal_analytics_v2_facts`)은 identity 당 공개 목록 중 **서버가 가장 나중에 수신한 서로 다른 답변** 하나를
  대표(`is_representative`)로 내보낸다: 답변 그룹의 수신 시각(`max(answer_accepted_at)`, fact 의 새 답변 수신 때만 갱신, 잠금 획득 뒤 `clock_timestamp()` 기록 — `now()`는 트랜잭션 시작 시각이라 동시 제출 순서가 뒤집힐 수 있음, REVIEW4) DESC,
  답변일(`completed_date`) DESC NULLS LAST, `first_accepted_at`·`contributor_id` 순. 동일 내용의 단순 재전송(`no_change`·
  `stale_ignored`)과 내용 없는 재공유(grant 만 바뀜)는 수신 시각을 갱신하지 않아 대표를 뒤집지 않는다(REVIEW3 높음-1).
  범위 필터(category·region·agency·manager·bbox)는 확정된 대표에만 적용한다(REVIEW2 높음-2).
  전체 지도·기관·담당자 통계는 대표행만 센다(고유 1건). 각 행은 기여 수(`contribution_count`, 필터 전 identity 전체)와 대표의 `report_number`·`report_identity`를 함께 싣는다.
  개인 범위(my-analytics)는 목록의 모든 행을 그대로 받아 계정별로 자신의 기여를 센다(같은 계정의 두 dataset도 identity당 1건).
  실제 처리 결과가 계정마다 다르면 각 관측을 그대로 보존하고 대표는 최신 답변으로 정한다. 한 계정의 삭제/철회는 그 계정의 관계만 처리하고, 남은 유효 기여가 있으면 대표가 승계된다.
- 삭제 tombstone 은 (contributor, source_report_key) — dataset_key 와 무관 — 이고, 그 신고의 이벤트는 captured_at·dataset_key 와 무관하게 영구 `rejected:deleted`.
- grant 귀속: 기존 fact 의 grant 계보가 **사용자 철회로 비활성**이면, `reshare` 가 아닌 이벤트는 내용·순서만 갱신하고 fact 는 옛 (비공개) grant 에 남긴다 → 공개되지 않음(`projection_status=held`). `reshare` 이거나 계보가 활성(정책 갱신 재동의 포함)이면 현재 grant 로 귀속(S-02).
- ACK `projection_status`(실제 공개 조건 기준 — 공개 RPC 와 같은 함수 `community_fact_publicly_listed`: completed ∧ 신고일·처리완료일 중 하나 이상 ∧ contributor active ∧ 계보 활성):
  `published` = 커밋 뒤 익명 API 가 이 fact 를 보여 줌(ready ∧ generated_at 존재) · `removed` = 보이던 fact 가 이번 변경으로 안 보이게 됨 ·
  `held` = 보일 조건이지만 공개 스위치 꺼짐(ready=false) 또는 계보 비활성 · `not_public` = 저장만 되고 목록에 안 나옴(미완료, 날짜 둘 다 없음) · `not_applicable` = fact 변경 없음.
- 삭제 보장 범위(S-02-F): 서버가 보장하는 것 — ① 삭제 시점의 모든 writer 연결 폐기(그 연결의 대기 이벤트는 captured_at 과 무관하게 거절), ② 이미 공유된 신고 identity 의 영구 tombstone, ③ 삭제 시각 이전으로 **주장된** captured_at 의 이벤트 거절. captured_at 은 클라이언트 시각이므로 ③은 정상 앱을 위한 보호다. 정상 앱은 삭제 성공 뒤 삭제 이전 journal 을 새 연결로 승계·재발급(reshare 포함)하지 않는다(계약 local-store). 조작된 클라이언트가 미래 시각으로 자기 자료를 다시 올리는 것은 막지 못한다(사용자 자신의 자료, 보안 경계 밖).
- 파생: `violation_law` 는 그대로 저장(공개 필터·법규별 통계용). `public_state` = eligible ? `completed` : `not_completed`; lat/lng = 문자열을 double 로(원 문자열도 `lat_text`/`lng_text` 로 보존); `point_key = "v1:" + lat + "," + lng`(문자열 그대로);
  `region_code` = 주소 앞 두 토큰(공백 분리, 시·도 약칭 정규화) — 공개 필터용 표시 키, 행정코드가 아님. 폐지 행정구역명은 현행 귀속표로 푼다(예: `충북 청원군` → 현행 청주시 — 공유 registry 2014_cheongju 단일 후계, regions-20260701.json `renamed`);
  `agency_key` = 확인된 1:1 승계(공유 registry as_of 기준, 원문 기관코드로 해석)면 `"inst:" + institution_id`, 아니면 `"a1:" + sha256(NFC(agency_name))[:24]`;
  승계 전·후 어느 코드로 들어와도(어떤 순서로 수신돼도) 같은 현행 기관 키로 묶인다 — resolver 가 후계 코드를 역방향으로
  따라가 같은 institution 으로 해석하고(합류 대상 등 모호하면 미해결 유지), 신규 수신 행·기존 행 백필에 동일하게 적용된다(REVIEW3 높음-2);
  `agency_current_name` = 승계면 현행명, 아니면 원문 기관명(원문 `agency_name` 은 그대로 저장 — 원문·현행 분리);
  `manager_key = "m1:" + sha256(agency_key + "|" + NFC(manager_name))[:24]`(agency_key 기준이므로 같은 기관의 같은 담당자는 개명 전후 한 키로 묶임).
  키 없는 구버전 갱신(v1/v2, payload에 `source_agency_code` 키 없음)은 기관명이 기존 fact와 같으면 저장된 기관코드뿐 아니라 기관 키·현행명·담당자 키도 보존한다(코드 없는 derived의 기관명 해시로 덮으면 승계 기관 통계가 갈라짐 — REVIEW4). 기관명이 다르면 코드 NULL·새 derived 키(새 기관 답변에 옛 코드·옛 키 부착 금지). 키가 있으면(명시적 null 포함) 그대로 쓴다.
