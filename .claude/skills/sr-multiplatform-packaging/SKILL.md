---
name: sr-multiplatform-packaging
description: Verify web UI assets and behavior in Python source, PyInstaller bundles and the isolated dev Docker project across supported operating systems.
---

# 다중 실행 환경 보존

먼저 `docs/development/runtime-and-packaging.md`, `scripts/build/build_exe.py`, `Dockerfile`, `.dockerignore`, `core/utils/path_utils.py` 를 읽는다.

1. 데이터 루트: source 는 저장소 `data/`, frozen 은 실행파일 옆 `data/`, 테스트는 `SAFETYREPORT_DATA_DIR`. cwd 변경만으로 분리되지 않는다.
   리소스(템플릿·static)는 `resource_path()` — 번들 내부를 데이터 저장소로 쓰지 않는다.
2. 외부 부작용은 `SAFETYREPORT_FIXTURE_MODE=1` 로 서버측에서 차단된다(`core/utils/runtime_mode.py`, `tests/test_fixture_runtime_mode.py`).
3. 새 CSS/JS/아이콘은 `web/static` 아래에 두면 build_exe.py add-data 와 Docker `COPY . .` 에 포함된다. 번들 결과에서 HTTP 200·MIME 을 확인한다.
4. Docker 는 운영 compose 대신 `tools/docker/compose.devtest.yml` 과 에이전트별 `-p <project>`·`SR_DOCKER_PORT` 를 쓴다.
   Watchtower·Docker socket·`./data` 마운트 없음. 정리는 그 project 의 `down -v` 만. prune·다른 컨테이너 조작 금지.
5. 이미지 안에 `.venv`, `node_modules`, `.agent-runs`, `docs`, `tests`, `tools`, `scripts/dev` 가 없고 `web/static`, `web/templates` 가 있는지 확인한다.
6. OS 지원과 브라우저 엔진 통과를 혼동하지 않는다. 실행하지 못한 runner(Windows/macOS 번들 등)는 not-run/blocked 로 남긴다.

산출: `docs/testing/environment-matrix.csv` 갱신, build/HTTP 결과, 누락 자산, 미검증 환경. image push·운영 컨테이너 재시작·release 는 범위 밖.
