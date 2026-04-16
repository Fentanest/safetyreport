"""
자동 업데이트 모듈.

- Docker 환경: 새 버전 안내 메시지만 출력 (docker pull 안내)
- 컴파일 바이너리(is_frozen): 대화형 프롬프트 → 다운로드 → 교체 → 재시작
  - Linux: os.replace + os.execv (즉시 재시작)
  - Windows: bat 스크립트로 프로세스 종료 후 파일 교체 + 재시작
- 개발 환경: git pull 안내 메시지만 출력

GitHub Releases 에셋 명명 규칙:
  mysafetyreport-windows-x64.exe
  mysafetyreport-linux-x64
  mysafetyreport-linux-arm64
"""

import os
import sys
import platform
import tempfile

GITHUB_REPO = "Fentanest/safetyreport"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases/latest"


def get_current_version() -> str | None:
    from core.utils.path_utils import resource_path
    try:
        with open(resource_path("VERSION"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def _get_asset_name() -> str:
    machine = platform.machine().lower()
    is_arm = 'arm' in machine or 'aarch64' in machine
    if sys.platform == "win32":
        return "mysafetyreport-windows-x64.exe"
    elif is_arm:
        return "mysafetyreport-linux-arm64"
    else:
        return "mysafetyreport-linux-x64"


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
    """v1이 v2보다 최신이면 True."""
    try:
        from packaging.version import Version
        return Version(v1) > Version(v2)
    except Exception:
        return v1 != v2


def check_and_prompt_update():
    """
    프로그램 시작 시 업데이트를 확인하고, 환경에 따라 안내 또는 대화형 업데이트를 수행합니다.
    Windows에서 업데이트 적용 후 sys.exit(0)으로 종료됩니다.
    """
    current = get_current_version()
    if not current:
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
        print(f"개발 환경입니다. git pull 로 업데이트하세요.")
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
    """바이너리를 다운로드하고 교체합니다."""
    import urllib.request

    current_exe = os.path.abspath(sys.executable)
    exe_dir = os.path.dirname(current_exe)
    suffix = ".exe" if sys.platform == "win32" else ""

    print("다운로드 중...")
    tmp_path = None
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(suffix=f"_update{suffix}", dir=exe_dir)
        os.close(tmp_fd)

        def _reporthook(block_num, block_size, total_size):
            if total_size > 0:
                downloaded = min(block_num * block_size, total_size)
                pct = downloaded * 100 // total_size
                mb_done = downloaded / 1024 / 1024
                mb_total = total_size / 1024 / 1024
                print(f"\r  {pct}% ({mb_done:.1f} / {mb_total:.1f} MB)", end='', flush=True)

        urllib.request.urlretrieve(download_url, tmp_path, _reporthook)
        print()

        if sys.platform == "win32":
            _apply_windows(current_exe, tmp_path, new_version, exe_dir)
        else:
            _apply_linux(current_exe, tmp_path, new_version)

    except Exception as e:
        print(f"\n업데이트 실패: {e}")
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def _apply_linux(current_exe: str, tmp_path: str, new_version: str):
    import stat
    os.chmod(tmp_path, os.stat(tmp_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    os.replace(tmp_path, current_exe)
    print(f"v{new_version} 업데이트 완료! 재시작합니다...")
    os.execv(current_exe, sys.argv)


def _apply_windows(current_exe: str, tmp_path: str, new_version: str, exe_dir: str):
    import subprocess
    bat_path = os.path.join(exe_dir, "_update.bat")
    with open(bat_path, 'w', encoding='mbcs') as f:
        f.write(
            "@echo off\n"
            "echo 업데이트 적용 중...\n"
            "timeout /t 2 /nobreak > nul\n"
            ":retry\n"
            f"move /y \"{tmp_path}\" \"{current_exe}\" > nul 2>&1\n"
            "if errorlevel 1 (timeout /t 1 /nobreak > nul & goto retry)\n"
            f"echo v{new_version} 업데이트 완료!\n"
            f"start \"\" \"{current_exe}\"\n"
            "del \"%~f0\"\n"
        )
    print(f"v{new_version} 업데이트가 준비되었습니다. 프로그램을 재시작합니다...")
    subprocess.Popen(
        ["cmd", "/c", bat_path],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
        close_fds=True,
    )
    sys.exit(0)
