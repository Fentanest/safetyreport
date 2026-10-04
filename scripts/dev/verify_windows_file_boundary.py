"""Windows runner에서 실제 Win32 파일 핸들 경계를 검증한다(운영 데이터 미사용)."""
import json
import os
import sys
import tempfile
from pathlib import Path


def main():
    if os.name != 'nt':
        print(json.dumps({'status': 'blocked', 'reason': 'actual Windows runner required'}))
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from core.utils.windows_file_handle import read_file, delete_file
    with tempfile.TemporaryDirectory(prefix='sr_boundary_') as directory:
        root = Path(directory) / '한글 logs'
        root.mkdir()
        source = root / '한글 file.txt'
        source.write_bytes(b'fixture\n')
        with read_file(str(source), str(root)) as handle:
            assert handle.read() == b'fixture\n'
            try:
                source.rename(root / 'renamed.txt')
            except PermissionError:
                pass
            else:
                raise AssertionError('held identity was renamed')
        outside = Path(directory) / 'outside.txt'
        outside.write_bytes(b'preserve')
        link = root / 'hardlink'
        os.link(outside, link)
        for action in (read_file, delete_file):
            try:
                action(str(link), str(root))
            except PermissionError:
                pass
            else:
                raise AssertionError('hardlink accepted')
        link.unlink()
        # Junction creation uses only this synthetic directory, with no admin token.
        import subprocess
        junction = root / 'junction'
        outside_dir = Path(directory) / 'outside-directory'
        outside_dir.mkdir()
        (outside_dir / 'probe.txt').write_bytes(b'preserve')
        command = ['cmd', '/c', 'mklink', '/J', str(junction), str(outside_dir)]
        subprocess.run(command, check=True, capture_output=True)
        try:
            read_file(str(junction / 'probe.txt'), str(root))
        except PermissionError:
            pass
        else:
            raise AssertionError('junction outside root accepted')
        junction.rmdir()
        delete_file(str(source), str(root))
        assert not source.exists() and outside.read_bytes() == b'preserve'
    print(json.dumps({'status': 'passed', 'checks': ['same_handle_read', 'rename_fence', 'hardlink_reject', 'junction_reject', 'same_handle_delete']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
