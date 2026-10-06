# PC 공식 계정 바인딩 — 3.0.0.2-rc1 릴리스 후보

이 문서는 **로컬 RC 검증 결과와 총괄의 승인 후 실행 절차**다. 배포 완료 기록이 아니다. 기준은 `feat/official-account-binding`, HEAD `ec570bc`(r1 `835ac23` 포함) 위의 미커밋 변경이다. fetch/rebase/커밋/태그/원격 push/워크플로 실행/이미지 업로드는 수행하지 않았다.

## 버전과 변경 범위

- 실제 HEAD와 작업 트리의 시작 VERSION은 `3.0.0.1`이었다. 지시서의 `3.0.0.0-dev`는 이전 상태이며, `git log -- VERSION`에서 `336095a`의 정식 버전 변경을 확인했다. 로컬 최신 태그는 `v3.0.0.1`이다(원격 조회 없음).
- 현재 RC VERSION: **`3.0.0.2-rc1`**. 후보 식별 태그명은 **`v3.0.0.2-rc1`**, 최종 정식 후보는 VERSION **`3.0.0.2`** / 태그 **`v3.0.0.2`**다. 어느 태그도 이번에 생성하지 않았다.
- 최근 `3.0.0.0 → 3.0.0.1` 및 과거 4자리 증가 관례를 이어가고, `core/utils/updater.py::_version_gt`가 지원하는 `rc` 접미사를 붙였다. 과거 RC 태그 관례는 확인되지 않았으므로 `-rc1`은 이번에 제안·적용한 표기다. 실제 함수에서 `3.0.0.1 < 3.0.0.2-rc1 < 3.0.0.2`를 검증했다. DB 스키마·모바일 버전은 변경하지 않았다.
- 바인딩 r2 구현은 보존했다. config/서버 dataset 대조, 변경 경고 → 백업 → 공유 삭제·바인딩 해제 → 개인 자료 초기화 → 새 연결, 클라우드 실패 차단·재시도, 카카오 주인 검사를 포함한다. **개인 DB에 안신 ID/해시를 새로 저장하지 않는다.** 같은 카카오의 다른/누락 안신 백업 허용 정책도 유지한다.
- 새 변경은 VERSION/CHANGELOG, 누락 계약 복구와 `.gitignore` 예외, 이 문서·화면 증거·재실행 도구·환경 매트릭스다. 제품 바인딩 코드·릴리스 워크플로·build.sh는 수정하지 않았다.
- 서버 운영 반영(auth `793aa9d`, map `56168b9`, account v8 / ingest v9)은 **총괄이 제공한 확인 사항**이다. 이번 검증은 운영 로그인·운영 API·실데이터를 사용하지 않았다.

## 기존 7개 오류 복구 근거

`tests/test_community_client_rules.py`가 읽는 `contracts/community-client/`에 manifest만 추적되어 있었다. `.gitignore`의 `*.md` / `*.json`에서 이 폴더만 예외가 빠져 문서 2개·벡터 4개가 누락됐다.

`/home/better0101/projects/worktree/mobile-account-binding/contracts/community-client/`에서 **현재 PC manifest 6개 SHA-256과 모두 일치하는 사본**을 찾아 그대로 복구했다. manifest를 재작성하거나 벡터를 추정 생성하지 않았다. auth 작업 트리의 사본은 일부 해시가 달라 복구 원본으로 쓰지 않았다. `.gitignore`에 이 계약 경로의 md/json 추적 예외를 추가했고 복구 파일이 ignore되지 않음을 확인했다.

r2의 기준 `b840fff` 전체 재현(7개 동일 FileNotFoundError)은 [REPORT.md](REPORT.md)에 보존되어 있다. 이번에는 복구 후 전체 테스트에서 7개가 모두 통과했다. 테스트 삭제·skip 추가·assert 완화 없음.

## 검사 결과

