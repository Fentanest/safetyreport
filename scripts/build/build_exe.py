import json
import sys
import os
import shutil
import platform

BUNDLED_PUBLIC = os.path.join("build", "community_public.json")


def write_community_public(env=None) -> str | None:
    """커뮤니티 공개 설정(Supabase URL·publishable key·site URL)을 번들용 JSON 으로 만든다.

    값은 CI 저장소 Variables(COMMUNITY_SUPABASE_URL·COMMUNITY_PUBLISHABLE_KEY·COMMUNITY_SITE_URL)에서 온다. 공개값만 받는다:
    비밀 키(sb_secret_·service_role JWT)나 자리표시자(<...>, YOUR_, example)면 빌드를 멈춘다. 값이 없으면 번들하지 않고
    경고한다(실행 때 환경변수·config.ini 로 설정하지 않으면 필수 설정 화면이 '설정 확인 필요'를 보인다).
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    if root not in sys.path:  # `python scripts/build/build_exe.py` 로 실행하면 저장소 루트가 경로에 없다
        sys.path.insert(0, root)
    from services.community_auth_service import normalize_site_url, normalize_supabase_url, validate_publishable_key

    env = os.environ if env is None else env
    url = (env.get("COMMUNITY_SUPABASE_URL") or "").strip()
    key = (env.get("COMMUNITY_PUBLISHABLE_KEY") or "").strip()
    site = (env.get("COMMUNITY_SITE_URL") or "https://safeauth.worklazy.net/").strip()
    if not url and not key:
        print("WARNING: COMMUNITY_SUPABASE_URL/COMMUNITY_PUBLISHABLE_KEY 없음 — 공개 설정을 번들하지 않습니다.")
        return None
    for value in (url, key, site):
        low = value.lower()
        if "<" in value or "your_" in low or "example" in low or "project_ref" in low:
            raise SystemExit("community public config looks like a placeholder")
    if not normalize_supabase_url(url) or not url.startswith("https://"):
        raise SystemExit("COMMUNITY_SUPABASE_URL must be an https Supabase URL")
    if not validate_publishable_key(key):
        raise SystemExit("COMMUNITY_PUBLISHABLE_KEY must be a publishable/anon key (secret keys are refused)")
    if not normalize_site_url(site):
        raise SystemExit("COMMUNITY_SITE_URL is not valid")
    os.makedirs(os.path.dirname(BUNDLED_PUBLIC), exist_ok=True)
    with open(BUNDLED_PUBLIC, "w", encoding="utf-8") as fh:
        json.dump({"supabase_url": url, "publishable_key": key, "site_url": site}, fh)
    return BUNDLED_PUBLIC


def _resolve_icon_option() -> list[str]:
    if sys.platform == "darwin":
        icns_path = "mysafetyreport.icns"
        if os.path.exists(icns_path):
            return [f"--icon={icns_path}"]
        return []
    return ["--icon=mysafetyreport.ico"]


def _resolve_target_arch_option() -> list[str]:
    if sys.platform != "darwin":
        return []

    target_arch = os.environ.get("PYINSTALLER_TARGET_ARCH", "").strip()
    if not target_arch:
        return []

    valid_arches = {"x86_64", "arm64", "universal2"}
    if target_arch not in valid_arches:
        raise SystemExit(
            f"Unsupported PYINSTALLER_TARGET_ARCH={target_arch!r}. "
            f"Expected one of: {', '.join(sorted(valid_arches))}"
        )

    print(f"[build] PyInstaller target arch: {target_arch}")
    return [f"--target-arch={target_arch}"]


def build():
    sep = ';' if sys.platform == 'win32' else ':'

    options = [
        'main.py',
        '--name=mysafetyreport',
        '--clean',
        '--noconfirm',
        # Add Jinja2 templates, Static files, and VERSION
        f'--add-data=web/templates{sep}web/templates',
        f'--add-data=web/static{sep}web/static',
        f'--add-data=VERSION{sep}.',
        # 신고내용 공유 동의문 사본(게이트가 해시와 함께 보여 준다) — 빠지면 동의할 수 없다
        f'--add-data=contracts/community-ingest/consent{sep}contracts/community-ingest/consent',
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
        '--collect-data=certifi',
        # Exclude unused stdlib & packages to reduce bundle size
        '--exclude-module=tkinter',
        '--exclude-module=setuptools',
        '--exclude-module=pytest',
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
    ] + _resolve_icon_option() + _resolve_target_arch_option()

    bundled = write_community_public()
    if bundled:
        options.append(f'--add-data={bundled}{sep}.')

    # readline은 Linux 번들에서만 제외
    if sys.platform.startswith('linux'):
        options.append('--exclude-module=readline')

    import PyInstaller.__main__  # 빌드 때만 필요(테스트는 공개 설정 검증만 import 한다)

    PyInstaller.__main__.run(options)

    # 현재 플랫폼에 불필요한 selenium-manager 바이너리 제거
    _remove_cross_platform_selenium_manager()

    if sys.platform == "darwin":
        _create_macos_launcher()
    elif sys.platform.startswith("linux"):
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
    _write_launcher(run_sh, content)
    print(f"[build] Linux 런처 스크립트 생성: {run_sh}")


def _create_macos_launcher():
    run_command = os.path.join("dist", "mysafetyreport", "run.command")
    content = r"""#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
EXE="$DIR/mysafetyreport"
INTERNAL_DIR="$DIR/_internal"

cd "$DIR" || exit 1

if command -v xattr >/dev/null 2>&1; then
    xattr -dr com.apple.quarantine "$DIR/run.command" "$EXE" "$INTERNAL_DIR" 2>/dev/null || true
fi

if [ ! -x "$EXE" ]; then
    chmod +x "$EXE"
fi

"$EXE" "$@"
STATUS=$?

echo
read -r -p "종료되었습니다. Enter를 눌러 창을 닫으세요..." _
exit "$STATUS"
"""
    _write_launcher(run_command, content)
    print(f"[build] macOS 런처 스크립트 생성: {run_command}")


def _write_launcher(path: str, content: str):
    import stat

    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(content)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


if __name__ == "__main__":
    build()
