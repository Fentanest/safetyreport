# 2026-09-27 커뮤니티 업로드 장애 대응 보완 + 모바일 Android(AGP 9·R8·이미지) 작업계획

범위: safetyreport(PC)·safetyreport-mobile. 기준 HEAD PC dev `3853578`, 모바일 dev `9c6fff74`(둘 다 origin 과 같음, 사용자 미추적 파일만 있음).
작업 폴더 `~/projects/worktree/upload-r8-20260927/{safetyreport,safetyreport-mobile}` (branch `ur0927/*`). 원격 push·운영 Supabase·스토어 배포 없음.
중앙(map/auth)은 계약 확인용으로만 읽었다 — 서버 동작 변경 없이 클라이언트만으로 해결한다(아래 §6).

## 0. 현재 코드에서 재현된 문제 (조사: 서브에이전트 3개 + 중앙 코드 직접 확인)

| # | 문제 | PC | 모바일 |
|---|---|---|---|
| R1 | 예외 한 번에 업로더 영구 정지(`_log` 미정의 → finally UnboundLocalError) | 재현 | – |
| R2 | ACK: `durable` 미확인, 형식 오류 ACK → `schema_invalid` → dead_letter | 재현 | durable 미확인, 본문 `httpStatus` 가 실제 상태를 덮음, 오류 응답의 `results` 도 적용 |
| R3 | 빈 results·누락 ACK | ~1초 고정 재시도 | **즉시 무한 재전송 루프** |
| R4 | attempt_count | 송신·오류에서 2번 증가, 안 보낸 건도 증가 | `_drain` 이 `j.*` 만 조회 → 항상 1초 |
| R5 | 429·5xx·offline 뒤 | 남은 배치 계속 송신 | 멈추지만 영속 cooldown 없음(다음 호출이 즉시 송신) |
| R6 | Retry-After | 429 정수만, 503 무시, HTTP-date 무시, 비JSON 본문이면 의미 상실 | 헤더를 아예 안 읽음 |
| R7 | 401 "강제 갱신" | 캐시 토큰 재사용(실제 갱신 아님), gate 무효화 없음, 갱신 네트워크 실패도 auth_required | 같음 + isolate 간 갱신 경쟁 |
| R8 | kakao_required/session_revoked | journal blocked_reason 으로 영구 차단(계약: auth_required) | 같음 + auth_required 행이 영구 제외 |
| R9 | 신고별 revision 순서 | 앞 revision 이 대기 중이면 뒤 revision 먼저 송신 | 같음 |
| R10 | 크기 | 로컬 초과·413 → 배치 통째 dead_letter, `_build_batches` 가 data_dir 무시 | UTF-16 길이 추정, TooLarge 가 매 실행 같은 배치로 막힘, 413 통째 dead_letter |
| R11 | 비JSON 4xx(404 HTML 등) | – | 배치 통째 dead_letter |
| R12 | 재시도 실행기 | next_retry_at 타이머 없음(게이트 poll 부수효과로만 돎), 시작 catch_up 이 lifespan 을 막음 | `wake()` 호출처 없음, 자정 성공 뒤 실시간 업로드가 다음날까지 대기, resume·네트워크 복구 없음 |
| R13 | 백그라운드 | – | 게이트 캐시 600초 초과면 매번 종료 → 예약 업로드 사실상 없음, 데모 모드 writer 등록 |
| R14 | 자정 | 대기 전(backoff) 행만 있어도 no_change→succeeded, misfire 1초 | 같음, recovery 트리거 미사용 |
| R15 | lease | heartbeat·소유권 확인 없음(300초, drain 무제한) | owner 고정 'uploader' → 두 isolate 동시 업로드, 남의 lease 해제 |
| R16 | 전체 적재 | 대기 전체+payload 를 메모리로 | LIMIT 60 이지만 실행 예산 없음 |
| R17 | upload_runs | wake 마다 1행(분당 1행) 무제한 | 호출마다 1행 무제한 |
| R18 | 상태 표시 | 다음 재시도·가장 오래된 미전송·혼잡/인증 구분·마지막 중앙 저장·quarantined 없음 | 같음 + auth_required 행 안 보임 |
| R19 | HTTP | 요청마다 opener(재사용 없음), 응답 크기 제한 없음 | 배치마다 새 client 누수, timeout 이 요청을 끊지 않음 |

