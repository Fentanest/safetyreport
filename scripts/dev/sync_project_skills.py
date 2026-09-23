#!/usr/bin/env python3
"""Copy only the five blueprint sr-* skills from .agents to .claude.
Dry-run by default. This script does not install external packages or change permissions.
"""
from __future__ import annotations
import argparse
import hashlib
from pathlib import Path
import sys

NAMES = (
    'sr-web-contract-audit', 'sr-jinja-theme-renewal', 'sr-browser-visual-qa',
    'sr-multiplatform-packaging', 'sr-statistics-semantics',
)

def checked(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f'Path escapes repository (possibly a symlink): {path}')
    return resolved

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True, help='Target repository root')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true', help='Actually write the copies')
    mode.add_argument('--check', action='store_true', help='Return non-zero for missing/different copies')
    parser.add_argument('--replace', action='store_true', help='With --apply, replace different SKILL.md copies')
    args = parser.parse_args()
    if args.replace and not args.apply:
        parser.error('--replace requires --apply')
    root = args.repo.resolve()
    if not root.is_dir():
        parser.error('Repository root is not a directory')
    plans = []
    try:
        for name in NAMES:
            src = checked(root, root / '.agents' / 'skills' / name / 'SKILL.md')
            dst = checked(root, root / '.claude' / 'skills' / name / 'SKILL.md')
            if not src.is_file():
                raise FileNotFoundError(f'Missing canonical source: {src}')
            data = src.read_bytes()
            if dst.exists() and not dst.is_file():
                raise ValueError(f'Destination is not a regular file: {dst}')
            state = 'missing' if not dst.exists() else ('same' if dst.read_bytes() == data else 'different')
            plans.append((src, dst, data, state))
        for src, dst, data, state in plans:
            print(f'{state:9} {dst.relative_to(root)} sha256={hashlib.sha256(data).hexdigest()}')
        if args.check:
            return 0 if all(p[3] == 'same' for p in plans) else 1
        if args.apply:
            if any(p[3] == 'different' for p in plans) and not args.replace:
                print('Conflicting copy: review differences, then explicitly use --apply --replace.', file=sys.stderr)
                return 2
            for src, dst, data, state in plans:
                if state != 'same':
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    # Exclusive creation protects new files from accidental overwrite.
                    with dst.open('wb' if state == 'different' else 'xb') as stream:
                        stream.write(data)
            print('Copies synchronized. Review git diff and ignore rules before committing.')
        else:
            print('Dry-run only. Use --apply after merging the canonical project skills.')
        return 0
    except (OSError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
