# app-announcements (2026-10-22)

운영자가 206 Supabase 에 올린 공지를 앱(PC 관리자 화면·모바일)에 팝업으로 보인다. 정본 규칙: 지도 레포
`docs/implementation/backfill-resume-20261022/CLIENT-RULES.md` 2-11, 작업지시서 `docs/tasks/backfill-resume-20261022.md` 2-11.

## 읽기 (앱)
`GET {COMMUNITY_SUPABASE_URL}/functions/v1/app-announcements?app=<pc|mobile>&mode=<..>&platform=<..>&version=<앱 버전>`
헤더 `apikey: <publishable key>` 만. **카카오 로그인·사용자 토큰이 필요 없다**(로그인 전 휴대폰, PC 관리자 화면).

- 쿼리 4개가 정확히 한 번씩: `app` = `pc`·`mobile`, `mode` = PC `server` / 모바일 `standalone`·`client`·`demo`,
  `platform` = `windows`·`linux`·`macos`·`docker`·`android`·`ios`·`other`, `version` = 앱 버전 문자열(40자 이하 ASCII).
  그 밖에는 400 `invalid_request`.
- 200: `response.schema.json`. 지금 유효하고 대상이 맞는 공지를 우선순위(큰 값 먼저) 순서로 **최대 3개**. `Cache-Control: public, max-age=300`.
- 버전을 해석하지 못하면(`version-vectors.json` 규칙, 지도 `contracts/community-ingest-v2/`) 버전 조건이 있는 공지는 빼고 조건 없는 공지만 준다.
- 503 `service_unavailable`(+`Retry-After`)·네트워크 오류·형식 이상: 앱은 아무것도 띄우지 않고 다음 때(6시간 뒤)를 기다린다. 재시도 반복 금지.
- 개인정보가 오가지 않는다. 지도 웹은 이 API 를 쓰지 않는다.

## 쓰기 (운영 콘솔)
문서 하나 = `announcement.schema.json`. 콘솔은 DB 함수 `ops.announcement_save`(초안만 저장·수정) → `ops.announcement_publish` →
`ops.announcement_end` 를 부른다(`docs/implementation/backfill-resume-20261022/OPS-DB-API.md`). 게시한 공지는 바꿀 수 없다 —
내용을 바꾸면 **새 id** 로 올린다(앱의 "보지 않기"가 id 별이라 같은 id 로 바꾸면 이미 숨긴 사람은 바뀐 내용을 못 본다).
- 대상 `audience` 에 `min_version`·`max_version` 이 하나라도 있으면 `apps` 는 **정확히 하나**다(PC 3.6.x 와 모바일 2.5.x 는 번호가 달라 같은 조건을 둘에 걸 수 없다).
  DB(`invalid_announcement`)·콘솔이 같은 규칙으로 거부한다. 두 앱에 버전 조건을 걸려면 공지를 앱마다 따로 만든다(2026-10-22.2, D10).

## 화면 규칙 (앱)
- 본문은 평문으로만 그린다(HTML·마크다운 해석 금지, 줄바꿈만). 제목 40자, 본문 600자.
- 버튼: `primary_action`(있으면), "일주일 동안 보지 않기"(`snooze_days` 7) 또는 "N일 동안 보지 않기"(0 이면 버튼 없음), "닫기".
- `primary_action.url` 은 `vectors/links.json` 허용 목록만 연다. 목록 밖이면 버튼을 숨긴다(서버도 저장·응답 때 거른다).
  벡터에는 경계 경우(쿼리·조각 `#`·`'`·`(`·`;`·`,`·`|`·한글 경로·대문자 호스트·하이픈 호스트·500자)가 있다. 기준은 서버 DB 정규식이며 PC·모바일·콘솔이 같은 벡터를 돌린다.
- `primary_action.app_action` 은 `open_mode_switch`(모바일 설정의 모드 변경 흐름)만. 모르는 값이면 버튼을 숨긴다.

## 구버전
PC 3.6.1·모바일 2.5.0 이하에는 이 기능이 없어 공지를 볼 수 없다.

파일을 바꾼 뒤: `python3 scripts/check_contract_manifests.py --write contracts/app-announcements`.
