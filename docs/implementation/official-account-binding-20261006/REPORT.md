# PC 공식 계정 바인딩 — r2 정정 반영

- 작업 위치: `/home/better0101/projects/worktree/pc-account-binding`, `feat/official-account-binding`.
- r1 보존 HEAD: `835ac23`, 기준 커밋: `b840fff`(origin/dev). 이 보고서는 r1 보고서의 정책·검증 결과를 대체한다.
- 직전 REPORT와 `HEAD~1..HEAD` diff, 사용자 지시서·공통 `binding-contract.md`, 모바일 r2 REPORT, 프로젝트 규칙·지속 메모리를 읽고 적용했다.
- 운영 Supabase·Edge·Pages·스토어 접속/배포, push, 커밋, 다른 개발 worktree 수정 없음.

## 변경 파일 (r1 HEAD 대비)

| 파일 | 변경 |
|---|---|
| `services/official_account.py` | 개인 DB 안신 키 상수·읽기·기록·백업 비교 제거, 백업 사본 키 보충 제거, 구서버 누락 허용, config/서버 기준 변경 판단 |
| `services/community_gate.py` | 개인 DB 안신 대조/최초 기록 제거, 구서버 기존 writer 재사용 및 가짜 바인딩 캐시 생성 방지 |
| `core/storage/exchange.py` | 복원 시 안신 검사 제거, 기존 `account_data.refuse_foreign_owner` 유지 |
| `tests/test_official_account_binding.py` | 구서버 정상 진입/연결 재사용, null 등록, 오형식 거절, 개인 DB 미기록, 5분 대조, 백업·실패 보존·재개, 구서버 변경 해제 확인 회귀 |
| `tests/test_account_data.py` | 다른/누락 안신 백업 거절 기대값을 서버·모바일 실제 복원 성공으로 갱신, 카카오 주인·행 수·메타 보존 검증 |
| `tests/test_{storage_exchange,db_upload_paths,community_scheduler_split}.py`, `scripts/dev/db_roundtrip_check.py` | r1에서 추가한 필수 안신 fixture/설정 제거. 기존 모든 테스트 유지(이 파일들은 b840fff 내용으로 복귀) |
| `tests/test_techlog_2026_10_04.py` | 제거된 안신 DB 조회 mock 제거, 기존 주인 검증 유지 |
| `contracts/community-ingest/account-api.md`, `MANIFEST.sha256` | 구서버/null/복원 정책 정렬 및 정본 해시 갱신 |
| `docs/architecture/{community-account,community-gate,data-contracts}.md` | config/서버 대조·개인 DB 미기록·카카오 필수·교환 계약 및 코드 대조 정정 |
| `CHANGELOG.md`, 이 REPORT, `VERIFICATION.txt` | 실제 변경·검증 결과·로그 해시 |

## 계약 결정

1. **개인 DB에 안신 ID 원문·해시를 기록하지 않는다.** 정상 시작/갱신/계정 변경/백업에서 새 안신 메타를 생성하지 않는다. r1 개발 중 남은 `official_account_dataset_key`는 주인 판정에 읽지 않는다. 모바일과 같이 별도 삭제 마이그레이션은 하지 않고 임의 메타 무손실 교환 규칙을 유지한다.
2. **복원·업로드 주인은 카카오만 검사한다.** 같은 카카오면 다른 안신 계정 시절 또는 안신 정보 없는 백업도 허용한다. 카카오 누락·불일치·현재 로그인 미확인, 스키마 불일치·손상 거절은 유지한다. 개인 DB 자료의 안신 소속은 추정하지 않는다.
3. **1:1은 config ID → dataset_key ↔ 서버 status 및 등록 거절로 보장한다.** 기존 시작/60초 scheduler poll, 웹 복귀/5분 확인, 탐색 300초 상한·새 작업 60초 검증을 유지한다. 불일치/선점은 설정에 가두고 크롤·업로드 context를 중단한다.
4. **구서버 필드 누락은 원격 대조만 생략한다.** 카카오/동의/카카오 DB 주인/등록 오류 검사는 그대로다. 기존 연결을 재사용하며 서버가 주지 않은 `official_account` 필드를 캐시에 만들지 않는다.
5. **명시적 `{dataset_key:null,bound_at:null}`은 미바인딩**으로 정상 진입·현재 config의 연결 등록을 시도한다. 안신 정보가 없는 개인 DB는 차단 근거가 아니다. `official_account:null`, 잘못된 dataset 또는 필수 키 누락은 오형식으로 차단한다. 모바일 REPORT와 같은 정책이다.
6. **계정 변경 절차는 유지한다.** 기존 config와 후보 ID 변경 또는 서버 불일치로 판단하며, 서버 바인딩 계정으로 되돌리는 저장은 삭제 없이 가능하다. 경고 → SQLite backup/무결성 검사 → contributions-delete/released 확인 → 기존 개인 자료 초기화·community dataset 회전·대기 크롤 제거 → 새 config → 새 connections 순서다. 구서버라도 삭제 응답에 `official_account_released:true`가 없으면 초기화/변경을 완료하지 않는다.
7. **재개 기록은 개인 DB 밖의 기존 JSON이다.** `official-account-change.json`(0600)은 대상 해시·커뮤니티 사용자·단계·백업 경로만 담는다. 원문 ID/비밀번호/토큰은 기록하지 않으며 DB 소유자 판정에 사용하지 않는다. 같은 카카오·같은 대상의 확인 저장으로 재개한다. 백업 실패/해제 미확인 시 개인 자료를 보존한다.
8. **카카오 연동은 필수다.** 미연동자는 기존 `/onboarding/community`에 머물며 계속 버튼이 숨겨진다. 보호 페이지는 온보딩으로 이동하고 작업 API는 거절된다. 실제 관리자 로그인 후 카카오 로그아웃한 브라우저에서 홈·설정 재진입 차단과 계속 버튼 숨김을 확인했다. 미연동자에게 익명 status를 요청하지 않는다.
9. 클라우드 장애 시 성공 캐시로 진입하지 않으며 기존 장애 페이지, 25초 HTTP timeout, 2/5/10초 자동 3회·수동 재시도를 유지한다.

