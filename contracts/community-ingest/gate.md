# 필수 진입 게이트 v1 (K·C)

`CAN_ENTER_MAIN = K && C`. 두 수집 앱(PC·Docker 서버, 모바일 Standalone·Client) 공통. Client 는 **앱 사용자 본인**의 K·C 다.

## 입력
- `config`: 공개 설정 검증 결과(`ok` / `missing` / `placeholder` / `secret_detected` / `conflict`).
- `session`: 로컬 커뮤니티 세션 상태(`none` / `valid` / `reauth_required` / `unreadable`).
- `status`: 마지막 `community-account/status` 응답(없을 수 있음)과 받은 시각 `verified_at`(로컬 단조 시계 기준).
- `now`, 캐시 유효 기간 `ttl = 600초`.
- `invalidated`: cold start·로그인·계정 변경·동의 저장/철회·API 401/403 수신 뒤 status 를 아직 다시 받지 않음.

## 판정 순서 (먼저 걸린 것이 결과)
1. config ≠ ok → `config_invalid` (fail-closed, 복구 화면: 재시도·진단·로그아웃/종료)
2. session = none → `kakao_required`; reauth_required → `kakao_reauth_required`; unreadable → `session_unreadable`
3. status 없음 또는 invalidated 또는 now - verified_at > ttl → `verification_required`(네트워크로 status 를 다시 받음. 실패하면 이 상태로 머물고 **진입 불가**)
4. status.gate.kakao = false → `kakao_required`
5. status.contributor.status ∉ {active, none} → `suspended`
6. status.consent.state ≠ active 또는 policy_version ≠ 앱이 아는 required_version → `consent_required`(state 가 outdated 면 새 정책 안내)
7. 그 밖 → `ok` (can_enter = true)

로컬 prefs·SQLite 에 저장된 "완료" 값만으로는 1~7 을 건너뛰지 않는다. 캐시된 status 는 유효 기간 안에서만 쓴다.
네트워크 장애 때 유효 기간이 남은 성공 캐시는 유지한다(수집이 끊기지 않게) — 단 동의 철회·401/403 을 받으면 즉시 무효.
중앙 ingest 는 이 캐시와 무관하게 저장 트랜잭션 안에서 매번 권한을 다시 확인한다.

## 허용 행동(게이트 미충족에서도 항상 가능)
카카오 인증 시작·콜백 수신·상태 새로고침·계정 변경·로그아웃, 동의 문서 보기·동의 저장, 도움말·개인정보 안내, 앱 종료. PC 는 기존 관리자 로그인·로그아웃·최초 설정.
