# Gemini G11 검수 기록 — 저장 계층 재설계 R1~R6 교차 검토

작업서: `safetyreport-gemini-g11/.agent-runs/g11/TASK.md`(role: review, 코드 수정 금지). 실행: agy 1.2.9 `gemini-3.1-pro-high`, 601초, status SUCCESS.
worktree: 서버 `safetyreport-gemini-g11`(845fc28, 범위 `ee8f058..`), 모바일 `safetyreport-mobile-gemini-g11`(d6d70070, 범위 `b142789c..`), 둘 다 detached.

## 사후 검사
- 두 worktree 추적 파일 변경 0. 새 파일은 서버 `.agent-runs/g11/`, 모바일 `test/g11_repro/` 안뿐(허용 범위). 메인·dev·모바일 체크아웃 변경 0.
- 재현 스크립트 2개 모두 **실제로 돌리지 않은 것으로 보임**:
  - 서버 `repro/test_exchange_decision_orphan.py` 는 실행하면 픽스처의 `member_count` 누락으로 `IntegrityError`(주장과 무관한 오류).
  - 모바일 `test/g11_repro/test_pending_race.dart` 는 목(mock) 설정에선 경합이 생기지 않아 실제 값이 `111,222,333`(보존)인데 `111,333`(유실)을 기대해 실패 — 주장의 반대를 보여 줌.

## 결함 판정 (5건)
| # | 주장 | Opus 판정 | 근거 | 조치 |
|---|---|---|---|---|
| 1 | 앱→서버 교환에서 중복 판단의 group_id 를 서버 기준 id 로 안 바꿔 판단이 고아가 됨 | **맞음** | 그룹·멤버만 `_canonical_group_ids` 로 바꾸고 판단은 원래 id. 앱은 새 그룹에 서버와 같은 sha256 을 쓰지만, 업그레이드 전 옛(FNV) id 그룹은 R3 이후 재생성하지 않아 남고 그 그룹의 판단은 옛 id 로 기록된다. Opus 테스트가 수정 전 코드에서 실패 | 판단 id 도 같은 규칙으로 변환, 같은 id 로 모이면 최근 판단 유지. `tests/test_storage_exchange.py::test_mobile_decisions_follow_the_canonical_group_id` |
| 2 | 앱의 빈 판단 표가 서버 판단을 지우지 못함(`or None`) | **맞음** | R1 때 "앱이 판단을 기록하기 전(R3 전)" 임시 예외로 둔 것. R3 에서 앱이 판단을 기록하므로 빈 표도 원천이어야 한다(표가 없는 구앱은 여전히 서버 것 유지). Opus 테스트가 수정 전 실패 | 예외 제거. `test_empty_decision_table_from_the_app_clears_server_decisions` |
| 3 | 1건 저장마다 그 해시 그룹만 갱신한다는 설계(§3-3)와 달리 크롤링당 전체 중복군 재계산 | **맞음(설계와 다름, 회귀 아님)** | `reports_repo.save_crawled` / `start.py` 는 건별 저장 후 `merge_final` 에서 전체 재계산 1회. 예전과 같은 동작이고 Opus 가 이 차이를 기록하지 않았다 | 계획서 §5-6 에 차이와 이유를 기록(아래). 건별 부분 갱신은 알림용 그룹 변경 추적과 얽혀 별도 작업으로 |
| 4 | 앱 DB 파일 복사·교체 중 다른 호출이 연결을 다시 열 수 있음 | **맞음** | `closeDb` 뒤 파일을 바꾸는 동안 `db` 게터·`runBackgroundWork` 에 막는 장치가 없었다(R6 이전부터 있던 틈, R6 대기 장치가 좁혔을 뿐) | 파일 작업 잠금(`_withFileExclusive`): 백그라운드 작업이 모두 빠진 직후 같은 동기 구간에서 잠그고, 그 사이 `db`·새 백그라운드 작업은 기다림. `test/storage/connection_test.dart` |
| 5 | Standalone 감지 큐(`standalone_pending_reports`)가 여전히 공유 CSV 읽고-고쳐-쓰기 | **맞음(재현 테스트는 틀림)** | R6 수신함 전환은 알림 기록·대기 변경 두 키뿐이었다. Kotlin `NotificationService.appendPendingReport` 와 Dart `append`(재읽기 없음)가 같은 키를 고쳐 씀 | 큐도 수신함(`inbox.queue.*`)으로. Kotlin 공용 `PrefsInbox.kt`. `test/services/standalone_pending_queue_store_test.dart` |

- 영역 5(마이그레이션)·6(지오코딩)은 문제 없음으로 보고됨 — Opus 의 R6 테스트와 일치.
- 수정 중 Opus 자신의 회귀 1건: 파일 잠금 확인을 `runBackgroundWork` 앞에서 항상 `await` 하게 했더니 작업 수를 세기 전에 한 번 넘어가 `closeDb` 가 "작업 없음"으로 보고 닫았다. 기존 연결 테스트가 잡음 → 잠금이 있을 때만 기다리도록 고침.

## 검증
서버 unittest 98 OK, 합성 왕복 차이 0. 모바일 flutter test 143 통과, 디버그 APK 빌드(Kotlin 컴파일) 성공.