## 검사 명령과 결과

모든 Python 검사에 별도 `SAFETYREPORT_DATA_DIR=$(mktemp -d /tmp/pc-binding-….XXXXXX)`를 지정했다. 전용 `.venv`(Python 3.14.6)를 재사용했으며 공용 환경은 변경하지 않았다. 원문 로그·브라우저 실행 스크립트·캡처는 `.agent-runs/official-binding-r2/`에 보존한다.

| 검사 | 명령(임시 DATA_DIR 공통) | 결과 |
|---|---|---|
| 최종 전체 | `PYTHONWARNINGS=ignore::ResourceWarning .venv/bin/python -m unittest discover -s tests -p 'test_*.py'` | **842 실행 / 830 통과 / 7 오류 / 기존 5 skip / assertion 실패 0**, 종료 1, 216.373초 |
| 관련 회귀 | `PYTHONPATH=tests PYTHONWARNINGS=ignore::ResourceWarning .venv/bin/python -m unittest test_official_account_binding test_community_gate test_account_data test_storage_exchange test_db_upload_paths test_community_scheduler_split test_settings_service test_techlog_2026_10_04 test_process_shutdown_recovery` | **166 통과 / 0 실패 / 0 skip**, 종료 0. 바인딩 26개 포함 |
| Chromium | `.venv/bin/python .agent-runs/official-binding-r2/browser_server.py` + `node .agent-runs/official-binding-r2/browser-check.cjs chromium` | **9 흐름 통과**, JS 오류 0, 검사 종료 0 |
| Firefox | 새 fixture 서버 + `node .agent-runs/official-binding-r2/browser-check.cjs firefox` | **9 흐름 통과**, JS 오류 0, 검사 종료 0 |
| 라우트·DOM | `python3 scripts/dev/web_contract_inventory.py` 변경 전후 `cmp` | 동일, 제거 0 |
| DOM 존재 | `python3 scripts/dev/web_contract_inventory.py --check-ids mainSettingsForm fld_username officialAccountConfirm cloudRetry cloudRetryStatus obContinueBtn` | **6 통과** |
| 정적/계약 | `git diff --check`, 핵심 Python 5파일 AST, 기존 바인딩 JS 3개 `node --check`, account-api SHA256/manifest 대조 | 통과 |

전체 7개 오류의 테스트 ID와 누락 파일은 기준 커밋의 7개와 동일함을 프로그램으로 대조했다. 전체 스위트는 **실패**로 기록하며 skip은 통과 수에 포함하지 않았다. 테스트 삭제·skip 추가 없음. 정책이 바뀐 테스트는 허용/거절 결과를 새 규칙으로 바꾸고 실제 복원·카카오 검사·개인 자료 보존/초기화 assertion을 유지/확장했다.