환경: Linux x86_64, Python 3.14.6(작업 트리 `.venv`), Node 22.17.1, Playwright 1.63.0, Docker 29.8.1, PyInstaller 6.22.3. PyInstaller는 이 작업 트리의 `.venv`에만 설치했다. 합성 데이터 루트는 `.agent-runs/official-binding-rc/`; Python 테스트와 패키징 기동은 `SAFETYREPORT_DATA_DIR` 및 `SAFETYREPORT_FIXTURE_MODE=1`을 사용했다.

| 검사 | 결과 | 근거 |
|---|---|---|
| 전체 unittest | **passed: 842 실행 / 837 통과 / 기존 5 skip / 실패·오류 0**, 190.738초 | `full-tests.log` |
| 관련 회귀 | **passed: 173 / 173**, 89.390초 | `targeted.log`; 바인딩 26 포함 |
| Chromium | **passed: 9개 흐름**, JS 오류 0 | `browser-chromium-verified.log`, 백업 증명 |
| Firefox | **passed: 9개 흐름**, JS 오류 0 | `browser-firefox-verified.log`, 백업 증명 |
| 계약 SHA·버전 순서·git diff 공백·bash 구문 | **passed** | 기존 manifest 6개 일치; `_version_gt`; `git diff --check`; `bash -n build.sh` |
| Docker linux/amd64 | **passed: 로컬 이미지 생성 / fixture HTTP smoke** | build/image-assets/http 로그, 아래 이미지 ID |
| Linux x64 PyInstaller | **passed: 실행파일·ZIP 생성 / fixture HTTP smoke** | frozen-build/http 로그, 아래 해시 |
| Windows / macOS x64·arm64 / Linux ARM / UPX | **not-run** | 해당 native runner·원격 workflow 미실행 |
| 운영 API·실계정·실크롤·업로드 | **not-run** | 이번 범위에서 금지 |

5 skip은 로컬 Supabase 스택 설정이 없는 기존 integration 테스트다. unittest에는 기존 SQLite ResourceWarning이 출력되지만 오류·실패는 없다. 브라우저 fixture는 실제 앱·관리자 인증·CSRF·SQLite와 loopback fake account 서버를 사용한다. uploader/manifest는 대역이므로 실제 공유 업로드의 통과를 뜻하지 않는다(대역의 `start_background` 부재 경고 포함).

브라우저별 9개 흐름은 구서버 누락 허용, 명시적 미바인딩 등록, 변경 경고 취소, 승인 후 백업/초기화/새 계정 저장, config 직접 수정 시 설정 제한·온보딩 우회 차단, 오프라인 화면·자동 재시도 종료, 수동 복구, 선점 안내·설정 제한, 카카오 미연동 제한이다. 백업은 `PRAGMA integrity_check=ok`, 원본 제목 24건·상세 12/6/6건 보존, 현재 DB의 같은 표는 모두 0건, 개인 DB 안신 키 없음까지 확인했다.

화면은 [evidence/](evidence/)의 `{chromium,firefox}-{confirm-light,mismatch-settings,cloud-light,cloud-dark-mobile,kakao-required}.png` 10장이다. 1440×900 라이트 확인창·불일치·장애와 390×844 다크 장애·라이트 미연동을 촬영했다. 모달의 opacity=1 확인 후 촬영하며 전역 networkidle/고정 sleep으로 화면 준비를 판정하지 않는다. 확인창·불일치·모바일 장애 화면을 직접 시각 검토했다.

원문 로그·큰 산출물은 `.agent-runs/official-binding-rc/`에 유지하고, 공유 가능한 요약·해시·재현 스크립트는 [evidence/RESULTS.txt](evidence/RESULTS.txt)에 정리한다. 실행 중 한 Chromium 재검증과 fixture가 SIGTERM(143)으로 중단되어 성공에 포함하지 않았다. 원인은 확정하지 않았으며, 서버 시작/종료를 관리하는 `run-browser.py`로 재실행하여 정상 종료 0과 백업 검증을 확보했다.

## 로컬 빌드 산출물과 범위