이미 맞는 것(유지): 수집 사본 → journal+outbox(한 트랜잭션) → 개인 DB 순서, 재시도는 저장된 event_id·payload 재사용, 네트워크 중 쓰기 트랜잭션 없음,
보낸 id 만 매칭(모바일), conflict→dead_letter·rejected→blocked, Client 모드 미업로드, KST 자정 key 계산, 누락 자정 1회 보충.

## 1. 두 앱 공통 업로드 제어 규칙 (UC-1) — `contracts/upload-control/` (두 레포 바이트 동일, 해시 테스트)

### 1-1. 응답 판정
- 전송 계층 결과 `(http_status, headers, body_bytes)` 와 서버 JSON 을 분리한다. 본문 필드가 상태·헤더를 덮지 않는다.
- **완료(ACK 확정)** = HTTP 200 ∧ JSON 객체 ∧ `protocol==1` ∧ `request_id` 문자열 ∧ `results` 배열 ∧ 항목마다 형식 유효 ∧ event_id 가 이번에 보낸 것 ∧ 같은 event_id 중복 없음
  ∧ `durable===true` ∧ status ∈ {accepted, duplicate, no_change, stale_ignored, quarantined} ∧ `receipt_id` 가 UUID 문자열(중앙 SQL 이 durable 상태마다 준다).
- `durable:false` 는 status ∈ {rejected, conflict} ∧ `error{code,retryable}` 일 때만 유효 → conflict=dead_letter(`event_conflict`), rejected=blocked. 그 밖의 durable:false·타입 오류는 형식 오류.
- 응답 전체가 형식 오류(깨진 JSON·HTML·protocol 오류·모르는 id·중복 id·항목 형식 오류) → **invalid_ack**: 보낸 행 전부 retry_wait(백오프), 서비스 cooldown 1회 실패로 센다. dead_letter 로 보내지 않는다.
- 형식이 유효하고 일부 id 만 빠진 응답 → 있는 항목은 반영, 빠진 id 는 **ack_missing** 으로 retry_wait(백오프). 같은 실행에서 다시 보내지 않는다.
- quarantined(durable) 는 outbox 정리·journal ack 기록, 화면에는 "중앙 보관됨·지도 미반영(확인 필요)" 로 따로 센다.
- 완료 처리는 journal ack 기록 + outbox 삭제를 **한 로컬 트랜잭션**으로.

