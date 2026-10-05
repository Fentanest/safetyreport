# 2026-10-05 PC·Android 통합 사용 안내 글 — 작업·검수 기록

공개 글: https://hb.worklazy.net/mysafetyreport-pc-android-guide/ (Apps, hbWorklazy 커밋 55ee977, GitHub Actions run 37307432569 success).

## 역할 (이번 작업 한정 예외)
사용자 지시서에 따라 이번 작업의 역할·집필·커밋·배포 순서는 기존 역할 규정보다 우선했다. 공통 규칙으로 확대하지 않는다.
- Opus: 두 dev(PC 1525469, 앱 c61db24f) 기능 조사(129개), 실DB 사본 촬영(PC fixture 웹, Android 테스트 에뮬레이터), 인계 자료, README, 앱 링크 변경·커밋.
- 아스트라(codex `gpt-6-astra`): 원고 집필·사진 배치·alt·Gemini 호출·반영 판단·`tools/check.sh`·커밋·push·배포·공개 확인.
- Gemini(`agy`, `gemini-3.1-pro-high`): 업로드 전 원고의 표현 지적만(종료 0, 지적 4건 — 반영 2·부분 1·미반영 1, 판단은 아스트라).
- 블로그 저장소의 새 번들 글 배포를 막던 기존 도구 결함 5개 파일(레이아웃 2, 검사 스크립트 3) 수정을 Opus 가 범위 확대 승인(CI 워크플로는 변경 없음).

## 사고·주의
- `codex exec resume` 이 기본 모델(`gpt-6.1-sol`)로 이어 실행돼 즉시 중단했다(읽기 명령 3개만, 파일 변경 없음). `-m gpt-6-astra` 로 다시 이어 실행. 재개할 때도 모델을 명시해야 한다.
- hbWorklazy 본 저장소 로컬 main 의 사용자 미푸시 커밋 d1a63a5 는 건드리지 않았다(origin/main 기반 worktree 에서 `push origin HEAD:main`). 로컬 main 은 이제 origin/main 과 갈라져 있다.
- 촬영 데이터: 사용자 PC dev DB 사본에서 차량번호·담당자 이름·전화번호를 일관된 가짜값으로 치환. 원본 DB 는 읽기 전용으로만 사용. 공개 사진에서 API 키·Standalone 계정 아이디·휴대폰번호는 가림. 실제 첨부 사진이 보이는 캡처 1장은 제외.
- Standalone 로그인은 사용자가 직접 했고, 동기화·초기화 크롤링은 실행하지 않았다. 촬영 앱은 끝난 뒤 삭제했다.

## 발견한 결함·불일치(제품 수정 안 함)
1. 앱 DB 백업 안내 문구(`Documents/mysafetyreport/`)와 실제 저장 위치(Android 10+ Download/mysafetyreport/) 불일치.
2. PC 목록 `엑셀 다운로드`가 CSV 를 만든다.
3. PC 크롤링 화면 로그 링크 `/file-browser?target=logs` 가 결과 탭을 연다.
4. Client 앱의 API 키에 'PC 모바일 앱 커뮤니티 계정 관리 권한'이 없으면 앱이 서버 초기화 상태를 못 읽어 "서버 오류: HTTP 403"과 초기화 크롤링 화면을 띄운다.
5. 신고 지도 '지도 주소 그룹'은 (위도, 경도, 주소) 묶음 수라 서로 다른 주소 수보다 크다.
6. 소프트웨어 렌더링 에뮬레이터가 통계 화면에서 두 번 종료(앱/에뮬레이터 원인 미확정).

## 증거 위치(로컬, 추적 안 함)
`.agent-runs/blog-guide-20261005/` — handoff/(HANDOFF·feature-inventory.csv·article-outline·image-manifest.csv·verified-facts·verification), shots-raw/, public/, astra/(RESULT·gemini/·check.log·preview/·live/).
