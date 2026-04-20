"""
자동 업데이트 모듈.

- Docker 환경: 새 버전 안내 메시지만 출력 (docker pull 안내)
- 컴파일 바이너리(is_frozen): 대화형 프롬프트 → ZIP 다운로드 → 압축 해제 → 전체 교체 → 재시작
  - Linux: 셸 스크립트로 프로세스 종료 후 rsync(없으면 cp) + exec 재시작
  - Windows: bat 스크립트로 프로세스 종료 후 xcopy + 재시작
- 개발 환경: git pull 안내 메시지만 출력

GitHub Releases 에셋 명명 규칙 (build.yml 기준):
  mysafetyreport-win.zip        ← Windows x64
  mysafetyreport-linux.zip      ← Linux x64
  # mysafetyreport-linux-arm64.zip  ← ARM64 (빌드 비활성화)
"""

import os
import sys
import tempfile
import zipfile
import shutil

GITHUB_REPO = "Fentanest/safetyreport"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"


_latest_version_cache: dict = {}   # {"version": str, "expires": float}


def get_latest_version_cached() -> str | None:
    """
    GitHub 최신 버전을 반환합니다. 1시간 캐시.
    네트워크 오류 시 None 반환.
    """
    import time
    now = time.monotonic()
    if _latest_version_cache.get("expires", 0) > now:
        return _latest_version_cache["version"]

    release = _fetch_latest_release()
    ver = release.get("tag_name", "").lstrip("v") if release else None
    _latest_version_cache["version"] = ver
    _latest_version_cache["expires"] = now + 3600
    return ver


