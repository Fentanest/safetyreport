# Agency/region resolver — algorithm (3 ports, one spec)

`resolve.py`가 정본이다. `resolve.dart`·`resolve.ts`는 같은 입력에서 같은 값을
반환해야 하며 `vectors/resolve_cases.json` 31건으로 확인한다.

## 입력·출력

- `resolve_agency(code, name, answered_at, snap)`:
  `code`는 선택 답변의 `C_MANAGE_ORG` 원문(없으면 null). `name`은 같은 답변의
  원문 기관명. `answered_at`은 답변일(`YYYY-MM-DD`, 없으면 현재로 해석).
  `snap`은 index/legacy/links/institutions/version/as_of 묶음이다.
- `resolve_current_agency(code, name, snap)`: `as_of_date`를 답변일로 쓴 현행 표시용.
- `resolve_region_gap(code, date)`: 지역코드(10자리 문자열, 앞자리 0 유지)와
  기준일. `resolve_region`에 재설치 공백기(`gap`) 판정을 덧씌운다.
- `display_agency` / `display_region`: 화면 표시문. `(구)`는 알려진 역사
  노드(`historical`)에만 붙고, 미확정은 원문을 그대로 둔다.

## 규칙

1. 기관 코드는 index에서 집계기관(agg)으로 접은 뒤 검증된 1:1 링크 연쇄만
   따른다. `from` 하나에 후속이 0개 또는 2개 이상이면 거기서 멈춘다.
   순환은 방지한다. 대표기관코드를 통계 키로 쓰지 않는다.
2. 기관 `institution_id`는 연쇄 전체에 안정적이다(`agency_institutions.json`).
   `current_*` 표시만 `answered_at` 이전에 시행된 링크까지 적용한다.
   시행 전이면 당시 값(원문 이름·원문 코드)을 그대로 둔다.
3. 폐지 코드는 legacy `forward`(최종 현존 경계)로 귀결하고, 후속이 둘 이상이면
   `historical`((구) 표시, 별도 src 행)으로 보존한다. 후속 없음·순환·공란은
   `unresolved`(원문 유지)이다.
4. 코드가 없으면 경계 코드의 표시명(`name`)과 공식 전체기관명(`lookup_name`)
   양쪽을 정확 일치로 찾고, 해당 시점의 유일한 코드 후보만 파생한다
   (`code_derived: true`). 동일 코드는 한 번만 센다. 둘 이상·없음이면 `unresolved`이다.
5. 지역은 `old_code` 일치 사건만 본다. `effective_date` 이전이면
   `current_then`(당시 현행), 이후면 relation별 판정:
   `rename`/`rename_under_merge`/`transfer`/`merge` → `resolved`,
   `split`/`merge_parent` → `historical`((구) 표시, 후속에 임의 배분 금지),
   `reestablished` → `resolved`(단 기관 연속성과 분리) — 단
   `old_closed ≤ date < effective_date` 구간은 `gap`(당시 구 없음, 소급 배분 금지).
6. 날짜 비교는 `YYYY-MM-DD` 문자열 순서로 한다(세 언어 동일).
7. 코드 정규화·이름 해시로 기관을 잇지 않는다. `previous_code` NULL은
   성공이 아니라 미확정이다.

## 버전

출력마다 `registry_version`(manifest)을 싣는다. 스냅샷이 바뀌면 세 리더를
함께 갱신하고 벡터를 먼저 통과시킨다(서버·앱 한쪽만 올리지 않는다).
