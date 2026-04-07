import PyInstaller.__main__
import sys
import os
import shutil
import platform

def build():
    sep = ';' if sys.platform == 'win32' else ':'

    options = [
        'main.py',
        '--name=mysafetyreport',
        '--icon=mysafetyreport.ico',
        '--clean',
        '--noconfirm',
        # Add Jinja2 templates, Static files, and VERSION
        f'--add-data=web/templates{sep}web/templates',
        f'--add-data=web/static{sep}web/static',
        f'--add-data=VERSION{sep}.',
        # Include hidden imports for dynamic loading frameworks
        '--hidden-import=uvicorn',
        '--hidden-import=fastapi',
        '--hidden-import=selenium',
        '--hidden-import=selenium.webdriver',
        '--hidden-import=selenium.webdriver.chrome.service',
        '--hidden-import=selenium.webdriver.chrome.options',
        '--hidden-import=selenium.webdriver.common.by',
        '--hidden-import=selenium.webdriver.support.ui',
        '--hidden-import=selenium.webdriver.support.expected_conditions',
        '--hidden-import=webdriver_manager',
        '--hidden-import=webdriver_manager.chrome',
        '--hidden-import=gspread',
        # Exclude unused stdlib & packages to reduce bundle size
        '--exclude-module=tkinter',
        '--exclude-module=setuptools',
        '--exclude-module=unittest',
        '--exclude-module=doctest',
        '--exclude-module=pdb',
        '--exclude-module=profile',
        '--exclude-module=pstats',
        '--exclude-module=cProfile',
        '--exclude-module=ftplib',
        '--exclude-module=imaplib',
        '--exclude-module=nntplib',
        '--exclude-module=poplib',
        '--exclude-module=smtplib',
        '--exclude-module=telnetlib',
        '--exclude-module=xmlrpc',
        '--exclude-module=curses',
        '--exclude-module=antigravity',
        # Show console for server logs
        # '--windowed'
    ]

    # readline은 Linux에서만 제외 (Windows는 기본 없음)
    if sys.platform != 'win32':
        options.append('--exclude-module=readline')

    PyInstaller.__main__.run(options)

    # 현재 플랫폼에 불필요한 selenium-manager 바이너리 제거
    _remove_cross_platform_selenium_manager()

    # Linux: 터미널 창을 열어주는 런처 스크립트 생성
    if sys.platform != "win32":
        _create_linux_launcher()


def _remove_cross_platform_selenium_manager():
    """빌드 후 타 플랫폼용 selenium-manager 바이너리를 제거해 용량을 줄입니다."""
    selenium_common = os.path.join(
        "dist", "mysafetyreport", "_internal",
        "selenium", "webdriver", "common"
    )
    if not os.path.isdir(selenium_common):
        return

    machine = platform.machine().lower()
    is_arm = 'arm' in machine or 'aarch64' in machine

    if sys.platform == "win32":
        remove_dirs = ["macos", "linux"]
    elif sys.platform == "darwin":
        remove_dirs = ["windows", "linux"]
    else:
        # Linux (including Raspberry Pi)
        remove_dirs = ["macos", "windows"]
        # If it's x86_64, you might want to remove arm64 folders if they exist
        # If it's ARM64, you might want to remove x64 folders if they exist
        # (Assuming selenium has separate linux-arm64 directory in future/other versions)
        if is_arm:
            print(f"[build] ARM64 환경 감지: {machine}")
        else:
            print(f"[build] x86_64 환경 감지: {machine}")

    for d in remove_dirs:
        path = os.path.join(selenium_common, d)
        if os.path.isdir(path):
            shutil.rmtree(path)
            print(f"[build] 타 플랫폼 selenium-manager 제거: {path}")


def _create_linux_launcher():
    import os
    import stat

    run_sh = os.path.join("dist", "mysafetyreport", "run.sh")
    content = r"""#!/bin/bash
# 터미널 창에서 실행 중이면 바로 시작, 아니면 터미널 에뮬레이터를 열어서 실행
DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
EXE="$DIR/mysafetyreport"

if [ -t 1 ]; then
    exec "$EXE" "$@"
fi

for term in gnome-terminal konsole xfce4-terminal lxterminal xterm; do
    if command -v "$term" &>/dev/null; then
        case "$term" in
            gnome-terminal)
                exec "$term" -- bash -c "\"$EXE\"; echo; read -p '종료되었습니다. Enter를 눌러 창을 닫으세요...' _"
                ;;
            konsole)
                exec "$term" -e bash -c "\"$EXE\"; echo; read -p '종료되었습니다. Enter를 눌러 창을 닫으세요...' _"
                ;;
            *)
                exec "$term" -e "\"$EXE\""
                ;;
        esac
    fi
done

# 터미널 에뮬레이터를 찾지 못한 경우 그냥 실행
exec "$EXE" "$@"
"""
    with open(run_sh, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    os.chmod(run_sh, os.stat(run_sh).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    print(f"[build] Linux 런처 스크립트 생성: {run_sh}")


if __name__ == "__main__":
    build()
