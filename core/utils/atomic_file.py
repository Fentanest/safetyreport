"""같은 디렉터리의 임시 파일을 fsync한 뒤 교체한다. 실패 시 원본은 보존한다."""
import os
import tempfile


def write_bytes(path, content, *, mode=0o600):
    path = os.path.abspath(path)
    parent = os.path.dirname(path)
    os.makedirs(parent, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.safetyreport-', dir=parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            os.chmod(temporary, mode)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
