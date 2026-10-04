"""관리자 계정·API 키 저장소(EO R-06에서 database.py 에서 분리). database 모듈이 같은 이름을 다시 내보낸다."""
import threading
from datetime import datetime

from sqlalchemy import func, select, update

from .models import admin_users_table, api_keys_table

# ── 관리자 계정 CRUD ─────────────────────────────────────────────────────────

def has_admin_user(engine) -> bool:
    with engine.connect() as conn:
        count = conn.execute(select(func.count()).select_from(admin_users_table)).scalar()
        return count > 0


def get_admin_user(engine, username: str):
    with engine.connect() as conn:
        result = conn.execute(
            select(admin_users_table).where(admin_users_table.c.username == username)
        ).first()
        return dict(result._mapping) if result else None


def create_admin_user(engine, username: str, password: str):
    from core.utils.security import hash_password
    salt, pwd_hash = hash_password(password)
    with engine.begin() as conn:
        conn.execute(admin_users_table.insert().values(
            username=username, password_hash=pwd_hash, salt=salt
        ))


_first_admin_lock = threading.Lock()


def create_first_admin_user(engine, username: str, password: str) -> bool:
    """최초 설정: 관리자가 없을 때만 만든다. 확인과 삽입을 한 잠금·한 트랜잭션에서 해 동시 요청이 관리자를 둘 만들지 못한다
    (기술일지 A1-08). 이미 있으면 False."""
    from core.utils.security import hash_password
    salt, pwd_hash = hash_password(password)
    with _first_admin_lock, engine.begin() as conn:
        if conn.execute(select(func.count()).select_from(admin_users_table)).scalar():
            return False
        conn.execute(admin_users_table.insert().values(username=username, password_hash=pwd_hash, salt=salt))
    return True


def update_admin_user(engine, old_username: str, new_username: str, new_password: str):
    from core.utils.security import hash_password
    salt, pwd_hash = hash_password(new_password)
    with engine.begin() as conn:
        conn.execute(
            update(admin_users_table)
            .where(admin_users_table.c.username == old_username)
            .values(username=new_username, password_hash=pwd_hash, salt=salt)
        )


# ── API Key CRUD ──────────────────────────────────────────────────────────────

def create_api_key(engine, name: str) -> str:
    import uuid
    key = "sk-" + uuid.uuid4().hex
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with engine.begin() as conn:
        conn.execute(api_keys_table.insert().values(key=key, name=name, created_at=created_at))
    return key


def get_all_api_keys(engine) -> list:
    with engine.connect() as conn:
        result = conn.execute(select(api_keys_table).order_by(api_keys_table.c.created_at.desc()))
        return [dict(row._mapping) for row in result]


def delete_api_key(engine, key: str):
    with engine.begin() as conn:
        conn.execute(api_keys_table.delete().where(api_keys_table.c.key == key))


def validate_api_key(engine, key: str) -> bool:
    with engine.connect() as conn:
        result = conn.execute(
            select(api_keys_table).where(api_keys_table.c.key == key)
        ).first()
        return result is not None

def get_api_key_name(engine, key: str) -> str:
    with engine.connect() as conn:
        result = conn.execute(
            select(api_keys_table.c.name).where(api_keys_table.c.key == key)
        ).first()
        return result[0] if result else "알 수 없는 기기"
