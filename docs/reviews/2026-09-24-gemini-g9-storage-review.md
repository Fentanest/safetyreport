# Gemini G9 검수 기록 — 저장 계층 재설계 계획

작업서: `safetyreport-gemini-g9/.agent-runs/g9/TASK.md`(role: planning, 코드 수정 금지). 실행: agy 1.2.9, `gemini-3.1-pro-high`, 대화 `b5bbb9db-…`.
worktree: 서버 `safetyreport-gemini-g9`(5b519c4), 모바일 `safetyreport-mobile-gemini-g9`(bf811235), 둘 다 detached.

## 1회차 (168초, status SUCCESS)
- 산출물 4개 존재. 실행 명령은 grep 6회, 재현 0건. 결함 7건 중 대부분이 작업서의 "Opus 가 이미 아는 잠재 결함" 재진술.
- 인용 오류: `db_backup.py:461` 은 NULL→'' 가 아니라 merge DELETE, 함수명 `restore_backup` 없음, `web/routers/auth.py`·`settings.py` 없음(`*_route.py`).
- 누락: 감시목록·별점·중복군 사용자 저장·DB 편집기·upgrade_schema/reset·crawl_state_store·모바일 sync_engine 등.
- **Opus 판정 오류:** "D3 트랜잭션 안 네트워크 호출"을 Opus 가 `prepare_geo_payload` 앞부분만 보고 거짓으로 판정해 2회차 지시서에 "거짓"이라 적었다.
  실제로는 캐시 미스 시 `resolve_address` 가 카카오 HTTP 를 부른다(`services/geocode_service.py:553 → 478`). 이 지적은 맞았다. 계획서 §0 과 S-7 에 반영.

## 2회차 (730초, status SUCCESS)
- 새 결함 5건, 재현 스크립트 2건(`repro/test_poll_downgrade.py`, `test_sync_meta_null.py`).
- Opus 재현: D2(목록 저장이 '참여 완료'→'참여 가능' 되돌림) **재현됨** — 임시 DB 에서 'Initial 참여 완료 → After 참여 가능'. 계획서 S-26/G-2.
- D1(sync_meta NULL→'') 코드 확인. D5(crawl_state 읽기-삭제 경합) 코드상 타당(S-18 에 포함).
- D3(편집기가 별점 변경에 synced_at 미갱신): synced_at 은 상세 변경 시각이라 설계상 정상 — 결함 아님.
- D4(백업 내보내기 WAL 손실) **반증**: 파이썬 sqlite3 로 WAL 모드 DB 를 닫으면 `-wal` 이 사라지고 본 파일 복사본에 3행 모두 있음. 모바일 `closeDb()` 는 단일 연결을 닫는다. 실제 위험은 다른 작업이 연결을 쓰는 중에 닫는 동시성(M-25).
- 계획서는 결함 수정 목록 수준에 머물러 "넓고 깊게" 요구(열 주인, 계약, 트랜잭션 경계, 사용자 소유 데이터)를 다루지 못했다.
- **규칙 위반:** 모바일 worktree 에서 `test/tool/` 디렉터리를 통째로 지워 추적 파일 `test/tool/db_roundtrip_harness_test.dart` 가 삭제됐다(작업서는 새 임시 파일만 삭제 허용). Opus 가 `git checkout` 으로 복구. 사후 검사에서 다른 worktree 변경은 없음(모바일 메인 체크아웃의 변경은 다른 세션의 동영상 작업).

## Opus 독립 조사와의 비교
- Opus 측 위임 조사: 서버 결함 35건, 모바일 30건. 표본 12건을 Opus 가 코드 줄로 확인(계획서 §0).
- Gemini 고유 기여: S-26(재현), G-1, S-7 최초 지적. 나머지 Gemini 결함은 Opus 측 목록에 포함됨.

## 3회차
통합 계획 초안 교차 검토(role: review) — 결과는 아래에 추가한다.
