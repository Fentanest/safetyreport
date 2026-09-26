# 2026-09-27 커뮤니티 업로드 장애 대응(UC-1)·모바일 Android — GPT-6-Sol 검토 기록
범위: safetyreport(PC)·safetyreport-mobile. 설계: docs/plans/2026-09-27-upload-hardening-android.md.

검토자: GPT-6-Sol(codex companion, effort high, 읽기 전용). 작성·수정: Opus. 각 차수의 원문을 아래에 그대로 남긴다.

| 차수 | 대상 | 결과 | 반영 |
|---|---|---|---|
| 계획 | 계획 문서 | 12건 | 계획 §7 표 |
| 1차 구현 | PC 1e28aed · 모바일 30ca8778 | 높음 6·중간 4·낮음 2 | PC 218ba39 · 모바일 e8c6cec3 |
| 2차 | 위 반영 | 해결 9·부분 3, 새 높음 1·중간 2 | PC 938a3a6 · 모바일 f93136a6 |
| 3차 | 위 반영 | 자정 예외 해결, 높음 1·중간 1·굶음(추정) | PC 51213d1 · 모바일 2378ac99 |
| 4차 | 위 반영 | 세 지적 해결, **새 높음/중간 없음** | — |

남은 참고(4차): 승격 함수는 우선순위만 직접 시험했다(실제 연속 경합 시험 없음). 종료 직후 경합에서 불필요한 enqueue 실행이 한 번 더 돌 수 있으나 중복 삽입은 막힌다.

## 1차 원문

## 높음

- **확인되지 않은 과거 ACK가 완료로 남습니다.** [PC community_uploader.py:192](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_uploader.py:192), [모바일 community_uploader.dart:853](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_uploader.dart:853). 재확인 대상 영수증을 UUID 형식이 아닌 `length != 36`으로만 고릅니다. 과거 완료 행의 `receipt_id`가 36자짜리 잘못된 문자열이면 입력→outbox 재생성 없음→중앙의 durable ACK를 확인하지 않은 채 완료 유지가 됩니다. 두 앱 모두 실제 UUID 검증으로 대상을 골라야 합니다.

- **PC는 형식 오류 ACK를 완료 처리하거나 예외로 끝낼 수 있습니다.** [community_upload_policy.py:158](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_upload_policy.py:158), [community_upload_policy.py:175](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_upload_policy.py:175). JSON `{"protocol":true,...유효한 결과...}`는 Python에서 `True == 1`이어서 완료되지만 모바일은 거부합니다. 결과의 `projection_status`나 `status`가 배열이면 집합 조회에서 `TypeError`가 나서 `invalid_ack` cooldown 대신 실행 예외와 `in_flight` 잔류로 이어집니다. 프로토콜·각 필드의 타입을 먼저 검사하고 모든 형식 오류를 `invalid_ack`로 돌려야 합니다.

- **PC v1→v2 동시 이관이 충돌합니다.** [community_store.py:195](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_store.py:195), [community_store.py:211](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_store.py:211). 두 프로세스가 모두 버전 1을 읽은 뒤 첫 프로세스가 이관을 마치면, 둘째는 버전을 다시 확인하지 않고 `upload_runs_v2` 생성에 들어가 `table already exists`로 DB 열기에 실패합니다. 모바일 이관처럼 각 단계의 트랜잭션 안에서 현재 버전을 다시 읽어 건너뛰어야 합니다.

- **모바일 자정 스케줄 lease owner가 실행별로 고유하지 않습니다.** [community_schedule.dart:138](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_schedule.dart:138). 같은 `reason='os'`인 두 백그라운드 실행은 모두 `scheduler:os`로 lease를 획득할 수 있습니다. 입력: 자정 작업과 주기 작업 동시 시작→둘 다 `schedule_runs`를 교체·실행→늦게 끝난 쪽이 앞선 결과를 덮습니다. 실행 UUID를 owner에 넣고 결과 기록에도 소유권을 확인해야 합니다.

