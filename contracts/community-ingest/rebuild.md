# 1회 초기화 크롤링 v1 (`source-rebuild-2026-09-26.1`)

## 필요 판정
`rebuild_jobs` 에 `(required_version, local_dataset_id, source_account_namespace)` 의 `completed` 또는 `completed_with_gaps` 행이 없으면 필요.
- **새 설치는 필요 없음**(2026-09-27 결정 — 처음 실행은 안내 없이 평소대로): 개인 DB 에 신고가 0건이고 이전 버전 DB 를 비운 기록(아래 `legacy_reset`)이 없으면 새 설치다.
  공식 계정이 있으면 그 범위 키에 완료 기준선 행(`state=completed`, `confirmed_at='fresh_install'`, `counts_json={"baseline":"fresh_install"}`)을 한 번 적어
  첫 일반 수집으로 신고가 생긴 뒤에도 다시 필요해지지 않게 한다(빈 목록 완료와 같은 뜻). 공식 계정이 없으면 적지 않고 필요 없음으로 본다.
  개인 DB 를 읽지 못하면 새 설치로 보지 않는다(필요). 첫 수집은 일반 크롤·동기화가 하고 capture 는 평소대로 `report_latest` 에 쓴다.
중앙 전역 플래그로 로컬 완료를 대신하지 않는다. 초기화 완료는 K·C 의 증거가 아니다.

## 안내 문구(기준)
> 이번 업데이트에서는 신고 데이터 저장 구조가 변경되어 초기화 크롤링을 한 번 진행해야 합니다. 확인을 누르면 안전신문고에서 신고 내역을 다시 수집하고 새 저장 구조를 구성합니다. 신고내용 공유에는 이번에 수집한 사용자 수정 전 값이 사용됩니다.

이어서: 보존 항목(관리자·공식 계정 설정, 커뮤니티 연결·동의, 앱 설정, 수정한 값·메모·감시목록·중복 판단, 지오코딩 캐시, 업로드 대기 자료), 시작 전 자동 백업(경로 표시), 단계, 소요시간은 신고 수·사이트 응답에 따라 다름(보장 없음). 주 버튼 `확인 — 초기화 크롤링 시작`.

이전 버전 DB 를 비운 사용자(`legacy_reset` 있음)에게는 보존 항목 문단 대신: 이전 DB 는 새 구조로 옮기지 않았고 전체를 백업(경로 표시)한 뒤 신고 자료를 비웠음,
남긴 것(관리자 계정·API 키(서버)·감시목록·지도 좌표 캐시), 신고는 초기화 크롤링으로 다시 수집함, 예전에 직접 수정한 값·중복 판단·메모는 옮기지 않음, 이전 DB 는 이 버전에서 가져올 수 없음.

## 이전 버전 개인 DB (2026-09-27, 이번 릴리스)
이번 릴리스는 이전 버전 개인 DB 를 새 구조로 옮기지 않는다(서버·모바일의 DB 업데이트 로직을 주석 처리해 비활성). 한 번 초기화 크롤링으로 다시 채운다.
- 이전 버전 = 서버 `PRAGMA user_version` < 서버 스키마 버전(표가 하나라도 있을 때), 모바일 저장 버전 1 이상 < 앱 `dbVersion`.
- 첫 실행(서버 시작·모바일 DB 열기) 때: 통째로 백업(WAL 에만 있는 쓰기까지 담는 방법 — 서버 sqlite backup API → `<data>/backups/legacy_v<옛 버전>_<시각>.db`,
  모바일 일관된 사본(`LocalDbService.copyDatabaseConsistent`: 새 파일에 원본을 ATTACH 해 한 읽기 트랜잭션으로 스키마·표·`sqlite_sequence`·`user_version` 을 옮기고 표별 행 수·integrity_check 확인 — `VACUUM INTO` 는 SQLite 3.27 부터라 Android 7~10 에서 실패해 쓰지 않는다) → `<db>.legacy_v<옛 버전>.<epoch ms>.bak`, 둘 다 `PRAGMA integrity_check`) → community dataset 선회전(실패하면 비우지 않음) →
  파일을 바꾸지 않고 그 자리에서 한 트랜잭션으로 비우고 지금 스키마로 다시 만든다(서버: 남길 표 외 DROP·CREATE, 모바일: 남길 자료를 읽어 둔 뒤 전부 DROP·CREATE·다시 넣기).
  양쪽 모두 쓰기 잠금(`BEGIN IMMEDIATE`)을 먼저 잡고 그 안에서 버전을 다시 확인한 뒤 백업·선회전·비우기를 한다 — 동시에 두 번 시작해도 두 번째는 아무것도 하지 않고,
  그 사이 다른 연결의 쓰기는 조용히 사라지지 않고 잠김 오류로 실패한다. 열린 DB 의 WAL 삭제·파일 이름 교체는 하지 않는다.
  크롤러 직접 실행(`start.py`, `--reset` 포함)은 이전 버전 DB 면 무엇이든 바꾸기 전에 멈춘다(비우기는 서버 시작만 한다).
- 모바일 데모(심사용 합성 데이터, 별도 DB 파일)는 초기화 대상이 아니다(판정·안내·동기화 차단 모두 없음). 이전 버전 데모 DB 를 비울 때 실제 계정의
  community dataset 은 선회전하지 않는다.