- Docker 태그: `safetyreport:official-binding-3.0.0.2-rc1` (로컬 전용).
- 이미지 ID: `sha256:66743593acc97f13aaa4574ce4ba4a63c3253fac992c288404b940b293a1441f`, `linux/amd64`.
- 실행파일: `dist/mysafetyreport/mysafetyreport` (동봉 `run.sh`, `_internal/` 필요).
- ZIP: `.agent-runs/official-binding-rc/mysafetyreport-linux-3.0.0.2-rc1.zip`.
- ZIP SHA-256: `c8524d7908c67910a9aca4f15c272a7e95e5bb2e8d631fad47c7d980150d00a5`.
- 실행파일 SHA-256: `fcdbbdbda669a57cc668ed65862ccb427fdfd09787b7990b12169242e0e6b532`.

Docker는 문서의 `tools/docker/compose.devtest.yml`에 자체 이미지 override를 더하고 project `safetyreport-binding-rc`, loopback `18816`, 전용 named volume을 사용했다. 공용 Docker CLI 설정 쓰기가 read-only로 실패해 `DOCKER_CONFIG=$PWD/.agent-runs/official-binding-rc/docker-config`로 격리한 후 빌드했다. 공용 builder 설정·운영 컨테이너는 변경하지 않았다.

실제 **이미지**를 `--network none --entrypoint python`으로 검사해 `.venv/node_modules/.agent-runs/docs/tests/tools/scripts/dev` 제외 및 templates/static/공개설정/VERSION 포함을 확인했다. devtest 실행 컨테이너의 `scripts/dev`는 문서대로 읽기 전용 마운트이므로 이미지 내용 검사와 구분했다. 검사 후 자기 project의 `down -v`로 컨테이너·볼륨을 정리했다. 이미지는 재검토용으로 남겨 두었다.

두 패키징 형태 모두 `/health`, `/login`, 바인딩 settings/retry JS, 로고 HTTP 200/MIME, 정상 관리자 로그인 303과 인증 후 community 온보딩 게이트 200을 확인했다. source의 fake account를 번들에 주입하지 않았으므로 패키징의 **로그인 후 전체 9개 동선 통과를 주장하지 않는다**. UPX 압축, 한글 설치 경로·재기동·실기기 smoke는 이번에 실행하지 않았다. 기본 공개 설정 검증은 두 실제 빌드 경로를 통해 실행됐다. `build_exe.py`에 별도 검증/드라이런 옵션은 없다.

`build.sh`는 두 분기 모두 `docker buildx ... --push`를 실행하며 드라이런이 없다. 따라서 구문 검사만 수행했다. 특히 정식 분기는 빌드가 끝난 **뒤** VERSION을 쓰므로 최신 VERSION을 먼저 커밋하고 정식 workflow를 사용하는 절차가 필요하다.

## 재현 명령 (저장소 루트)

```sh
SAFETYREPORT_DATA_DIR="$PWD/.agent-runs/official-binding-rc/unit-data" SAFETYREPORT_FIXTURE_MODE=1 \
  .venv/bin/python -m unittest discover -s tests -p 'test_*.py'
SAFETYREPORT_DATA_DIR="$PWD/.agent-runs/official-binding-rc/target-data" SAFETYREPORT_FIXTURE_MODE=1 PYTHONPATH=tests \
  .venv/bin/python -m unittest test_official_account_binding test_community_gate test_account_data \
  test_storage_exchange test_db_upload_paths test_community_scheduler_split test_settings_service \
  test_techlog_2026_10_04 test_process_shutdown_recovery test_community_client_rules

# loopback 18806에서 브라우저별 새 합성 DB. 각 명령이 자기 fixture를 시작·종료한다.
.venv/bin/python docs/implementation/official-account-binding-20261006/evidence/run-browser.py chromium
.venv/bin/python docs/implementation/official-account-binding-20261006/evidence/run-browser.py firefox

# 외부 서비스 차단 상태에서 실제 Linux 실행파일 생성
SAFETYREPORT_DATA_DIR="$PWD/.agent-runs/official-binding-rc/build-data" SAFETYREPORT_FIXTURE_MODE=1 \
  PYINSTALLER_CONFIG_DIR="$PWD/.agent-runs/official-binding-rc/pyinstaller-cache" \
  .venv/bin/python scripts/build/build_exe.py
```