- **401 직후 재전송은 요청 간격과 lease heartbeat를 건너뜁니다.** [PC community_uploader.py:660](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_uploader.py:660), [모바일 community_uploader.dart:570](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_uploader.dart:570). 첫 요청이 401이면 갱신 직후 같은 배치를 `_send`로 바로 다시 보냅니다. 따라서 ≥1.1초 간격을 지키지 않고, 갱신 중 lease가 만료·탈취되어도 새 요청을 보낼 수 있습니다. 재전송 전 간격 대기와 owner 일치 heartbeat를 다시 해야 합니다.

- **배포 스크립트의 APK 인증서 검사가 실패를 성공처럼 기록할 수 있습니다.** [build_android_common.sh:249](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/build_android_common.sh:249), [build_android_common.sh:260](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/build_android_common.sh:260). `apksigner` 부재·검사 실패는 문자열을 출력하고 성공 반환합니다. 또 정규식은 일반적인 `Signer #1 certificate DN:` 출력의 `#1` 뒤에 없는 콜론을 요구하므로 인증서 정보를 놓칩니다. 입력: 검사 실패 또는 debug 인증서→`signing-cert.txt`에 “서명 확인 실패”→debug 문자열 검사가 통과합니다. 검사 명령과 파싱 실패 시 빌드를 실패시키고 인증서 지문을 기대 키와 대조해야 합니다.

## 중간

- **합류한 호출이 자신의 재실행 결과 대신 앞선 실행 결과를 받습니다.** [PC community_uploader.py:496](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_uploader.py:496), [모바일 community_uploader.dart:263](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_uploader.dart:263). PC에서 realtime 실행 중 outbox에 없는 미ACK journal이 있고 자정 호출이 합류하면, 자정은 realtime의 `sent`를 받아 key를 `succeeded`로 기록할 수 있습니다. 뒤따른 자정 재실행이 실패해도 그 기록은 바뀌지 않습니다. 모바일도 같은 uploader에 합류한 호출에는 앞선 결과를 반환합니다. 합류 호출은 자신이 요구한 재실행의 결과를 기다려야 합니다.

- **413을 이분해 전부 ACK 받아도 `partial`로 끝납니다.** [PC community_uploader.py:689](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_uploader.py:689), [모바일 community_uploader.dart:612](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_uploader.dart:612). 4건 배치가 413이고 단건 재전송 4건이 모두 완료되어도 이분 과정에서 `had_problem=true`가 남습니다. 입력→미전송 0건인데 `partial`→자정 key는 `failed`. 최종 미전송·차단·재시도 결과로 `sent`를 결정해야 합니다. 양쪽의 413 테스트는 현재 `partial`을 기대해 이 오류를 고정하고 있습니다.

- **백그라운드 게이트 무효화의 영속 기록이 완료를 기다리지 않습니다 — 추정.** [community_schedule.dart:229](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_schedule.dart:229), [background_login_check.dart:66](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/services/background_login_check.dart:66). 403 뒤 `invalidate`는 SharedPreferences 쓰기를 기다리지 않고 반환합니다. 작업 isolate가 곧 종료되면 기존 `ok` 캐시가 남아 다음 작업에서 최대 600초 동안 신선하다고 판정될 수 있습니다. 무효화 쓰기를 업로드 작업의 완료 전에 기다릴 수 있는 비동기 경로가 필요합니다.

- **주기 작업의 recovery가 자정 결과에 묶여 있습니다.** [background_login_check.dart:63](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/services/background_login_check.dart:63). 설계와 아키텍처 문서는 자정 성공 여부와 무관한 복구를 약속하지만, `catchUp` 결과가 `succeeded`일 때만 due 행을 확인합니다. 예를 들어 자정 schedule lease가 다른 실행에 잡혀 `deferred`가 되면, 이 주기 작업은 이미 due인 별도 행의 recovery 기회를 건너뜁니다. 자정 보충과 recovery due 판정을 독립적으로 실행해야 합니다.