### 1-2. 오류 분류 (코드 → 행 상태 / cooldown 범위)
| 분류 | 조건 | 행 | cooldown |
|---|---|---|---|
| `offline` | 연결·DNS·timeout·토큰 갱신 네트워크 실패 | retry_wait | service |
| `rate_limited` | 429 | retry_wait | account |
| `server_busy` | 5xx(500/502/503/504), 오류 envelope 없는 4xx(404 HTML 등), 408 | retry_wait | service |
| `invalid_ack` / `ack_missing` | 1-1 | retry_wait | service(invalid_ack 만) |
| `auth_required` | 401(실제 1회 강제 갱신 뒤에도), 403 kakao_required·session_revoked, 갱신 토큰 확정 폐기 | auth_required(journal 차단 없음), gate 무효화 | – |
| `consent_rejected` | 403 consent_* | blocked(consent_*), gate 무효화 | – |
| `connection_rejected` / `writer_superseded` / `contributor_suspended` | 403 해당 코드 | blocked (기존 rebind 흐름 유지) | – |
| `request_too_large` | 413, 로컬 envelope 초과 | 배치를 반으로 나눠 재시도, 단건이면 그 건만 dead_letter | – |
| `payload_invalid` | 유효한 오류 envelope 의 422 payload_hash_mismatch/event_type_mismatch | 배치 이분 → 단건이면 dead_letter | – |
| `payload_invalid`(모호) | 400 invalid_request·422 schema_invalid | 이분 → 단건이면 대조 요청(다른 이벤트 1건): 그 이벤트가 저장되면 앞 단건만 dead_letter, 모두 거절이면 버리지 않고 보류(failed) | – |
| `request_rejected` | 405·415(오류 envelope 없음) 또는 method_not_allowed/unsupported_media_type | retry_wait(보류), 실행 failed | service |
| `event_conflict` | ACK conflict | dead_letter | – |
- HTTP 라이브러리 재시도 없음. 재시도는 outbox(next_retry_at)·제어기(cooldown)·스케줄러(깨우기)가 나눠 맡고, 백그라운드 OS 재시도는 "상태를 저장하기 전 충돌" 일 때만.

### 1-3. 횟수·대기
- `attempt_count` 는 **실제 HTTP 요청마다 그 요청에 들어간 이벤트만 +1**(401 뒤 실제 재전송도 +1). gate 차단·cooldown·lease 실패·로컬 초과·토큰 없음(요청 안 함)은 세지 않는다.
- 로컬 백오프 `backoff(n,u) = min(300, 5·2^(n-1)) × (0.5 + 0.5u)` 초(u∈[0,1) 주입 가능, 상한은 지터 뒤에도 300).
- 서버 지시 `hint` = 유효값 중 최댓값: 헤더 `Retry-After`(정수 초 또는 HTTP-date, 대소문자 무관) · 본문 `error.retry_after_seconds`(정수≥1). 과거 시각·음수·파싱 불가는 무시, 24시간 초과는 24시간(비정상 값 방어 — 유일한 예외, 백오프 상한과 별개). HTTP-date 는 응답 `Date` 헤더 기준(없으면 로컬 시각).
- 재시도 시각 = now + max(hint, backoff(n,u)). 서버 지시보다 일찍 보내지 않고, 서버 지시를 백오프 상한으로 줄이지 않는다. 장시간 실패해도 미전송 사본을 폐기하지 않는다.

### 1-4. 영속 전송 제어 (community.db 새 표 `upload_control`, migration v1→v2)
`scope`(service:`<project>` / account:`<project>:<contributor>`), `state` ready|cooling_down|probing, `next_attempt_at`, `consecutive_failures`, `last_error_code`, `updated_at`.
- 일시 장애(offline·server_busy·invalid_ack → service, rate_limited → account): 시도한 배치만 결과 기록, **남은 배치 중단**, cooling_down, next_attempt_at = now + max(hint, backoff(consecutive)).
- 모든 트리거(실시간·수동·자정·복구·재공유)가 같은 제어기를 먼저 확인한다. cooling_down 이고 시각 전이면 보내지 않고 `cooldown` 으로 끝낸다(새 수집은 로컬에만 쌓임). 재시작·연타로 초기화되지 않는다.
- 시각이 지나면 probing: **대기 이벤트 1건짜리 요청 하나**로 확인 → 성공이면 ready 로 제한 속도 재개, 실패면 cooling_down(연속 실패+1). 별도 상태 확인 요청은 보내지 않는다.
- 인증·동의 오류는 제어기를 건드리지 않는다(다른 계정 전송을 막지 않음).

