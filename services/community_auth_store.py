"""커뮤니티 계정(safeauth) 세션 암호화 저장소.

- 파일: <datapath>/auth/community_session.enc (Fernet 암호문, JSON 한 덩어리)
- 키:   <datapath>/auth/.community_key (설치별 Fernet 키, 0600). 설정 암호화 키(.config_key)와 따로 둔다.
- 설치 ID: <datapath>/auth/community_installation_id (중계의 설치별 대기 요청 상한용, 비밀 아님)
- 락:   threading.RLock(프로세스 안) + <datapath>/auth/.community.lock 파일 락(프로세스 사이)

data.db 에는 아무것도 쓰지 않는다(백업·모바일 DB 변환 대상이 아니게 하려는 의도). 읽지 못하는 파일(키 없음·손상)은
덮어쓰지 않고 StoreUnreadable 로 알린다. 명시적 초기화(reset_unreadable)만 파일을 옆으로 치운다.
"""
from __future__ import annotations

import base64
import contextlib
import json
import logging
import os
import re
import secrets
import tempfile
import threading
import time

_log = logging.getLogger("safetyreport.core.community_auth")

SESSION_FILE = "community_session.enc"
KEY_FILE = ".community_key"
LOCK_FILE = ".community.lock"
INSTALL_FILE = "community_installation_id"
STORE_VERSION = 1


class StoreUnreadable(RuntimeError):
    """세션 파일을 복호화할 수 없다(키 없음·키 불일치·손상)."""


def random_b64url(nbytes: int = 32) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(nbytes)).rstrip(b"=").decode("ascii")


def _chmod_600(path: str) -> None:
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _fsync_dir(directory: str) -> None:
    if os.name != "posix":
        return
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write(path: str, data: bytes) -> None:
    """같은 디렉터리의 임시 파일에 쓰고 fsync 후 os.replace 로 바꾼다(0600)."""
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        _chmod_600(tmp)
        os.replace(tmp, path)
        _fsync_dir(directory)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


class _FileLock:
    """프로세스 사이 배타 락. POSIX fcntl.flock, Windows msvcrt.locking."""

    def __init__(self, path: str):
        self.path = path
        self._fh = None

    def acquire(self) -> None:
        fh = open(self.path, "a+b")
        _chmod_600(self.path)
        try:
            if os.name == "nt":  # pragma: no cover - Windows 전용
                import msvcrt
                fh.seek(0)
                while True:
                    try:
                        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
                        break
                    except OSError:
                        time.sleep(0.05)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        except BaseException:
            fh.close()
            raise
        self._fh = fh

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if os.name == "nt":  # pragma: no cover
                import msvcrt
                fh.seek(0)
                with contextlib.suppress(OSError):
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def empty_state() -> dict:
    return {"version": STORE_VERSION, "current": None, "pending": None, "reauth": None, "last_error": None}