브라우저 재현은 기존 `tools/web-tests/node_modules`와 설치된 Chromium/Firefox를 요구한다. 처음 준비할 때는 레포 문서대로 해당 디렉터리에서 `npm ci`를 사용한다. 로컬 Docker 빌드에 사용한 override는 `services.app.image: safetyreport:official-binding-3.0.0.2-rc1` 한 항목이다.

```sh
mkdir -p .agent-runs/official-binding-rc/docker-config
DOCKER_CONFIG="$PWD/.agent-runs/official-binding-rc/docker-config" SR_DOCKER_PORT=18816 \
  docker compose -f tools/docker/compose.devtest.yml \
  -f docs/implementation/official-account-binding-20261006/evidence/docker-override.yml -p safetyreport-binding-rc build
SR_DOCKER_PORT=18816 docker compose -f tools/docker/compose.devtest.yml \
  -f docs/implementation/official-account-binding-20261006/evidence/docker-override.yml -p safetyreport-binding-rc up -d --no-build
.venv/bin/python docs/implementation/official-account-binding-20261006/evidence/http-smoke.py http://127.0.0.1:18816
SR_DOCKER_PORT=18816 docker compose -f tools/docker/compose.devtest.yml \
  -f docs/implementation/official-account-binding-20261006/evidence/docker-override.yml -p safetyreport-binding-rc down -v
```

## 총괄의 승인 후 릴리스 절차 (이번에는 실행하지 않음)

### 1. RC 검토와 OS별 manual 빌드

1. 총괄이 diff·복구 계약 해시·결과를 독립 검토하고 커밋한다. `.git` 읽기 전용인 이번 에이전트는 커밋하지 않았다. 사용자 승인 뒤 총괄 환경에서 RC 브랜치 커밋을 원격에 반영한다.
2. 같은 RC commit/ref로 아래 **manual** workflows를 실행한다. 이들은 `workflow_dispatch` 전용, `contents: read`, ZIP artifact 보존 **1일**이며 release 생성·이미지 push 단계가 없다. 다만 GitHub artifact 업로드 자체가 있으므로 사용자 최종 승인 후 실행한다.

```sh
gh workflow run build-windows-manual.yml --ref feat/official-account-binding
gh workflow run build-linux-manual.yml --ref feat/official-account-binding
gh workflow run build-macos-x64-manual.yml --ref feat/official-account-binding
gh workflow run build-macos-arm64-manual.yml --ref feat/official-account-binding
# 각 workflow의 새 run ID와 head SHA를 확인한 후
# gh run watch RUN_ID --exit-status
# gh run download RUN_ID --dir rc-artifacts/플랫폼
```

3. runner는 Windows x64 `233`, Linux x64 `235`, macOS x64 `236`, macOS arm64 `macos-15`다. 공개 community Variables 3개와 macOS Python/Xcode 요건은 해당 YAML을 따른다. 플랫폼별 fixture 로그인·정적 자산·바인딩 흐름을 검사하고 artifact를 1일 안에 내려받아 해시와 SHA를 남긴다.
4. `v3.0.0.2-rc1`은 현재 후보 식별명이며 manual 빌드는 태그가 필요 없다. 현행 `build.yml`은 **`prerelease: false`와 `:latest` push가 고정**이라 RC 브랜치에서 실행하면 안 된다. RC를 GitHub prerelease로 공개하려면 별도 승인·발행 정책 수정이 필요하며 이번 범위에는 포함하지 않았다.

### 2. 정식 3.0.0.2 발행

1. RC 통과 및 최종 사용자 승인을 받은 뒤 총괄이 VERSION을 `3.0.0.2`로 승격하고 CHANGELOG의 정식 배포 항목을 실제 승인 범위대로 작성·커밋한다. 동일한 코드인지 확인하고 최종 버전/패키징 검사를 수행한다. 승인된 변경을 dev/main 통합 절차에 따라 반영한다.
2. **`v3.0.0.2` 태그를 미리 생성·push하지 않는다.** `build.yml`의 prepare-release는 VERSION에서 태그명을 만들고 원격 태그가 이미 있으면 후속 빌드·발행 전체를 건너뛴다. 재시도 전에도 태그/Release/이미지 존재와 각 job 결과를 먼저 확인한다.
3. 승인된 main push가 **Build & Release (`build.yml`)를 자동 실행**한다. 자동 실행이 시작되었으면 중복 dispatch하지 않는다. 자동 실행이 없고 승인된 main SHA가 확정된 경우에만 아래 명령으로 한 번 실행한다.