### 1-5. 한 번 실행의 범위
- 동시 업로드 1개(데이터셋 범위 lease `upload`), 실행별 고유 owner(`run:<uuid>:<trigger>`). 획득은 원자적, 갱신·해제는 owner 일치일 때만.
- 요청마다 먼저 lease heartbeat(연장) — 실패하면(소유권 상실) 새 배치를 보내지 않고 끝낸다. 보낸 행은 `in_flight` + lease_owner/lease_until. 실행 시작 때 만료된 in_flight(lease_until 지남)만 retry_wait 로 되돌린다(살아 있는 실행의 행은 건드리지 않음).
- 실행 예산: 요청 최대 25개·90초, 요청 간격 ≥1.1초(중앙 한도 분당 60). 남으면 곧바로 다음 실행을 깨운다. DB 는 200행씩 읽는다(payload 는 보낼 배치만).
- 선택 규칙: 신고마다 **보낼 수 있는 가장 앞 revision 하나**만 후보(dead_letter·blocked 는 제외), 그 행이 due 가 아니면 그 신고는 이번에 건너뜀 → 뒤 revision 이 먼저 가지 않는다. 한 요청 1신고 1이벤트, ≤20건, envelope 전체 UTF-8 ≤ 256KiB(최종 직렬화 바이트로 계산).
- 네트워크 대기 중 SQLite 트랜잭션 없음. 모바일 HTTP client 는 실행 단위로 하나 만들고 끝에 닫는다(30초에 요청을 끊음). PC 는 요청마다 urllib opener 를 만든다(연결 재사용 없음 — 요청 간격 1.1초라 비용이 작아 그대로 둠). 응답 본문은 최대 1MiB 까지 읽는다.

### 1-6. 실행 결과·기록
결과 코드(공통): `sent`, `partial`, `no_pending`(미전송 0), `not_due`(있으나 재시도 시각 전), `cooldown`, `busy_other_run`, `needs_auth`, `needs_consent`, `blocked_gate`, `failed`, `more_pending`(예산을 다 써 남음 — 곧바로 다시 깨움, 자정 key 는 deferred).
- PC `/api/v1/community/upload/*` 의 `result` 는 옛 값으로 변환해 유지(모바일 소비자 호환), 새 코드는 `outcome` 추가 필드.
- `upload_runs` 는 **요청을 1개 이상 보낸 실행과 상태가 바뀐 실행만** 기록, 최근 500행·30일 보관. 미전송 사본은 정리 대상 아님.
- 자정: 그날 key 는 `sent`/`no_pending` 일 때만 succeeded. `not_due`/`cooldown`/`busy_other_run`/`needs_*` 는 deferred(사유 저장). 자정 성공 여부와 무관하게 복구(`recovery`)는 재시도 시각 타이머로 따로 돈다. 누락 자정은 다음 기회에 1회만(기존 규칙).
- 로그·상태에 payload·토큰·비밀 없음(request_id 는 앞 8자만 화면에).

### 1-7. 상태 표시(지도 탭, 두 앱 같은 항목)
대기/재시도/전송 중/인증 필요/차단/보류(dead_letter)/중앙 보관·지도 미반영(quarantined) 건수, 가장 오래된 미전송 시각, 다음 재시도 가능 시각,
제어 상태(서버 혼잡 대기·언제까지 / 인증 조치 필요), 마지막 중앙 저장 확인 시각(ack 최대 시각), 중앙 저장 건수와 지도 반영(published) 구분.
원인은 확인 가능한 수준으로만("서버가 일시적으로 요청을 받지 못함(503)" 등) — DB 연결 부족 같은 추정 원인을 쓰지 않는다.

### 1-8. 재로그인·계정
auth_required 행은 게이트가 다시 ok(같은 contributor·project·유효 grant·승인 연결)이면 다음 실행에서 그대로(같은 event_id·payload) 재개. 다른 계정·철회 grant 로는 승계하지 않는다(기존 context_mismatch 차단 유지).
401: 실제 강제 갱신(refresh token 사용) 1회 → 성공이면 1회 재전송, 갱신이 네트워크 실패면 offline, 갱신 토큰 폐기면 auth_required. 모바일은 isolate 간 갱신 잠금(community.db lease `auth_refresh`) + 잠금 뒤 저장된 토큰 재확인(회전된 refresh token 덮어쓰기 방지).

