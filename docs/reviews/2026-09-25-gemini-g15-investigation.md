# Gemini G15 조사 4건 — Opus 팩트체크 (2026-09-25)

- 실행: agy 1.2.9 `gemini-3.1-pro-high`, worktree `safetyreport-gemini-g15`(dev 362238d), `safetyreport-mobile-gemini-g15`(mobile dev 496158d). 4건 동시, 모두 `status: SUCCESS`, stderr 비어 있음.
- 사후 검사: 다른 worktree·운영 checkout 변경 없음, 모바일 worktree `git status` 깨끗, fixture(18629) 종료됨.
  위반 1건: G15d 가 `tools/web-tests/capture.js` 를 지우지 않고 남김(추적 파일 아님, Gemini worktree 안) → Opus 가 삭제.

## G15a 별점 사유 — 대체로 정확
- 확인됨: 제출 `POST …/satisfactionstatistics` 본문 `STTEMNT_NO, C_PHONE2, STSFDG_SCORE, STSFDG_CAUSE=""`(`services/star_rating_service.py:88-94`), 성공 시 `sync_rating_status(..., cause="")`(:100),
  사유 조회 필드도 `STSFDG_CAUSE`(`services/satisfaction_fetcher.py:84`, 팝업 HTML `id="STSFDG_CAUSE"` **textarea** :42), 모바일 제출 `STSFDG_CAUSE: ''`(`standalone_api_service.dart:326`), Client 본문 `{report_numbers, score}`(`api_service.dart:789`), 점수는 일괄 1개(`rating_route.py:24`, `api_route.py:312`).
- Opus 보강: 팝업의 사유 칸이 textarea 라는 점이 "사이트가 사유 텍스트를 받는다"는 가장 강한 코드 근거. 필수 점수 구간은 코드 근거 없음(사이트 확인 필요) — 동의.
- 추가 발견(서버·모바일 차이): 모바일은 제출 직전 `처리상태` 취하·답변 대기·처리중을 막지만(`rating_service.dart:13,85`),
  서버 `run_batch_rating` 은 `만족도조사여부` 만 본다(`star_rating_service.py:20`). 서버는 대상 목록(`get_unrated_records`, `report_query_service.py:315`)에서만 거른다 → ID 를 직접 넘기면 처리중도 제출 시도.

## G15b 비회원 로그인 — 정확
- 확인됨: `resume.sig` 는 비회원 대기 전용(`start.py:356-364,412`, `crawl_control.py:221`), 라우트 `/crawl/resume`(`crawl.py:61`)·`/api/v1/crawl/resume`(`api_route.py:491`),
  UI `crawl.html:27-34,143-146`, 모바일 Client `crawl_screen.dart:418-422,793-794,888-900`, `api_service.dart:696-704`(200 아니면 throw), Standalone 에는 없음. 테스트·spec 참조 0.
- 누락: `core/utils/scheduler.py:18` 가 `login_mode="member"` 를 넘김(제거 시 인자 정리 대상).
- 호환 제안(구 앱용 `/api/v1/crawl/resume` 는 200 + 안내 메시지로 남김) 타당.

## G15c 레거시 크롤링 — 판정은 맞고 분류는 일부 틀림
- 판정 "미착수" 확인: `start.py:9` 가 `crawltitle`/`crawldetail` 을 import 하고 :155-252 에서 호출, 설정·UI 에 legacy 선택지 유지, 제거 커밋 없음(`0baa5b4` 는 값 이름 변경).
- 틀린 분류(지울 대상 A 로 넣었지만 API 경로도 씀):
  - `core/crawler/detail_pipeline.py` — `crawldetail_api.py:15` 가 import(공용).
  - `core/crawler/driv.py`, `login.py` — API fallback 이 사용(`start.py:424-426`).
  - `satisfaction_fetcher.fetch_score_via_selenium_page` — API fallback 만족도 조회(`satisfaction_fetcher.py:124`)도 사용.
- 레거시 전용으로 확인된 것: `crawltitle.py`, `crawldetail.py`, `parser.parse_details`(HTML, :258)와 그 안의 `supplement_parser` 호출(:280-283).
- 결정 필요: API fallback(Selenium)을 남길지. 남기면 Selenium·Chromium 의존성(requirements, Dockerfile, build_exe)은 유지.

## G15d 다크모드 — 수치는 맞고 캡처는 웹만 쓸 만함
- 수치 재계산 일치: 웹 `--sr-bg #060e22` H 223° S 70% L 8%, 모바일 `#0b1220` S 49%. 대비 `#e5edfb`/`#3b82f6` 3.12, 흰색/`#3b82f6` 3.68, 모바일 brand/bg 4.16.
- 웹/모바일 불일치 확인: 배경(`#060e22` vs `#0B1220`), 표면(`#0c1a36` vs `#111827`), 브랜드(`#3b82f6` vs `#0D6EFD`).
- 웹 캡처 15장(현재/A/B) 서로 다름. 대시보드 A 캡처만 비율 막대가 끝까지 찬 것은 막대 애니메이션 도중 캡처한 타이밍 차이로 보임(토큰은 색만 바꿈) — 적용 시 재캡처 필요.
- 모바일 캡처 3장은 폰트가 네모, 실제 화면이 아닌 임의 카드 — 예시로 쓰지 않음.
- 하드코딩 색 지적은 부정확: `background:#000`(`base.html:756,771`, `data_table.html:891,900`)은 사진·동영상 뒷배경이라 문제없음. 실제로 토큰을 안 타는 곳은 `report_map.html`(hex 38곳)이 크다 — Gemini 누락.
- 권고 B안은 근거가 약함(“배터리·눈 피로”는 일반론). 선택은 사용자 몫.
