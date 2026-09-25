# G16 구현 교차 검수 (2026-09-25)

계획: 초안 v1 → Gemini·6Sol(Codex `gpt-6-sol`) 라운드 1 → v2 → 라운드 2(Gemini "반대 없음", 6Sol 5건 채택) → v2.1. 작업: W1 별점 대상 규칙 통일, W2 별점 공통 사유, W3 비회원 제거, W4 레거시 크롤링 제거, W5 다크 B안.

## W1+W2 — 6Sol 검수 (판정: 반려 → 수정 후 재확인)

6Sol 은 읽기 전용 샌드박스라 임시 폴더가 필요한 테스트 4건과 flutter test 는 돌리지 못했다(환경 제약, 결함 아님). 지적 4건은 Opus 가 코드로 확인했고 모두 타당:

| # | 지적 | 확인 | 조치 |
|---|---|---|---|
| 1 높음 | 모바일 제출 POST 가 내부에서 5회 재시도 → 응답만 끊겨도 중복 제출. 양쪽 모두 제출 뒤 점수가 늦게 보이면 다음 시도에서 다시 제출 | `standalone_api_service.dart` `_postPublicFormWithRetry`, 두 레포 재시도 루프 | 제출 POST 1회만, 이미 제출했으면 재시도에서 확인만(양쪽). 테스트: 끝까지 미확인 시 POST 1회 |
| 2 중간 | 조회 `result` 가 비면 서버는 "대상 없음" 실패, 모바일은 미참여로 보고 제출 | `star_rating_service._site_result`, `fetchSatisfaction` | 모바일도 대상 없음이면 실패(`exists` 필드). 테스트 추가 |
| 3 중간 | 서버는 DB 저장 실패를 삼키고 성공으로 집계 | `save_site_values` | 저장 실패는 예외 → 재시도 → 끝내 실패. 저장 뒤에 성공 로그 |
| 4 낮음 | 팝업 사유 HTML 엔터티 해독 범위·순서 차이 | 서버 `html.unescape` 후 태그 제거, 모바일 일부 치환 후 태그 제거 | 모바일을 서버 순서·숫자 엔터티까지. 공용 벡터 `popup_cases` 8건 |

확인됨(문제 없음): 새 `경고: [SPP-…]` 로그 줄은 모바일 Client 정규식(성공·스킵·실패)에 걸리지 않는다. 구앱↔신서버·신앱↔구서버 경로 정상. 공용 벡터 두 레포 sha 동일.

## W3+W4 — Gemini 검수 (판정: 통과)

- Gemini 주장: 계획 v2.1 의 W3·W4 항목 모두 구현, 결함 없음, 서버 unittest 132 OK, 모바일 analyze error 0. 남길 것(410 재개 API, `/crawl/config` 호환 필드, `from_legacy_tuple`, API 브라우저 비상 경로의 Selenium)과 지울 것 구분이 맞다.
- Opus 표본 확인: 삭제 모듈 import 0건, 웹 DOM id(`#crawlMin`·`#maxEmptyPages`·`#btnResume`·hidden `crawl_type`) 0건, `bot.py` min 메뉴 제거 확인, Playwright 전체 189 통과.
- Gemini 누락 1건: 레거시 HTML 파서를 지운 뒤 `beautifulsoup4` 가 어디에서도 import 되지 않는데 `requirements.txt` 에 남아 있었다 → 제거.
- 사후 검사: 두 Gemini worktree 에 추적 파일 변경 없음, `node_modules`·`.venv` 링크 정상(지난 G15 에서 Gemini 가 dev 의 node_modules 를 자기 worktree 로 옮겨 Opus 가 복구한 일이 있어 이번 작업서에 금지 조항을 넣었다).