class CommunitySessionStore:
    def __init__(self, datapath: str):
        self.auth_dir = os.path.join(datapath, "auth")
        self.session_path = os.path.join(self.auth_dir, SESSION_FILE)
        self.key_path = os.path.join(self.auth_dir, KEY_FILE)
        self.install_path = os.path.join(self.auth_dir, INSTALL_FILE)
        self._rlock = threading.RLock()
        self._file_lock = _FileLock(os.path.join(self.auth_dir, LOCK_FILE))
        self._depth = 0

    # ── 락 ────────────────────────────────────────────────────────────────
    @contextlib.contextmanager
    def locked(self):
        """프로세스 안 RLock + 파일 락. 같은 스레드에서 중첩해도 된다."""
        with self._rlock:
            outer = self._depth == 0
            if outer:
                os.makedirs(self.auth_dir, exist_ok=True)
                self._file_lock.acquire()
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
                if outer:
                    self._file_lock.release()

    # ── 키 ────────────────────────────────────────────────────────────────
    def _read_key(self) -> bytes | None:
        try:
            with open(self.key_path, "rb") as fh:
                key = fh.read().strip()
        except FileNotFoundError:
            return None
        return key or None

    def _create_key(self) -> bytes:
        from cryptography.fernet import Fernet

        os.makedirs(self.auth_dir, exist_ok=True)
        key = Fernet.generate_key()
        try:
            fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            existing = self._read_key()
            if existing:
                return existing
            raise StoreUnreadable("key_file_empty")
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
            fh.flush()
            os.fsync(fh.fileno())
        _chmod_600(self.key_path)
        return key

    def _fernet(self, create: bool):
        from cryptography.fernet import Fernet

        key = self._read_key()
        if key is None:
            if not create:
                return None
            key = self._create_key()
        try:
            return Fernet(key)
        except (ValueError, TypeError) as exc:
            raise StoreUnreadable("key_invalid") from exc

    # ── 읽기/쓰기 ─────────────────────────────────────────────────────────
    def load(self) -> dict:
        """복호화한 상태 dict. 파일이 없으면 빈 상태. 읽을 수 없으면 StoreUnreadable."""
        from cryptography.fernet import InvalidToken

        with self.locked():
            try:
                with open(self.session_path, "rb") as fh:
                    blob = fh.read()
            except FileNotFoundError:
                return empty_state()
            fernet = self._fernet(create=False)
            if fernet is None:
                raise StoreUnreadable("key_missing")
            try:
                data = json.loads(fernet.decrypt(blob).decode("utf-8"))
            except InvalidToken as exc:
                raise StoreUnreadable("decrypt_failed") from exc
            except (ValueError, UnicodeDecodeError) as exc:
                raise StoreUnreadable("corrupt") from exc
            if not isinstance(data, dict) or data.get("version") != STORE_VERSION:
                raise StoreUnreadable("unknown_format")
            state = empty_state()
            state.update({k: data.get(k) for k in state if k != "version"})
            return state

    def is_readable(self) -> bool:
        try:
            self.load()
            return True
        except StoreUnreadable:
            return False

    def save(self, state: dict) -> None:
        """상태 전체를 원자적으로 저장. 기존 파일을 읽을 수 없으면 덮어쓰지 않는다."""
        with self.locked():
            if os.path.exists(self.session_path):
                self.load()  # StoreUnreadable 이면 여기서 멈춘다(조용히 덮어쓰지 않음)
            payload = empty_state()
            payload.update({k: state.get(k) for k in payload if k != "version"})
            if not any(payload[k] for k in ("current", "pending", "reauth", "last_error")):
                self._remove_session_file()
                return
            fernet = self._fernet(create=True)
            blob = fernet.encrypt(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            atomic_write(self.session_path, blob)

    def _remove_session_file(self) -> None:
        with contextlib.suppress(FileNotFoundError):
            os.remove(self.session_path)
            _fsync_dir(self.auth_dir)

    def reset_unreadable(self) -> str | None:
        """읽을 수 없는 세션 파일을 옆으로 치우고(삭제하지 않음) 새로 시작할 수 있게 한다."""
        with self.locked():
            if not os.path.exists(self.session_path) or self.is_readable():
                return None
            target = f"{self.session_path}.unreadable-{time.strftime('%Y%m%d%H%M%S')}"
            os.replace(self.session_path, target)
            _chmod_600(target)
            _log.warning("[community] 읽을 수 없는 커뮤니티 세션 파일을 %s 로 옮겼습니다.", os.path.basename(target))
            return target

    # ── 설치 ID ───────────────────────────────────────────────────────────
    def installation_id(self) -> str:
        with self.locked():
            try:
                with open(self.install_path, "r", encoding="ascii") as fh:
                    value = fh.read().strip()
                if re.fullmatch(r"[A-Za-z0-9_-]{22,64}", value):
                    return value
            except (FileNotFoundError, UnicodeDecodeError):
                pass
            value = random_b64url(24)
            atomic_write(self.install_path, value.encode("ascii"))
            return value