## 2. 플랫폼별 추가
- PC: 재시도 실행기 = 기존 1초 poll 루프에 "캐시된 next_due(가장 이른 next_retry_at·cooldown) 도달" 조건 추가(행마다 타이머 없음). 시작 catch_up 은 백그라운드 스레드로. 종료 때 새 실행을 시작하지 않음.
  자정 cron `misfire_grace_time`·`coalesce`. data_dir 주입 무시 경로 제거. `_log` 결함·`{"error":"str"}` 크래시 수정.
- 모바일: 앱 단위 업로드 제어 싱글턴(`wake(trigger)` — 실행 중이면 재실행 표시, clear 로 새 알림을 잃지 않음), 수집 완료·앱 복귀·네트워크 복구·재시도 타이머가 깨움.
  WorkManager: 게이트 캐시가 오래됐으면 headless 로 중앙 상태를 제한적으로 재확인(설정·secure storage·세션 갱신) → ok 면 업로드, 일시 장애면 상태 보존하고 종료, 명시적 거절이면 차단.
  데모·Client 는 업로드·writer 등록 없음. WorkManager 결과는 상태를 저장했으면 성공(true), 저장 전 예기치 못한 예외면 false(OS 재시도).

## 3. 기존 사본·대기 자료 보존
community.db 삭제·초기화 없음. v1→v2 migration 은 표 추가만(기존 행 무변경). 이미 dead_letter 로 잘못 간 행(R2·R10·R11 로 생긴 것)은 자동 복구하지 않는다 — 사유별로 화면에 보이고,
사용자가 "재공유" 로 다시 보낼 수 있다(기존 흐름). 개인 DB·초기화 크롤링 상태는 건드리지 않는다.

## 4. 모바일 Android
- **도구 조합**: Flutter 3.41.6 은 AGP 9 를 지원하지 않는다(공식: 3.44 가 builtInKotlin=false 임시 지원, 3.47 이 built-in Kotlin 지원). 전역 SDK 는 그대로 두고
  Flutter **3.47.5**(Dart 3.13.4, 현재 stable) 를 `~/development/flutter-3.47.5` 에 따로 설치해 프로젝트가 고정 버전으로 쓴다(`tool/flutter-version`, 빌드 스크립트·CI 가 같은 값을 검사).
  AGP 는 그 Flutter 가 검증한 9.x 최신(flutter_tools `maxKnownAndSupportedAgpVersion`)과 AGP 요구 Gradle, KGP ≥ 2.2.10, JDK 17 이상.
- **Kotlin/DSL**: 앱은 `kotlin-android`·`kotlinOptions` 제거 → `kotlin { compilerOptions }`. 플러그인 중 kotlin-android 를 쓰는 것(in_app_review, package_info_plus, share_plus,
  shared_preferences_android, video_player_android)은 안정판 새 버전이 이전했으면 최소 업데이트. 전부 이전됐을 때만 `android.builtInKotlin=true`,
  아니면 Flutter 공식 임시 옵션 `android.builtInKotlin=false`(+필요 시 `android.newDsl=false`)를 근거·범위·제거 조건과 함께 기록. AGP 10 에서 opt-out 이 없어진다.
- **R8**: release 에 `isMinifyEnabled=true`·`isShrinkResources=true` 를 명시(FGP 주입과 같은 값). AGP 9 는 optimized resource shrinking 기본. 빌드 로그·mapping·resources.txt·
  산출물(APK dex/리소스)로 적용 확인. keep 규칙은 근거 있는 최소만(기존 `-assumenosideeffects Window.set*Color` 는 검토).