브라우저 9개 흐름: 구서버 정상 진입, 명시적 미바인딩 새 등록, 변경 경고 취소, 승인 후 백업/삭제/새 연결, config 직접 수정 감지·온보딩 우회 차단, 장애 페이지/자동 재시도 3회, 수동 복구, 선점 안내/설정 제한, 카카오 미연동 온보딩 제한. 관리자 인증·CSRF를 우회하지 않았으며 전용 loopback 포트 18796을 사용했다. 1440×900 라이트 확인창·장애 화면과 390×844 다크 장애 화면·미연동 화면을 캡처했고 확인창/좁은 장애 화면/미연동 화면을 시각 검토했다. Chromium 확인창 최초 캡처는 전환 도중이어서 확정 시각 판정에는 애니메이션 완료 후 찍은 Firefox 캡처를 사용했다. 검사 후 두 서버 실행을 각각 Ctrl-C로 중단했다.

원문 로그: `full-tests.log`, `baseline-full.log`, `targeted-final.log`, `browser-{chromium,firefox}.log`. 캡처: `{chromium,firefox}-{confirm-light,cloud-light,cloud-dark-mobile,kakao-required}.png`. 별도 요약은 [VERIFICATION.txt](VERIFICATION.txt)에 보존한다.

### 기준 커밋 실제 재현

원본 저장소에서 `git worktree add --detach /tmp/pc-binding-r2-baseline-b840fff b840fff`를 시도했으나 원본 `.git/worktrees` 읽기 전용으로 실패했다. 우회 권한을 요청하거나 원본 권한을 바꾸지 않고 다음과 같이 `/tmp`에 분리한 로컬 Git 메타데이터로 **실제 worktree 체크아웃**을 만들었다.

```sh
git clone --bare --shared . /tmp/pc-binding-r2-baseline.git
git --git-dir=/tmp/pc-binding-r2-baseline.git worktree add --detach /tmp/pc-binding-r2-baseline-b840fff b840fff
# cwd=/tmp/pc-binding-r2-baseline-b840fff, 위 임시 DATA_DIR 지정
PYTHONWARNINGS=ignore::ResourceWarning /home/better0101/projects/worktree/pc-account-binding/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
git --git-dir=/tmp/pc-binding-r2-baseline.git worktree remove /tmp/pc-binding-r2-baseline-b840fff
```

결과: **813 실행 / 801 통과 / 7 오류 / 기존 5 skip / assertion 실패 0**, 종료 1, 186.370초. 실행 후 임시 worktree를 제거했고 `worktree list`에는 bare 저장소만 남았다. stash/기존 worktree 체크아웃 전환 없이 검사했다.

7개 모두 `test_community_client_rules`의 FileNotFoundError다: `AccountErrorTest` 2개(`vectors/account-errors.json`), `DeviceLabelTest` 1개(`vectors/device-label.json`), `GateTimingTest` 2개(`vectors/gate-timing.json`), `ManifestTest` 1개(`README.md`), `StatusDtoTest` 1개(`vectors/status-dto.json`). 모두 `contracts/community-client/` 아래 누락이다. r1의 기존 오류 주장을 이번에는 기준 커밋 전체 실행으로 확인했다. 누락 계약을 추정 작성하거나 테스트 삭제/skip/기대값 완화로 통과시키지 않았다.

## 미확인·위험

- 운영 API·실카카오·실안신 크롤·실업로드는 미검증이다. 브라우저는 실제 앱/관리자 인증/라우팅/SQLite와 loopback 가짜 계정 API를 사용하고 uploader/manifest는 테스트 대역이다. 서버 1:1 트랜잭션·backfill·운영자 해제·ingest 방어의 실제 구현/배포는 auth/map 검토 범위다.
- 구서버 정상 진입은 허용하지만 구서버 자체에는 새 양방향 1:1 보장이 없다. 해제 확인 없는 구서버에서 계정 변경은 완료할 수 없다.
- 같은 카카오의 다른 안신 시절 백업 자료가 로컬에 표시되는 것은 r2에서 허용한 동작이다. 복원 자료를 공식 수집 응답으로 위장해 공유하는 경로는 추가하지 않았다.
- 실제 PC↔모바일 Dart 전 컬럼 왕복, Windows/macOS/PyInstaller/Docker, WebKit·실기기·전체 접근성은 NOT_RUN이다. 개인 DB 스키마/일반 교환 규칙은 변경하지 않았고 모바일에 안신 메타 기록을 요구하지 않는다.
- 실제 디스크 고장/전원 차단은 미검증이다. 단계별 예외·백업 보존·재개는 테스트했지만 원격 삭제를 서버에서 롤백할 수는 없다. 기존 JSON/백업 보존이 필요하다.
- 구현자가 수행한 검증이며 총괄의 독립 검토가 남아 있다. **커밋하지 않았다.**
