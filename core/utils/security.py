import os
import hashlib
import hmac
import secrets


# ── Config 값 암호화/복호화 (Fernet 대칭키) ────────────────────────────────

def _get_or_create_config_key(datapath: str) -> bytes:
    key_path = os.path.join(datapath, 'auth', '.config_key')
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    if os.path.exists(key_path):
        with open(key_path, 'rb') as f:
            return f.read().strip()
    from cryptography.fernet import Fernet
    key = Fernet.generate_key()
    with open(key_path, 'wb') as f:
        f.write(key)
    return key


def encrypt_config_value(value: str, datapath: str) -> str:
    """평문 값을 암호화하여 'enc:<ciphertext>' 형태로 반환합니다."""
    if not value:
        return value
    from cryptography.fernet import Fernet
    key = _get_or_create_config_key(datapath)
    return 'enc:' + Fernet(key).encrypt(value.encode('utf-8')).decode('utf-8')


def decrypt_config_value(value: str, datapath: str) -> str:
    """'enc:<ciphertext>' 형태의 값을 복호화합니다. 평문이면 그대로 반환 (하위 호환)."""
    if not value or not value.startswith('enc:'):
        return value
    key_path = os.path.join(datapath, 'auth', '.config_key')
    if not os.path.exists(key_path):
        return ''
    try:
        from cryptography.fernet import Fernet
        key = _get_or_create_config_key(datapath)
        return Fernet(key).decrypt(value[4:].encode('utf-8')).decode('utf-8')
    except Exception:
        return ''


# ── 관리자 비밀번호 해싱 (PBKDF2-HMAC-SHA256) ───────────────────────────────

def hash_password(password: str) -> tuple:
    """(salt, pwd_hash) 튜플 반환. 둘 다 hex 문자열."""
    salt = secrets.token_hex(32)
    pwd_hash = hashlib.pbkdf2_hmac(
        'sha256', password.encode('utf-8'), salt.encode('utf-8'), 260000
    )
    return salt, pwd_hash.hex()


def verify_password(password: str, salt: str, pwd_hash: str) -> bool:
    """입력된 비밀번호가 저장된 해시와 일치하는지 확인합니다."""
    computed = hashlib.pbkdf2_hmac(
        'sha256', password.encode('utf-8'), salt.encode('utf-8'), 260000
    )
    return hmac.compare_digest(computed.hex(), pwd_hash)


# ── 세션 서명 키 관리 ────────────────────────────────────────────────────────

def get_or_create_session_key(datapath: str) -> str:
    """앱 재시작 시에도 세션이 유지되도록 키를 파일에 영속 저장합니다."""
    key_path = os.path.join(datapath, 'auth', '.session_key')
    os.makedirs(os.path.dirname(key_path), exist_ok=True)
    if os.path.exists(key_path):
        with open(key_path, 'r', encoding='utf-8') as f:
            val = f.read().strip()
            if val:
                return val
    key = secrets.token_hex(32)
    with open(key_path, 'w', encoding='utf-8') as f:
        f.write(key)
    return key