- **서명**: `key.properties` 가 없으면 release 빌드를 실패시키고, 검증용은 `ALLOW_DEBUG_SIGNED_RELEASE=1` 로만 debug 서명 + 산출물 이름·메타에 표시.
- **H2.h.b**(추적 완료·수정 보류 — §7): Play 후보 3개 버전(1.3.3+28·1.3.4+29·1.3.5+30) CI AAB 의 `BUNDLE-METADATA/.../proguard.map`(모두 pg_map_id `fbc35fa`) 에서
  `H2.h` = `com.mr.flutter.plugin.filepicker.FileUtils`, `b` = `processUri`/`compressImage`(file_picker 11.0.2 `FileUtils.kt:464` `BitmapFactory.decodeStream` 옵션 없음) 확인.
  앱은 압축을 쓰지 않아 실제 실행 경로는 아니지만 바이너리에 포함. file_picker **13.1.0**(android_file_picker 2.0.0: inJustDecodeBounds+inSampleSize) 로 최소 업데이트 + API 이전.
- **이미지**: 앱·플러그인 Android 코드에 다른 BitmapFactory 없음, 알림 이미지 없음. Dart 는 신고 상세 `Image.network` 가 원본 해상도 디코딩 → 구현은
  `ResizeImage(NetworkImage, policy: fit)` — 폭 = 화면 폭×devicePixelRatio(≤4096), 높이 상한 = 화면 높이×devicePixelRatio×4(≤8192), 비율 유지·확대 없음. 원본 보기·공유는 기존 "다른 앱으로 열기"(원본 파일) 유지.
- **산출물**: 빌드마다 `dist/<versionName>+<code>-<commit>/{apk,aab}/` 에 산출물·그 빌드 직후 복사한 mapping·configuration/usage/resources·메타(버전·커밋·Flutter/AGP/Gradle/JDK·SHA-256).
  CI 는 mapping·메타도 artifact 로 올리고, 배포와 무관한 검증 실행(workflow_dispatch `verify_only`)을 둔다.

## 5. 테스트 순서
1) 공통 판정 벡터(`contracts/upload-control/vectors.json`)를 Python·Dart 가 같은 결과로 통과 → 2) 두 앱 업로더 시나리오 테스트(요청서 §12-A 목록, 시계·난수 주입)
→ 3) 기존 전체 단위 테스트·DB 왕복 → 4) 로컬 실제 스택(합성 Supabase + 가짜 카카오)으로 실제 ingest ACK/오류 → 5) 모바일 Flutter 3.47.5 analyze·test
→ 6) Android clean release APK·AAB(검증 서명), R8·리소스 증거, mapping 대응 → 7) 에뮬레이터(API 35)에서 release 실행·백그라운드 작업·이미지 메모리 측정(수정 전후)
→ 8) GPT-6-Sol 검토(계획·구현) → 병합. 실기기·정식 서명·운영 로그인·Play 재검사는 미실행으로 분리 보고.

## 6. 중앙 계약
서버 코드 변경 없이 해결된다. 현재 중앙이 주는 것만 쓴다: 오류 envelope·`Retry-After`(429 에서 60), `durable`·`receipt_id`(durable 상태마다), 1~20건·256KiB·요청당 1신고.
중앙 쪽 권장(선택, 이번에 하지 않음): 503 에도 `Retry-After` 를 주면 클라이언트가 그대로 따른다. `results` 가 보낸 모든 이벤트를 항상 포함한다는 문장을 ack.schema/errors.md 에 명시.

## 7. GPT-6-Sol 계획 검토(12건) 반영과 구현 중 결정 (2026-09-27)