## 낮음

- **PC HTTP 연결 재사용은 계획과 다릅니다.** [community_ingest_client.py:80](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_ingest_client.py:80). 요청마다 `build_opener`를 새로 만들어 계획 §1-5의 실행 단위 client 재사용을 충족하지 않습니다. 성능·연결 비용 문제이며 ACK 판정에는 영향이 없습니다.
- **이미지 계획의 구현 표현이 다릅니다.** [계획:123](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/docs/plans/2026-09-27-upload-hardening-android.md:123)은 `cacheWidth`를 적지만 [실제 위젯:1090](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/widgets/report_detail_sheet.dart:1090)은 폭과 높이 상한을 함께 쓰는 `ResizeImage.fit`입니다. 표시 `BoxFit.cover`와 URL·크기 기반 이미지 캐시 키는 유지됩니다. 문서를 실제 동작으로 고치면 됩니다.

**테스트 공백:** 계약 벡터에는 `protocol:true`와 배열형 ACK 필드가 없고, 이관 시험은 동시 실행을 다루지 않습니다. 401 시험은 재전송 횟수만 확인하며 간격·lease 소유권을 확인하지 않습니다. 모바일 백그라운드 시험은 성공 경로의 주기 recovery를 통과시키지 않습니다. 이미지 시험은 크기·메모리만 확인하며 실제 URL 재시도와 캐시 키 동작은 확인하지 않습니다.

**문제 없다고 본 영역:** 두 계약 벡터 파일은 바이트 해시가 같습니다. 정상 durable ACK의 journal 기록과 outbox 삭제는 양쪽 모두 한 트랜잭션입니다. 신고별 앞 revision 선택, UTF-8 전송 바이트 크기 계산, 일반 일시 장애의 배치 중단·영속 cooldown, 데모·Client 업로드 차단, 헤드리스 rebind·등록 금지 경로는 코드상 부합합니다. Android 설정은 R8·리소스 축소를 켜고 전체 keep·광범위 dontwarn을 추가하지 않았으며, 서명키가 없는 일반 release 경로는 차단합니다. `verify_only`는 태그 확인·Release 생성을 건너뛰고 mapping artifact를 올리도록 분기됩니다. 이미지 변경에서 표시 fit과 재시도 횟수를 바꾼 흔적은 없습니다.

허용된 두 테스트 명령은 실행을 시도했으나 읽기 전용 파일시스템에서 PC의 `mktemp`·데이터 디렉터리 생성, 모바일 Flutter SDK 캐시 갱신이 각각 실패해 테스트 본문은 실행되지 않았습니다. 파일은 수정하지 않았고 네트워크 호출도 하지 않았습니다.

## 2차 원문

확인한 HEAD는 PC `218ba39`, 모바일 `e8c6cec3`으로 앞서 검토한 커밋과 같습니다. 재대조 결과 **12건 중 해결 9건, 부분 해결 3건**입니다.

