# 1회 초기화 크롤링 v1 (`source-rebuild-2026-09-26.1`)

## 필요 판정
`rebuild_jobs` 에 `(required_version, local_dataset_id, source_account_namespace)` 의 `completed` 또는 `completed_with_gaps` 행이 없으면 필요. 새 설치도 같은 job 하나로 첫 전체 수집을 겸한다.
중앙 전역 플래그로 로컬 완료를 대신하지 않는다. 초기화 완료는 K·C 의 증거가 아니다.

## 안내 문구(기준)
> 이번 업데이트에서는 신고 데이터 저장 구조가 변경되어 초기화 크롤링을 한 번 진행해야 합니다. 확인을 누르면 안전신문고에서 신고 내역을 다시 수집하고 새 저장 구조를 구성합니다. 신고내용 공유에는 이번에 수집한 사용자 수정 전 값이 사용됩니다.

이어서: 보존 항목(관리자·공식 계정 설정, 커뮤니티 연결·동의, 앱 설정, 수정한 값·메모·감시목록·중복 판단, 지오코딩 캐시, 업로드 대기 자료), 시작 전 자동 백업(경로 표시), 단계, 소요시간은 신고 수·사이트 응답에 따라 다름(보장 없음). 주 버튼 `확인 — 초기화 크롤링 시작`.

## 상태기계
```
required ──(K·C·공식 계정·필수 권한 없음)──▶ prerequisites_required ──(충족)──▶ awaiting_confirmation
awaiting_confirmation ──확인──▶ preparing_backup ──▶ running ──▶ validating ──▶ committing ──▶ completed
running/validating ──영구 누락만 남음·사용자 수락──▶ committing ──▶ completed_with_gaps
preparing_backup/running/validating ──오류──▶ failed ──계속──▶ (같은 run_id) running
running ──철회·로그아웃·권한 상실·사용자 일시정지──▶ paused ──계속──▶ running
```
- `preparing_backup`: PC = sqlite3 backup API → `<data>/backups/pre-rebuild-<run_id>.db` + `PRAGMA integrity_check`; 모바일 = `VACUUM INTO '<앱폴더>/backups/pre-rebuild-<run_id>.db'` + integrity_check. 실패·공간 부족 → failed(개인 DB 무변경).
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
- 프로세스 재시작·재부팅: `running` + 만료 lease → 같은 run_id 로 자동 재개(같은 run 범위만). 새 파괴적 초기화를 조용히 시작하지 않는다.
- 업로드는 초기화 중에도 계속(실시간 capture 이벤트).

## 화면 표시
`백업 준비 → 전체 신고 확인 → 상세 수집 → 새 저장 구조 반영 → 완료`, 성공·실패·재시도 대기·업로드 대기 건수 분리. 완료 후 `초기화 완료 · 중앙 전송 대기 N건` 또는 `중앙 저장 완료 · 지도 반영 중`(held)/`지도 반영됨`(published). 과장된 진행률 금지.
