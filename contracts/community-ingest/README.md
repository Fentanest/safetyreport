# community-ingest 계약 v1 (정본)

이 폴더가 PC(safetyreport)·모바일(safetyreport-mobile)·중앙(map ingest, auth account)이 공유하는 계약의 정본이다.
다른 저장소는 이 폴더의 파일을 **그대로 복사**해 `contracts/community-ingest/` 에 두고, 테스트에서 `MANIFEST.sha256` 과 해시가 같은지 검사한다.
사본을 고치지 않는다. 바꿀 때는 이 폴더를 먼저 고치고 버전을 올린 뒤 사본을 다시 복사한다.

| 이름 | 값 |
|---|---|
| 계약 | `community-ingest-v1` (`protocol: 1`) |
<| private 신고번호 | event `report_number`: 새 PC·모바일은 항상 전송(null 가능), 구 클라이언트의 필드 생략은 허용. Observation·해시에는 포함하지 않음 |
| 공유 payload 스키마 | `observation-v3` (`observation.schema.json`) — v3 는 `source_agency_code`(원문 기관코드) 추가(2026-09-28). v2(위반법규)·v1 payload 도 서버가 받는다 |
| 필수 동의 정책 버전 | `2026-09-28.2` (위반법규 공개 추가, 2026-09-28. 이전 `2026-09-28.1`은 위반법규 없음, `2026-09-26.1`은 금액 비공개 판). 앱은 동의문을 번들에 넣지 않고 중앙 `policy` 로 받는다(`account-api.md`). 동의문 원본은 이 폴더가 아니라 지도 레포 `contracts/consent/`(복사하지 않음) |
| 초기화 크롤링 버전 | `source-rebuild-2026-09-26.1` |
| 업로드 기준 시간대·시각 | `Asia/Seoul` 00:00 (한국은 DST 없음 → UTC+9 고정 계산) |
| ingest 함수 | `POST {COMMUNITY_SUPABASE_URL}/functions/v1/community-ingest` |
| 계정 함수 | `POST {COMMUNITY_SUPABASE_URL}/functions/v1/community-account/{action}` |

| 파일 | 내용 |
|---|---|
| `canonical-json.md` | 해시용 직렬화 규칙 |
| `observation.md` | 공식 관측 → 공유 DTO 규칙, 완료 판정, event_type |
| `observation.schema.json` | 공유 payload JSON Schema |
| `envelope.schema.json` / `ack.schema.json` | ingest 요청·응답 |
| `account-api.md` | community-account 액션·응답·오류 |
| `gate.md` | K·C 게이트 판정·캐시 |
| `schedule.md` | 00:00 KST due·보충 규칙 |
| `rebuild.md` | 1회 초기화 job 상태기계·보존 범위 |
| `local-store.md` | 앱 쪽 `community.db` 테이블 |
| `vectors/*.json` | 세 언어(Python·Dart·TypeScript)가 같은 결과를 내야 하는 입력·기대값 |
| `MANIFEST.sha256` | 위 파일들의 sha256 |

보안 한계: 이 계약의 출처 표시는 "클라이언트 수집 데이터"다. 서버는 인증·연결·동의를 확인하지만 공식 서버 대조는 하지 않는다.
정상 세션과 연결을 가진 사용자가 다른 프로그램으로 같은 요청을 만드는 것은 구분하지 못한다.