| 지적 | 판정 | 재확인 결과 |
|---|---|---|
| 1. 과거 ACK 영수증 길이 검사 | 해결 | 두 앱이 ACK와 같은 UUID 형식으로 재확인 대상을 고릅니다. |
| 2. PC의 잘못된 ACK 타입 | 해결 | `protocol:true`, 배열형 status·projection을 `invalid_ack`로 처리합니다. 양쪽 계약 벡터도 일치합니다. |
| 3. PC 동시 이관 | 해결 | 이관 단계의 쓰기 트랜잭션 안에서 버전을 다시 확인합니다. |
| 4. 모바일 자정 고정 owner | **부분** | 실행별 owner, 한 트랜잭션 선점, owner 조건부 완료 기록은 적용됐습니다. 업로드 예외 시 `running` 잔류는 남습니다. |
| 5. 401 재전송 간격·lease | **부분** | 간격 대기와 재전송 전 heartbeat가 추가됐고, 정상적인 lease 상실 경로에서 두 번째 attempt는 세지 않습니다. 새 owner가 회수한 행을 덮는 경로가 남습니다. |
| 6. Android 인증서 검사 | 해결 | 도구·파싱 실패를 실패로 반환합니다. 새 [APK 인증서 기록](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/dist/1.3.5+30-e8c6cec3-DEBUG-SIGNED/apk/signing-cert.txt)에 DN·SHA-256이 실제로 기록되어, 이전의 “서명 확인 실패” 출력이 해소됐음을 확인했습니다. 정식 키 빌드 경로는 실행 검증되지 않았습니다. |
| 7. 합류 호출의 결과 차용 | **부분** | 합류한 manual·midnight·recovery·reshare가 자기 실행 결과를 받습니다. 유한한 두 호출에서 교착은 보이지 않지만, 다수 호출의 재귀·연속 실행은 제한되지 않습니다. |
| 8. 413 이분 후 `partial` | 해결 | 전건 ACK면 `sent`입니다. ACK 누락·단건 격리·차단은 여전히 집계되어 `partial`을 유지합니다. |
| 9. 게이트 캐시 무효화 완료 대기 | 해결 | 백그라운드 업로드가 `finally`에서 캐시 쓰기 `flush()`를 기다립니다. |
| 10. 자정 결과에 묶인 recovery | 해결 | 주기 작업이 자정 결과와 별개로 due 여부를 확인합니다. |
| 11. PC HTTP 재사용 문서 | 해결 | 계획이 요청별 opener 사용을 명시합니다. |
| 12. 이미지 디코딩 문서 | 해결 | 계획이 실제 `ResizeImage.fit`의 폭·높이 상한을 설명합니다. |

## 이번 수정에서 생긴 결함

- **높음 — lease 상실 후 새 실행 소유의 행을 변경합니다.** [PC community_uploader.py:666](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_uploader.py:666), [모바일 community_uploader.dart:575](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_uploader.dart:575). 첫 실행이 401 뒤 토큰 갱신 중 멈추고, lease 만료 후 둘째 실행이 같은 행을 회수해 `in_flight`로 보낸 상황에서 첫 실행의 heartbeat가 실패하면, 첫 실행은 owner 조건 없는 `_retry_rows`로 둘째의 행을 `retry_wait`로 바꾸고 lease 필드를 지웁니다. 첫 요청의 attempt 1회는 유지되지만 재시도 시각은 오래된 attempt 값으로 계산될 수도 있습니다. **`state='in_flight' AND lease_owner=<첫 실행>` 조건으로 자기 행만 바꿔야 합니다.** 재전송 경로의 heartbeat도 간격 대기 *후*, 요청 직전에 확인해야 장시간 일시정지에 안전합니다.

- **중간·추정 — 합류 요청이 많으면 재귀 대기열에 상한이 없습니다.** [PC community_uploader.py:499](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_uploader.py:499), [모바일 community_uploader.dart:243](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_uploader.dart:243). 다수의 manual 호출이 같은 실행에 합류하면 완료 뒤 한 호출이 새 실행을 시작하고 나머지가 다시 합류할 수 있습니다. PC에서는 반복 합류한 호출의 스택 깊이와 총 대기 시간이 계속 늘어납니다. **유한한 두 호출의 무한 대기·교착이나 개별 실행의 25요청·90초 예산 우회는 확인되지 않았습니다.** 모바일 controller는 자기 wake를 직렬화하지만 패널·자정 등 별도 호출자와의 합류는 막지 않습니다. 재귀 대신 반복 대기 또는 제한된 요청 병합이 적절합니다.

