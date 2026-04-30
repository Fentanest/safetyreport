#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


MACHO_SUFFIXES = {".so", ".dylib"}
SKIP_DIR_NAMES = {
    "__pycache__",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    "build",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scan a macOS build tree and fail if any Mach-O binaries are missing "
            "the expected architectures."
        )
    )
    parser.add_argument(
        "--root",
        action="append",
        required=True,
        help="Directory or file to scan. May be provided multiple times.",
    )
    parser.add_argument(
        "--expected-arch",
        action="append",
        dest="expected_arches",
        default=[],
        help="Architecture that every Mach-O binary must contain. Repeatable.",
    )
    parser.add_argument(
        "--fail-if-empty",
        action="store_true",
        help="Fail if no Mach-O binaries are found in the scan target.",
    )
    return parser.parse_args()


def run_command(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=False, capture_output=True, text=True)


def is_macho_binary(path: Path) -> bool:
    result = run_command("file", "-b", str(path))
    if result.returncode != 0:
        return False
    return "Mach-O" in result.stdout


def read_arches(path: Path) -> set[str]:
    result = run_command("lipo", "-archs", str(path))
    if result.returncode != 0:
        return set()
    return {part.strip() for part in result.stdout.split() if part.strip()}


def should_consider_file(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    if path.suffix.lower() in MACHO_SUFFIXES:
        return True
    return os.access(path, os.X_OK)


def iter_candidate_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if should_consider_file(root) else []

    results: list[Path] = []
    for current_root, dir_names, file_names in os.walk(root):
        dir_names[:] = [name for name in dir_names if name not in SKIP_DIR_NAMES]
        current_path = Path(current_root)
        for file_name in file_names:
            candidate = current_path / file_name
            if should_consider_file(candidate):
                results.append(candidate)
    return sorted(results)


def summarize_owner(path: Path) -> str:
    parts = path.parts
    if "site-packages" in parts:
        idx = parts.index("site-packages")
        if idx + 1 < len(parts):
            owner = parts[idx + 1]
            return owner.split(".")[0]
    return path.parent.name or "."


def scan_root(root: Path, expected_arches: set[str]) -> tuple[list[tuple[Path, set[str]]], int]:
    failures: list[tuple[Path, set[str]]] = []
    macho_count = 0

    for candidate in iter_candidate_files(root):
        if not is_macho_binary(candidate):
            continue

        macho_count += 1
        arches = read_arches(candidate)
        if expected_arches - arches:
            failures.append((candidate, arches))

    return failures, macho_count


def main() -> int:
    if sys.platform != "darwin":
        print("[check] This diagnostic is intended for macOS runners only.", file=sys.stderr)
        return 2

    args = parse_args()
    expected_arches = set(args.expected_arches or ["x86_64", "arm64"])
    roots = [Path(root).resolve() for root in args.root]

    total_failures: list[tuple[Path, set[str]]] = []
    total_macho_count = 0

    print(f"[check] Expected architectures: {', '.join(sorted(expected_arches))}")

    for root in roots:
        if not root.exists():
            print(f"[check] Missing root: {root}", file=sys.stderr)
            return 1

        failures, macho_count = scan_root(root, expected_arches)
        total_failures.extend(failures)
        total_macho_count += macho_count

        print(
            f"[check] Scanned {macho_count} Mach-O file(s) under {root}"
        )

    if total_macho_count == 0 and args.fail_if_empty:
        print("[check] No Mach-O binaries found.", file=sys.stderr)
        return 1

    if total_failures:
        print("[check] Universal 2 validation failed. Offending binaries:")
        for path, arches in total_failures:
            missing_arches = sorted(expected_arches - arches)
            owner = summarize_owner(path)
            arch_display = ", ".join(sorted(arches)) if arches else "<unknown>"
            missing_display = ", ".join(missing_arches)
            print(
                f"  - {path} | owner={owner} | arches={arch_display} | missing={missing_display}"
            )
        return 1

    print("[check] Universal 2 validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