- 남기는 것(구조가 지금과 같을 때만, 옮기는 코드 없음): 감시목록, 지오코딩 캐시. 서버는 관리자·API 키도 — 구조가 다르면 비우지 않고 멈춘다.
- 기록 `sync_meta[legacy_reset]` = `{from_version, backup, kept, dropped, at}`. 이 기록이 있으면 신고가 0건이어도 초기화가 필요하다.
  상태 응답(`GET /settings/community/rebuild`, `/api/v1/community/rebuild`)에 `legacy_reset`(추가 필드, 없으면 null).
- 가져오기·복원(서버 복원·모바일 서버 DB 가져오기·앱 백업 복원)은 스키마 버전이 정확히 같은 DB 만 받는다. 낮으면 `이전 버전 … DB(스키마 N)는 가져올 수 없습니다`,
  높으면 `더 새 버전 … DB(스키마 N)는 가져올 수 없습니다` — 무엇이든 바꾸기 전에 멈춘다.

## 상태기계
```
required ──(K·C·공식 계정·필수 권한 없음)──▶ prerequisites_required ──(충족)──▶ awaiting_confirmation
awaiting_confirmation ──확인──▶ preparing_backup ──▶ running ──▶ validating ──▶ committing ──▶ completed
running/validating ──영구 누락만 남음·사용자 수락──▶ committing ──▶ completed_with_gaps
preparing_backup/running/validating ──오류──▶ failed ──계속──▶ (같은 run_id) running
running ──철회·로그아웃·권한 상실·사용자 일시정지──▶ paused ──계속──▶ running
```
- `preparing_backup`: PC = sqlite3 backup API → `<data>/backups/pre-rebuild-<run_id>.db` + `PRAGMA integrity_check`; 모바일 = 같은 일관된 사본 → `<앱폴더>/backups/pre-rebuild-<run_id>.db` + integrity_check(2026-09-27 전에는 `VACUUM INTO` 였으나 Android 7~10 에서 실패). 실패·공간 부족 → failed(개인 DB 무변경).
- `running`: 이번 run 의 목록 수집이 **전 페이지 성공**이어야 `list_complete=1` 과 함께 목록 ID 를 `rebuild_items(pending)` 으로 등록(부분 실패 → failed, 0건 성공 금지). 상세는 pending·failed_retryable 만(checkpoint). 각 상세는 일반 수집과 같은 저장 경로(capture → 개인 저장, override 보존). PC 명령 `--force --rebuild <run_id>`, 모바일 `SyncEngine(fullSync, rebuildRunId)`.
  - 목록에서 사라진 기존 행은 **삭제하지 않는다**(orphan, 건수만 counts 에).
  - 상세 오류 분류: 네트워크·5xx·타임아웃 → failed_retryable(최대 5회), 접근 거절·삭제된 원본(사이트가 명확히 없음을 응답) → failed_permanent, 로그인 실패·토큰 만료 → run 전체 paused(auth).
- `validating`: list_complete=1 ∧ pending·failed_retryable 0. failed_permanent 가 있으면 화면에 누락 N건과 ID 범위를 보여 주고 사용자가 수락해야 `completed_with_gaps`.
- `committing`(community.db 한 트랜잭션): staging 행을 `report_latest` 에 **upsert(병합)** — staging 에 없는 신고(무변경은 staging 에 기존 포인터가 들어 있고, 영구 실패·목록 부재는 staging 에 없음)는 기존 행을 그대로 둔다(carry-forward, 삭제 없음). `source_generation` 증가, job completed, 완료 시각. 개인 DB 는 이미 제자리 갱신됨.
- 시작 때(`running` 진입 전) 중앙 manifest 전 페이지로 `server_completed` 를 새로 받는다(재설치·writer 전환 뒤 정정 누락 방지). 실패하면 `prerequisites_required:manifest_unavailable` 로 머물고 크롤을 시작하지 않는다.
- 영구 실패 item 은 그때의 목록 라벨을 `last_list_label` 에 남긴다. 이후 목록 라벨이 바뀌면 일반 증분이 다시 조회한다.
- 정상 인증의 빈 목록(총 0건, 목록 탐색 완료) → completed.

## 동시성·재개
- 확인 연타·여러 브라우저·Client·다중 worker: `rebuild_one_active` unique + `leases('rebuild')`. 이미 있으면 그 run 을 반환.
- 초기화 필요·진행 중에는 다른 크롤(수동·예약·큐) 시작을 409 `COMMUNITY_REBUILD_REQUIRED` 로 거절. 조회·설정·도움말은 가능.
  모바일 Standalone 은 일반 동기화(`SyncEngine` 수동·공유 대기열 처리)를 같은 판정으로 막는다(판정이 실패해도 막음). Client 는 서버 상태가 `required=false` 면 안내 화면에 머물지 않는다.
- 같은 run 의 재시도(`failed`·`paused` → 계속)는 사전 백업을 같은 이름으로 다시 만든다(지난 사본을 지운 뒤 새로, 무결성 검사는 만든 사본에서).
- 프로세스 재시작·재부팅: `running` + 만료 lease → 같은 run_id 로 자동 재개(같은 run 범위만). 새 파괴적 초기화를 조용히 시작하지 않는다.
- 업로드는 초기화 중에도 계속(실시간 capture 이벤트).

## 화면 표시
`백업 준비 → 전체 신고 확인 → 상세 수집 → 새 저장 구조 반영 → 완료`, 성공·실패·재시도 대기·업로드 대기 건수 분리. 완료 후 `초기화 완료 · 중앙 전송 대기 N건` 또는 `중앙 저장 완료 · 지도 반영 중`(held)/`지도 반영됨`(published). 과장된 진행률 금지.