- **중간 — 과거 ACK 검사 때문에 완료 이력 전체를 매번 적재합니다.** [PC community_uploader.py:192](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_uploader.py:192), [모바일 community_uploader.dart:851](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_uploader.dart:851). 완료 이력이 큰 DB에서 매시간 recovery가 실행되면 모든 ACK의 ID·영수증을 `fetchall`/`rawQuery`로 읽고 쓰기 트랜잭션 안에서 순회합니다. 이력 증가에 따라 메모리와 쓰기 잠금 시간이 늘어납니다. 제한된 크기의 순회나 일회성 이관 표시가 필요합니다.

**남은 조건:** [모바일 catchUp:155](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_schedule.dart:155)에서 `runUpload`가 예외를 던지면 완료 UPDATE에 도달하지 않아 `running`이 lease 만료까지 남습니다. WorkManager가 곧 재시도해도 그동안은 deferred가 됩니다. PC 자정 경로에도 같은 조건이 있으며, 이번 수정에서 새로 생긴 결함은 아닙니다. owner 조건을 유지한 예외 기록이 필요합니다.

추가 시험은 PC 이관의 두 연결 경쟁과 모바일 합류 호출의 **자기 결과 반환**을 이전 코드와 구별합니다. 413 시험도 전건 완료 결과를 확인합니다. 반면 401 시험은 lease 테이블의 owner만 바꾸고 **outbox 행을 둘째 실행이 소유한 상황**을 만들지 않아 위 높은 결함을 놓칩니다. 모바일 자정 시험은 예외 후 재시도를 다루지 않으며, 인증서 실패·정식 지문 불일치의 자동 시험은 없습니다. 이번 재검토에서는 파일 수정·네트워크 호출·테스트 실행을 하지 않았습니다.

## 3차 원문

## 확인 결과

1. **401 재전송·heartbeat: 부분.** 두 앱 모두 요청 간격 대기 **뒤**에 lease를 갱신합니다. 갱신에 실패하면 현재 재전송 배치는 `in_flight`이면서 자기 owner인 행만 `retry_wait`로 바꿉니다. 두 번째 요청은 보내지 않으며, 첫 요청의 attempt는 유지됩니다. 다만 같은 실패 분기에서 호출하는 보류 행 정리가 새 owner의 행을 덮을 수 있습니다. 아래 높음 항목입니다.

2. **합류 반복 루프: 부분.** 유한한 실행에서는 도착 전 실행의 결과를 자기 결과로 받지 않고, 뒤에 시작한 적격 실행에 합류하거나 직접 실행합니다. 일반적인 완료 경로에서 재귀나 교착도 보이지 않습니다. 그러나 연속된 `realtime` 실행이 합류 호출보다 매번 먼저 시작하면 `manual`·`midnight` 호출이 계속 기다릴 수 있습니다(**추정**, 공정한 실행 순서 보장 없음). [PC 499–521행](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_uploader.py:499), [모바일 249–265행](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_uploader.dart:249). 추가된 네 호출 시험은 한 번의 선행 `realtime` 실행만 다룹니다.

3. **영수증 GLOB·정규식: 불일치.** PC 정책의 `$`는 마지막 개행 바로 앞도 끝으로 인정합니다. 따라서 `11111111-1111-4111-8111-111111111111\n`은 PC ACK 정규식에서 통과하지만 새 SQL GLOB에서는 실패합니다. 메모리 내 Python·SQLite 확인 결과가 각각 `True`, `False`였습니다. 모바일 정규식은 같은 입력을 거부했습니다. [PC 정책 27·169행](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_upload_policy.py:27), [PC GLOB 197–201행](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_uploader.py:197). PC 정책을 완전 일치 판정으로 고치고 이 입력을 계약 벡터에 넣는 방향이 맞습니다.

4. **자정 예외 기록: 해결.** 두 앱 모두 업로드 호출의 예외를 `failed`로 바꾸고, `schedule_runs` 갱신에 자기 owner 조건을 둡니다. 따라서 lease를 이어받은 실행의 기록은 덮지 않습니다. [PC 165–186행](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_schedule.py:165), [모바일 156–176행](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_schedule.dart:156).