| # | 지적 | 반영 |
|---|---|---|
| 1 | 예전 코드가 영수증 없이 완료로 적은 행 | manual/midnight/recovery enqueue 때 현재 연결·동의의 durable 행 중 receipt 가 없거나 UUID 형식이 아니면 ack 를 지우고 같은 event_id 로 다시 확인(중앙은 duplicate+receipt) |
| 2 | upload_runs CHECK 가 새 결과 코드를 막음 | v2 단계에서 표 재생성(행 보존) |
| 3 | 옛 in_flight 는 lease 가 비어 영원히 안 돌아옴 | 시작 때 `lease_until IS NULL OR < now` 를 되돌림 |
| 4 | 예산 초과를 자정 성공으로 적음 | `more_pending` 결과, 자정 deferred, 곧바로 다시 깨움 |
| 5 | 재전송이 현재 context 의 writer_epoch 로 바뀜 → conflict | journal 에 저장된 writer_epoch 그대로(없으면 dead_letter `writer_epoch_missing`) |
| 6 | 400/422 가 envelope 공통 문제일 수 있음 | 모호 코드는 대조 요청으로 판정, 모두 거절이면 보류 |
| 7 | 405/415 는 요청 형식 문제 | request_rejected: 보류 + 서비스 cooldown + failed |
| 8 | 백그라운드 게이트 무효화가 isolate 에 안 보임 | `CacheGateCheck.invalidate` 가 캐시에 기록, 헤드리스 재확인 |
| 9 | Retry-After 상한·기준 시각 | 24시간 상한, 응답 Date 기준 |
| 10 | CI 검증 전용 경로 | workflow_dispatch `verify_only`(태그 확인·Release 생략, dist artifact) |
| 11 | AGP 9 세부 | Flutter 3.47.5 템플릿 조합(AGP 9.1.0·Gradle 9.3.1·KGP 2.4.0, newDsl/builtInKotlin opt-out 근거·제거 조건 기록) |
| 12 | Android 사용처 시험·세로로 긴 이미지 | 디코딩 크기 시험(`photo_decode_size_test`), 세로 상한(화면 높이×4) |

구현 중 결정:
- **file_picker 13(H2.h.b 수정판) 보류**: file_picker ≥12 는 windows 구현이 win32 6 을 요구 → package_info_plus 10·share_plus 13 →
  flutter_secure_storage 11 이 필요하다. flutter_secure_storage 11 은 v10 이전 방식(`encryptedSharedPreferences`, 옛 기본 cipher)으로
  저장한 값을 읽지 못한다(변경 기록: "upgrade to v10 first so existing data is migrated") — 커뮤니티 세션·연결 비밀·로그인 정보가
  사라진다. "새 암호화 도입 금지"·데이터 보존 규칙과 충돌하므로 이번에 올리지 않았다. 11.0.2 의 `FileUtils.compressImage`
  (`BitmapFactory.decodeStream` 옵션 없음)는 `compressionQuality > 0` 일 때만 실행되고 앱은 항상 0 이라 실행 경로는 아니다(바이너리에는 있음).
  선택지: (a) flutter_secure_storage 9→10 이관 릴리즈(이관 시험 포함)를 먼저 내고 다음 릴리즈에서 11·file_picker 13, (b) file_picker 11.0.2 의
  Android 코드만 고친 로컬 사본(유지 부담), (c) 그대로 두고 Play 경고 사유를 기록.
- 모바일은 연결 변화 감지 플러그인을 추가하지 않았다(cooldown 탐색·앱 복귀·재시도 타이머로 복구).
- 모바일 location_supplement 발급은 원래 없었다(후보 함수만) — 이번 범위 밖, PC 와 차이로 남김.

### 구현 검토(GPT-6-Sol, 2026-09-27) 반영