```sh
gh workflow run build.yml --ref main
# 새 run의 head SHA와 VERSION=3.0.0.2를 확인
# gh run watch RUN_ID --exit-status
# gh release view v3.0.0.2
```

4. Windows/Linux/macOS x64/arm64 ZIP 4개와 Docker linux/amd64·arm64 두 아키텍처를 모두 확인한다. Docker job은 별도 runner `234`에서 GHCR 및 Docker Hub에 `3.0.0.2`와 `latest`를 push한다. create-release는 ZIP 4개 job 완료 후 `v3.0.0.2` GitHub 정식 Release를 생성한다. **create-release의 needs에 build-docker가 없으므로 Release가 생겼다는 것만으로 Docker 성공을 판단하지 않는다.** 전체 run 및 registry digest/platform을 따로 확인한다. Linux ARM 실행파일 job은 주석 처리되어 있어 ZIP 4개에 포함되지 않는다.
5. 최종 사용자 환경 업데이트는 승인된 운영 절차로 진행하고 버전·바인딩 게이트·백업 경로를 확인한다. 이번 로컬 검증에서 실제 사용자의 계정 변경·삭제를 실행하지 않았다.

### 3. 실패/롤백

- main 반영 전 문제면 RC를 수정하고 `-rc2` 등 새 후보로 재검증한다. 실패한 RC를 정식 workflow로 넘기지 않는다.
- 발행 중 일부 실패 시 전체 job 상태와 `v3.0.0.2` 태그 생성 여부를 먼저 확인한다. Docker 이미지 push는 GitHub Release와 원자적이지 않다. 태그가 생긴 뒤 새 workflow를 실행하면 skip될 수 있으므로 **기존 실패 run/job의 재실행** 또는 새 수정 버전 발행을 총괄이 선택한다. 태그 강제 이동·삭제로 무리하게 재발행하지 않는다.
- 클라이언트 롤백은 기존 검증된 `v3.0.0.1` ZIP 또는 해당 버전의 고정 이미지 digest를 사용한다. `latest`를 되돌리는 registry 작업·운영 컨테이너 재시작은 총괄의 별도 승인된 운영 작업이다. 기록된 기존 이미지 digest/설정/데이터 백업을 보존한 뒤 실행한다.
- 코드 롤백은 별도 revert 커밋과 새 버전 릴리스로 처리한다(reset/force push 금지). 구 PC는 신규 바인딩 UX를 제공하지 않으므로 임시 제한을 안내해야 한다. 운영 auth/map의 1:1 보호를 PC 롤백과 함께 제거하지 않는다.
- 계정 변경으로 이미 삭제한 **원격 공유자료·바인딩은 바이너리 롤백만으로 복원되지 않는다**. 로컬 백업·`official-account-change.json`을 보존하고 실제 완료 단계와 서버 바인딩을 확인한 뒤 처리한다. 같은 카카오의 로컬 백업 복원과 원격 재수집/재공유는 별개이며, 운영 데이터 조작은 사용자 승인 없이는 실행하지 않는다.

## 남은 판정

RC의 로컬 검사 완료이며 정식 발행 승인이 아니다. 총괄의 독립 검토, native OS manual 빌드·동작 확인, 운영 반영에 대한 최종 사용자 승인이 남아 있다. 운영 API/실데이터·실카카오/안신, 이번 RC의 PC↔모바일 실제 Dart 왕복, 전체 접근성/실기기·전체 브라우저 회귀는 재실행하지 않았다. source 브라우저 성공을 Docker/frozen 전체 동선이나 모든 플랫폼 성공으로 확대하지 않는다.
