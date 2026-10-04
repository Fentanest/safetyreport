# 2026-10-04 기술일지 결함 수리 기록 (서버·auth·모바일)

근거: 2026-10-04 정밀 점검 기술일지(claude.ai artifact "안전신문고 정비 기술일지", 결함 77건 + SB 권고 3건 + EO 개조 18건).
사용자 지시(2026-10-04): "이번에 너가 찾아낸 모든 결함들 역시 Opus가 수리한다. Sol과 Muse에게 위임하지 말고 Opus가 직접 A부터 Z까지".
범위: 결함 77건과 SB 권고 3건. **EO(리팩터링 개조) 18건은 결함이 아니라 이번 범위에서 제외**했다.

## 결과 요약

| 레포 | 브랜치/커밋 | 고친 항목 |
|---|---|---|
| safetyreport(서버) | `fix/techlog-2026-10-04` → dev | A1-01~10, A2-01~09, B-02~10, C01~C14, O-01~O-08, D2-01·02·04·07(서버)·09, F-01(서버)·02·03·05·06·07·09 |
| safetyreport-community-auth | `fix/techlog-2026-10-04` → main | D1-01~11, D2-07(중앙), F-01(문구)·F-04 + migration `202610050100_relay_hardening.sql` |
| safetyreport-mobile | `fix/techlog-2026-10-04` → dev | A2-01(모바일 경로)·A2-06·A2-08·D2-03·D2-05·D2-06·D2-08·O-01(같은 규칙) |
| safetyreport-community-map | `chore/auth-relay-hardening-manifest`(로컬 커밋만) | 공유 migration manifest 에 auth `202610050100` 등록 — push 는 사용자 결정 대기 |

서버·모바일 같은 규칙으로 맞춘 항목: 토큰 계정 귀속(A2-01), 만족도 응답 분류(A2-06, 같은 입력 벡터 시험), Sunwi result(A2-08),
requireFresh 재검증(D2-03), 대시보드 총 신고 취하 표기(O-01). 중복군 메모 수정(A1-05)은 모바일이 이미 옳아 서버를 모바일에 맞췄다.
대표 fingerprint 동률(B-10)도 모바일 `ORDER BY frequency DESC, first_ordinal` 과 같게 했다.

## 판정 메모
- A1-03 은 PROJECT_RULES 3-1 로 B→A 상향한 항목이다. 계약 `storage-contract.json` 의 category 값(traffic|parking|other) 밖이면 NULL·빈 값까지 거절한다.
- O-01: API `*_pct` 필드 의미는 모바일 계약이라 바꾸지 않았다. 웹 막대만 모바일 원 그래프와 같은 분모(그린 구간의 합)로 바꾸고 빠진 건수를 밝혔다.
- D2-01: 게이트 캐시에 받은 계정(user_id)과 무효화 세대를 둬, 로그인 확정 직후(complete 재시도 중)와 늦은 이전 계정 응답 모두 막는다.
- D1-01: 중계 기록은 하루 뒤 지워지는 임시 자료라 `ON DELETE CASCADE` 를 택했다(완료 기록 보존 가치 없음).
- F-04: 서버 기본 기기명에 호스트명을 넣는 안은 개인 이름이 중앙으로 갈 수 있어 택하지 않았다. 중앙 페이지에서 같은 글자를 두 번 쓰지 않게 했다.
- C02·F-09: 일반 크롤 시작 확인은 기존 브라우저 시험이 기본 confirm 창에 의존해 그대로 두고, 되돌릴 수 없는 조작만 공통 확인 창으로 바꿨다.

## 검증(실행 증거, `.agent-runs/techlog-fix/`)

| 대상 | 결과 | 비고 |
|---|---|---|
| 서버 unittest 기준선 | 675 run / OK(skipped 5) | `server-baseline.log` |
| 서버 unittest 수정 후 | 698 run / OK(skipped 5) | `server-run4.log`. 신규 `tests/test_techlog_2026_10_04.py` 23건. 수정 전 코드(1f43fe6)에 같은 파일을 돌리면 19건 실패, 통과 4건은 결과 동일성 시험(A2-05 저장 함수, B-05, B-08, B-09). 기존 시험 2건은 바뀐 계약에 맞춰 준비값·추출식만 보완(D2-02 단언 추가, F-06 라벨 속성 허용) |
| auth vitest | 50 passed / 35 skipped(로컬 스택 필요분) | 기준선 39 passed. 신규 11건 중 흐름 3건은 수정 전 `flow.ts` 에서 실패 확인 |
| auth migration | 로컬 통합 스택 DB 에서 트랜잭션 적용·검사 후 ROLLBACK | FK `c`, 생성 함수 잠금, 만료 코드 → `failed/code_expired`, 완료 기록이 있어도 사용자 삭제 가능 |
| map manifest | `compose_supabase check` ok(42 migrations, 7 functions) | map 로컬 커밋 |
| 모바일 flutter test | 1188 passed / 16 skipped | 기준선 1170 + 신규 18. analyze error 0 / 기존 warning 9 |
| 서버↔모바일 DB 왕복 | 양방향 컬럼 차이 0 | `roundtrip.log`(실제 Dart importer·서버 restore) |
| 브라우저 month-fixes | 28 passed(Chromium·Firefox) | `browser-monthfixes.log` |
| 브라우저 회귀(completion-regression, Chromium·Firefox·WebKit) | 384건 중 382 passed, 2건 환경 실패 → 재실행 2 passed | `browser-regression.log`, `browser-regression-rerun.log`. 실패 2건은 새 worktree 에 `.agent-runs/v3-user-reports/browser-fixture/{legacy,future}.db` 가 없어 난 것(제품 결함 아님) |
| 브라우저 신규 techlog-ui | 10 passed(Chromium·Firefox) | `browser-techlog.log`: C01·C02·C03·C07·C08·O-01·O-04·O-06·B-03·F-05 |
| 실제 화면 확인 | 대시보드·크롤·파일·지도 모달·목록·설정, 1440 라이트·390 다크, 콘솔 오류 0·가로 넘침 0 | `visual/`, DB 초기화 확인 모달 키보드 조작 확인 |
| 카카오 연결·동의 흐름 | 2026-10-04 실측(passed) 이후 서버 쪽 변경은 회귀 시험으로만 확인 | 실제 카카오·운영 중앙은 not-run |

## 남은 경계
- 실제 Windows/macOS/ARM, 물리 기기, 실제 카카오·운영 Supabase 적용은 not-run. auth migration 운영 적용과 Edge 함수 재배포는 배포 단계(사용자 승인)에서 한다.
- community-map manifest 커밋은 push 하지 않았다(이번 지시 범위 밖).
- EO 개조 18건은 별도 결정 대기.