| 심각도 | 지적 | 반영 |
|---|---|---|
| 높음 | 옛 완료 재확인이 영수증 길이(36)만 봄 | 두 앱 모두 UC-1 과 같은 UUID 정규식으로 판정(36자 비UUID 도 다시 확인 — 시험 추가) |
| 높음 | PC 가 `protocol:true`·배열형 status/projection 을 통과·예외 | 정수 1·문자열 타입 검사, 벡터 4건 추가(두 레포 같은 결과) |
| 높음 | PC v1→v2 동시 이관 충돌 | 단계 트랜잭션 안에서 버전 재확인(모바일과 같음), 경쟁 재현 시험 + 대조 실험 |
| 높음 | 모바일 자정 lease owner 가 `scheduler:os` 로 같음 | PC `run_midnight` 와 같은 구조로 재작성: 한 트랜잭션 확인·선점, 실행별 owner, owner 일치일 때만 결과 기록(PC 도 owner 조건 추가) |
| 높음 | 401 재전송이 간격·heartbeat 를 건너뜀 | 재전송 전 lease 연장(잃으면 retry_wait + busy_other_run)·요청 간격 대기, 시험 추가 |
| 높음 | 빌드 스크립트 인증서 검사 실패가 성공으로 기록 | 도구 없음·검사 실패·두 줄 못 읽음이면 실패, 정규식 수정, 선택 `EXPECTED_RELEASE_CERT_SHA256` 대조 |
| 중간 | 합류한 호출이 앞선 실행 결과를 받음(자정 key 를 남의 결과로 성공 처리) | manual/midnight/recovery/reshare 는 앞선 실행이 끝난 뒤 자기 실행 결과를 돌려준다(두 앱), 시험 + 대조 실험 |
| 중간 | 413 이분 뒤 모두 저장돼도 partial | 이분·대조는 문제로 세지 않음 → sent |
| 중간 | 백그라운드 403 무효화 기록을 기다리지 않음 | `CacheGateCheck.flush()` 를 작업 끝에서 기다림 |
| 중간 | 주기 작업 복구가 자정 결과(succeeded)에 묶임 | 자정 결과와 무관하게 재시도 시각이면 recovery, 시험 추가 |
| 낮음 | PC 연결 재사용·이미지 표현 | 위 §1-5·§4 문구를 실제 구현으로 고침 |

재검토(같은 날, 9건 해결·3건 부분 → 추가 반영):
- 401 재전송 전 lease 를 잃으면 **아직 내 것인(in_flight + 내 owner) 행만** retry_wait 로 되돌린다(새 실행이 회수한 행을 덮지 않음). heartbeat 는 요청 간격
  대기 **뒤**, 요청 직전에 한다(일반 배치도 같은 순서). 새 실행 소유 행 시험 + 대조 실험(owner 조건을 빼면 실패).
- 합류 호출: 재귀 대신 반복. manual/midnight/recovery 는 **자기가 도착한 뒤 시작한 enqueue 실행**의 결과를 쓰고(reshare 는 뒤에 시작한 어느 실행이든),
  아니면 앞선 실행이 끝난 뒤 직접 실행한다 — 여러 호출이 와도 실행 하나로 합쳐진다(4개 합류 시험).
- 옛 영수증 재확인은 SQL `GLOB`(UUID 규칙과 같은 소문자 8-4-4-4-12) 한 문장 — 이력을 메모리로 읽지 않는다.
- 자정 실행 중 예외: 내 owner 일 때만 `failed`(사유=예외 종류)로 기록하고 lease 를 푼다(두 앱).

3차 확인(자정 예외 해결, 나머지 부분 → 추가 반영):
- 대조 보류(suspect) 행 정리도 **아직 내 것인 in_flight 행만**(두 앱). 공통 거절 2건째를 pending 으로 바꾸던 것을 없애 보류 정리 한 곳에서 처리. 시험 + 대조 실험.
- PC 영수증 정규식을 완전 일치(`fullmatch` — 끝 개행 불허)로 바꿔 모바일·SQL GLOB 과 같은 집합. 벡터 "receipt with a trailing newline" 추가.
- 기다리는 enqueue 호출(manual/midnight/recovery)이 있으면 새로 시작하는 realtime 실행을 그 트리거로 올린다(`_effective_trigger`/`effectiveTrigger`) —
  realtime 이 연달아 먼저 시작해도 굶지 않는다.