def get_current_version() -> str | None:
    from core.utils.path_utils import resource_path
    try:
        with open(resource_path("VERSION"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def _get_asset_name() -> str | None:
    """현재 플랫폼에 해당하는 릴리스 에셋 파일명을 반환합니다."""
    if sys.platform == "win32":
        return "mysafetyreport-win.zip"
    # import platform
    # machine = platform.machine().lower()
    # is_arm = 'arm' in machine or 'aarch64' in machine
    # if is_arm:
    #     return "mysafetyreport-linux-arm64.zip"  # ARM64 빌드 비활성화
    return "mysafetyreport-linux.zip"


def _fetch_latest_release() -> dict | None:
    """GitHub API에서 최신 릴리스 정보를 가져옵니다. 실패 시 None 반환."""
    try:
        import urllib.request
        import json
        req = urllib.request.Request(
            GITHUB_API_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "mysafetyreport-updater",
            }
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _version_gt(v1: str, v2: str) -> bool:
    """v1이 v2보다 최신이면 True. -dev 등 pre-release 태그 지원."""
    try:
        from packaging.version import Version
        return Version(v1) > Version(v2)
    except Exception:
        pass
    # fallback: 숫자 튜플 비교
    def _base_tuple(v: str):
        base = v.split('-')[0].split('+')[0]
        try:
            return tuple(int(x) for x in base.split('.'))
        except ValueError:
            return (0,)
    t1, t2 = _base_tuple(v1), _base_tuple(v2)
    if t1 != t2:
        return t1 > t2
    # base가 같으면 pre-release(dev/alpha/beta/rc)는 정식보다 낮음
    _PRE = ('dev', 'alpha', 'beta', 'rc')
    is_pre1 = any(tag in v1.lower() for tag in _PRE)
    is_pre2 = any(tag in v2.lower() for tag in _PRE)
    return is_pre2 and not is_pre1


def check_and_prompt_update():
    """
    프로그램 시작 시 업데이트를 확인하고, 환경에 따라 안내 또는 대화형 업데이트를 수행합니다.
    업데이트 적용 후 sys.exit(0)으로 종료됩니다.
    """
    current = get_current_version()
    if not current:
        print("업데이트 확인 건너뜀: VERSION 파일을 읽을 수 없습니다.")
        return

    print("업데이트 확인 중...", end=" ", flush=True)
    release = _fetch_latest_release()
    if not release:
        print("(서버 연결 실패, 건너뜀)")
        return

    latest_ver = release.get("tag_name", "").lstrip("v")
    if not latest_ver:
        print("(버전 정보 없음)")
        return

    if not _version_gt(latest_ver, current):
        print(f"최신 버전입니다. (v{current})")
        return

    print()
    print("=" * 60)
    print(f"  새 버전 v{latest_ver} 이 있습니다!  (현재 버전: v{current})")
    print("=" * 60)

    is_docker = os.path.exists('/.dockerenv')
    from core.utils.path_utils import is_frozen

    if is_docker:
        print("도커 환경입니다. 아래 명령으로 최신 이미지를 받으세요:")
        print(f"  docker pull ghcr.io/{GITHUB_REPO.lower()}:latest")
        print(f"릴리스 정보: {GITHUB_RELEASES_URL}")
        print()
        return

    if not is_frozen:
        print("개발 환경입니다. git pull 로 업데이트하세요.")
        print()
        return

    # 컴파일 바이너리 — 대화형 업데이트
    asset_name = _get_asset_name()
    download_url = None
    for asset in release.get("assets", []):
        if asset["name"] == asset_name:
            download_url = asset["browser_download_url"]
            break

    if not download_url:
        print(f"이 플랫폼용 파일({asset_name})을 릴리스에서 찾을 수 없습니다.")
        print(f"직접 다운로드: {GITHUB_RELEASES_URL}")
        print()
        return

    try:
        answer = input("지금 업데이트하시겠습니까? (y/n): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return

    if answer != 'y':
        print("업데이트를 건너뜁니다.")
        print()
        return

    _perform_update(download_url, latest_ver)


def _perform_update(download_url: str, new_version: str):
    """ZIP을 다운로드하고 압축을 해제한 뒤 설치 디렉토리 전체를 교체합니다."""
    import urllib.request

    current_exe = os.path.abspath(sys.executable)
    install_dir = os.path.dirname(current_exe)

    # install_dir 밖의 시스템 임시 디렉토리 사용.
    # install_dir 안에 두면 rsync --delete 실행 시 tmp_dir 자신을 삭제하려다
    # "vanished source files" (종료코드 24) 오류가 발생함.
    tmp_dir = tempfile.mkdtemp(prefix="safetyreport_update_")
    zip_path = os.path.join(tmp_dir, "update.zip")
    extract_dir = os.path.join(tmp_dir, "extracted")

    try:
        # ── 1. 다운로드 ──────────────────────────────────────────────
        print("다운로드 중...")

        def _reporthook(block_num, block_size, total_size):
            if total_size > 0:
                downloaded = min(block_num * block_size, total_size)
                pct = downloaded * 100 // total_size
                mb_done = downloaded / 1024 / 1024
                mb_total = total_size / 1024 / 1024
                print(f"\r  {pct}% ({mb_done:.1f} / {mb_total:.1f} MB)", end='', flush=True)

        urllib.request.urlretrieve(download_url, zip_path, _reporthook)
        print()

        # ── 2. 압축 해제 ──────────────────────────────────────────────
        print("압축 해제 중...")
        os.makedirs(extract_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(extract_dir)
        os.unlink(zip_path)

        # ── 3. 플랫폼별 교체 + 재시작 ─────────────────────────────────
        if sys.platform == "win32":
            _apply_windows(current_exe, install_dir, extract_dir, tmp_dir, new_version)
        else:
            _apply_linux(current_exe, install_dir, extract_dir, tmp_dir, new_version)

    except Exception as e:
        print(f"\n업데이트 실패: {e}")
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _apply_linux(current_exe: str, install_dir: str, extract_dir: str,
                 tmp_dir: str, new_version: str):
    """
    셸 스크립트를 백그라운드로 실행한 뒤 현재 프로세스를 종료합니다.
    스크립트가 모든 파일을 교체하고 프로그램을 재시작합니다.
    shlex.quote()로 경로를 이스케이프하므로 한글·공백·특수문자 경로에 안전합니다.
    """
    import subprocess
    import stat
    import shlex

    sh_path = os.path.join(tmp_dir, "_apply_update.sh")
    log_path = os.path.join(install_dir, "_update_log.txt")

    q = shlex.quote
    extra_args = " ".join(q(a) for a in sys.argv[1:])

    with open(sh_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(
            "#!/bin/bash\n"
            f"LOG={q(log_path)}\n"
            'log() { echo "[$(date "+%Y-%m-%d %H:%M:%S")] $*" | tee -a "$LOG"; }\n'
            f'log "업데이트 시작: v{new_version}"\n'
            f'log "설치 경로: {install_dir}"\n'
            f'log "임시 경로: {extract_dir}"\n'
            "sleep 2\n"
            'log "파일 복사 시작..."\n'
            f"if command -v rsync &>/dev/null; then\n"
            f"  rsync -a --delete {q(extract_dir + '/')} {q(install_dir + '/')} 2>&1 | tee -a \"$LOG\"\n"
            f"else\n"
            f"  cp -rT {q(extract_dir)} {q(install_dir)} 2>&1 | tee -a \"$LOG\"\n"
            f"fi\n"
            'RC=${PIPESTATUS[0]}\n'
            'if [ $RC -ne 0 ]; then log "오류: 파일 복사 실패 (종료코드 $RC)"; exit 1; fi\n'
            f"chmod +x {q(current_exe)}\n"
            f'log "파일 복사 완료. 임시 폴더 정리..."\n'
            f"rm -rf {q(tmp_dir)}\n"
            f'log "재시작: {current_exe}"\n'
            f"exec {q(current_exe)} {extra_args}\n"
        )

    os.chmod(sh_path, os.stat(sh_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    print(f"v{new_version} 업데이트가 준비되었습니다. 재시작합니다...")
    print(f"업데이트 로그: {log_path}")
    subprocess.Popen(
        ["bash", sh_path],
        start_new_session=True,
        close_fds=True,
    )
    sys.exit(0)


def _ps_escape(path: str) -> str:
    """PowerShell 단일 인용 문자열 안에서 홑따옴표를 이스케이프합니다 (' → '')."""
    return path.replace("'", "''")


def _apply_windows(current_exe: str, install_dir: str, extract_dir: str,
                   tmp_dir: str, new_version: str):
    """
    PowerShell 스크립트를 새 창에서 실행한 뒤 현재 프로세스를 종료합니다.
    cmd.exe/bat 대신 PowerShell을 사용해 한글·공백 경로를 안전하게 처리합니다.
    UTF-8 BOM으로 저장하므로 PowerShell이 유니코드를 올바르게 읽습니다.

    Copy-Item 주의: 디렉토리를 -LiteralPath로 지정하면 디렉토리 자체가 대상 안으로 복사됨.
    내용물을 덮어쓰려면 -Path 'dir\\*' (와일드카드) 방식을 사용해야 함.
    """
    import subprocess

    ps_path = os.path.join(tmp_dir, "_apply_update.ps1")
    log_path = os.path.join(install_dir, "_update_log.txt")

    e = _ps_escape
    # Copy-Item 주의:
    #   -LiteralPath 'dir' → 디렉토리 자체가 대상 안으로 복사됨 (잘못된 동작)
    #   -Path 'dir\*'      → 디렉토리 내용물을 대상으로 복사 (올바른 동작)
    ps_content = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$LogFile = '{e(log_path)}'\n"
        "function Log($msg) {\n"
        "    $line = \"[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg\"\n"
        "    Write-Host $line\n"
        "    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8\n"
        "}\n"
        f"Log '업데이트 시작: v{e(new_version)}'\n"
        f"Log '설치 경로: {e(install_dir)}'\n"
        f"Log '임시 경로: {e(extract_dir)}'\n"
        "Start-Sleep -Seconds 2\n"
        "try {\n"
        "    Log '파일 복사 시작...'\n"
        f"    Copy-Item -Path '{e(extract_dir)}\\*' -Destination '{e(install_dir)}' -Recurse -Force\n"
        "    Log '파일 복사 완료'\n"
        "} catch {\n"
        "    Log \"오류: $_\"\n"
        "    Write-Host ''\n"
        "    Write-Host '업데이트 실패. 10초 후 창이 닫힙니다.'\n"
        "    Start-Sleep -Seconds 10\n"
        "    exit 1\n"
        "}\n"
        # tmp_dir은 이 스크립트가 실행 중인 폴더이므로 Start-Process 후 별도 정리
        f"Log '재시작: {e(current_exe)}'\n"
        f"Start-Process -FilePath '{e(current_exe)}'\n"
        "Log '업데이트 완료'\n"
        "Start-Sleep -Seconds 2\n"
        f"Remove-Item -LiteralPath '{e(tmp_dir)}' -Recurse -Force -ErrorAction SilentlyContinue\n"
    )

    with open(ps_path, 'w', encoding='utf-8-sig', newline='\r\n') as f:
        f.write(ps_content)

    print(f"v{new_version} 업데이트가 준비되었습니다. 재시작합니다...")
    print(f"업데이트 로그: {log_path}")
    subprocess.Popen(
        [
            "powershell",
            "-ExecutionPolicy", "Bypass",
            "-File", ps_path,
        ],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        close_fds=True,
    )
    sys.exit(0)