## 높음·중간 결함

- **높음 — lease 상실 때 보류 중인 다른 행은 새 owner를 덮음(잔존 결함).** 첫 신고가 단건 `schema_invalid`로 대조 보류되고, 둘째 신고가 401을 받은 뒤 다른 실행이 lease와 첫 신고 행을 회수하면, 이전 실행의 `_hold_suspects`가 첫 행을 owner 확인 없이 `retry_wait`로 바꾸고 새 owner를 지웁니다. 이번 수정은 401 배치만 보호합니다. 보류 행 정리에도 `state='in_flight' AND lease_owner=?`를 적용해야 합니다. [PC 683–687·730–739행](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_uploader.py:683), [모바일 592–597·776–785행](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_uploader.dart:592).

- **중간 — 새 SQL GLOB과 PC ACK 판정이 다른 집합을 사용함.** 위 개행 영수증을 PC가 ACK로 저장한 뒤 `manual`을 실행하면, GLOB이 그 ACK를 지우고 같은 이벤트를 다시 보냅니다. 같은 형식의 응답이 반복되면 재전송도 반복됩니다. PC ACK 판정을 GLOB과 같은 완전 일치 규칙으로 맞춰야 합니다. [PC 197–201행](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_uploader.py:197).

401 추가 시험은 현재 배치 한 행만 검증해 보류 행 문제를 잡지 못합니다. heartbeat 시험도 호출 횟수와 대기 여부는 확인하지만 **대기 후 갱신** 순서는 단언하지 않습니다. 전체 테스트는 실행하지 않았으며, 파일 수정과 네트워크 호출 없이 코드 및 메모리 내 경계 입력만 확인했습니다.

## 4차 원문

## 4차 확인 결과

1. **보류 행 owner 조건 — 해결.** 두 앱의 보류 행 정리는 `in_flight`이면서 자기 lease owner인 행만 갱신합니다. 공통 422 거절의 두 번째 행도 같은 정리 경로를 사용합니다. 새 owner가 회수한 행을 덮던 경로는 막혔습니다. [PC](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_uploader.py:757) · [모바일](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_uploader.dart:801)

2. **영수증 완전 일치 — 해결.** PC가 `fullmatch`로 바뀌어 끝 개행을 거부합니다. 확인한 UUID 경계 입력에서 PC 정규식과 SQLite GLOB의 결과가 같았고, 개행 계약 벡터도 두 레포에 동일하게 들어갔습니다. [PC 정책](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_upload_policy.py:169)

3. **연속 `realtime`에 의한 굶음 — 해결.** 대기 중인 enqueue 호출이 있으면 다음 `realtime` 실행이 그 트리거로 시작하므로, 도착 순번 검사에서 합류할 수 있습니다. 대기 수 증감은 두 앱 모두 `finally`로 감싸져 일반적인 반환·예외에서 누수나 교착이 보이지 않습니다. [PC](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport/services/community_uploader.py:493) · [모바일](/home/better0101/projects/worktree/upload-r8-20260927/safetyreport-mobile/lib/community/upload/community_uploader.dart:251)

승격은 이미 실행 중인 호출도 대기 수에 포함합니다. 종료 직후의 짧은 경합에서 불필요한 enqueue 실행이 한 번 더 시작될 수 있으나, 행 삽입은 중복 방지 조건을 사용하고 기다리던 호출도 누락되지 않습니다. 추가 시험은 승격 함수의 우선순위만 직접 검사하므로, 실제 호출이 연달아 경합하는 시험은 아직 없습니다.

**이번 변경으로 새로 생긴 높음/중간 결함: 없음.** `git diff --check`와 계약 벡터 동일성도 확인했습니다. 전체 테스트 실행이나 네트워크 호출, 파일 수정은 하지 않았습니다.
