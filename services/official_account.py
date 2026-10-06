"""Remote official-account binding and resumable account replacement.

Only hashes and backup paths are journaled; credentials stay in config.ini.
"""
from __future__ import annotations

import json
import os
import re
import threading

CONFIRM = "DELETE_OLD_OFFICIAL_ACCOUNT_DATA"
BLOCKED = {"official_account_mismatch", "official_account_taken", "official_account_change_pending"}
MESSAGES = {
    "official_account_mismatch": "연결된 안전신문고 계정과 설정이 다릅니다. 바인딩된 계정으로 되돌리거나, 기존 데이터를 백업·초기화하고 새 계정으로 시작하세요.",
    "official_account_taken": "이 안전신문고 계정은 이미 다른 카카오 계정에 연결되어 있습니다. 운영자에게 문의해 주세요.",
    "official_account_change_pending": "안전신문고 계정 변경이 완료되지 않았습니다. 같은 새 계정으로 다시 저장하여 백업·초기화 절차를 마쳐 주세요.",
    "official_account_protocol_required": "클라우드의 계정 연결 기능 업데이트가 필요합니다. 잠시 후 다시 시도해 주세요.",
    "cloud_unavailable": "클라우드에 연결할 수 없습니다. 잠시 후 이용해 주세요",
    "official_account_change_confirmation": "기존 데이터가 지워집니다. 개인 DB 백업 후 기존 공유자료와 신고 내역을 초기화합니다. 확인 후 다시 저장하세요.",
}
_lock = threading.RLock()


class BindingError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(MESSAGES.get(code, "계정 변경을 완료하지 못했습니다. 잠시 후 다시 시도해 주세요."))


def binding(status):
    """Legacy field omission skips comparison; explicit null dataset means unbound.

    Neither case authorizes bypassing Kakao/consent or connection registration.
    Malformed fields in an extended response still fail closed.
    """
    if isinstance(status, dict) and "official_account" not in status:
        return None
    value = status.get("official_account") if isinstance(status, dict) else None
    if not isinstance(value, dict) or "dataset_key" not in value or "bound_at" not in value:
        raise BindingError("official_account_protocol_required")
    key = value["dataset_key"]
    if key is not None and (not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key)):
        raise BindingError("official_account_protocol_required")
    if value["bound_at"] is not None and not isinstance(value["bound_at"], str):
        raise BindingError("official_account_protocol_required")
    return key


def _journal_path():
    import settings.settings as settings
    return os.path.join(settings.datapath, "official-account-change.json")


def pending():
    try:
        with open(_journal_path(), encoding="utf-8") as source:
            value = json.load(source)
        if not isinstance(value, dict) or not value.get("target"):
            raise ValueError("invalid account change journal")
        return value
    except FileNotFoundError:
        return None


def _write_journal(value):
    from core.utils.atomic_file import write_bytes
    write_bytes(_journal_path(), json.dumps(value).encode("utf-8"))


def save_settings(command, persist):
    """Serialize whole settings saves with replacement. Never wipe before a verified backup.

    A journal survives failed delete responses, wipe, config save and process restart.
    Only an explicit confirmed resubmission of the same target can resume it.
    """
    import settings.settings as settings
    from services import community_gate as gate, community_auth_service as cas, account_data
    from services.community_account_client import CommunityAccountClient, AccountApiError
    from core.database import database, write_barrier
    from core.database.engine import get_engine
    from core.storage import exchange
    from services.crawl_manager import crawl_manager, RestoreBlocked
    from services.community_store import CommunityStore

    with _lock:
        names = [v for section, key, v in command.values if (section, key) == ("LOGIN", "username")]
        if not names:
            return persist(command)
        old = gate.dataset_key(gate.official_username())
        target = gate.dataset_key(names[-1])
        journal = pending()
        # Even an unchanged manually edited username needs comparison with the remote binding.
        service = cas.get_service()
        _, current = gate._gate._session_state(service)
        user_id = (current or {}).get("user_id")
        status = gate._gate.binding_status()
        if journal and journal.get("user_id") != user_id:
            raise BindingError("official_account_change_pending")
        bound = binding(status)
        changing = bool(journal or (bound and bound != target)
                        or (old and old != target and bound != target))
        if not changing:
            result = persist(command)
            gate.invalidate("official_account_settings_saved")
            return result
        if not target:
            raise BindingError("official_account_mismatch")
        if command.official_account_confirm != CONFIRM:
            raise BindingError("official_account_change_confirmation")
        if journal and journal["target"] != target:
            raise BindingError("official_account_change_pending")
        engine = get_engine()
        try:
            kakao_id = service.current_kakao_id()
        except cas.CommunityAuthError:
            raise BindingError("cloud_unavailable") from None
        if not kakao_id or account_data.db_owner(engine) not in (None, kakao_id):
            raise BindingError("db_owner_mismatch")
        exchange.ensure_restore_allowed(engine)
        try:
            with gate._gate._refresh_lock, crawl_manager.hold_for_restore(), write_barrier.exclusive():
                if journal is None:
                    journal = {"target": target, "phase": "started", "user_id": user_id}
                    _write_journal(journal)
                gate.invalidate("official_account_change_pending")
                if journal["phase"] == "started":
                    backup = exchange._backup_live(settings.db_path)
                    if not backup:
                        raise RuntimeError("personal database backup missing")
                    exchange._integrity_check(backup, readonly=True)
                    journal.update(phase="backed_up", backup=backup)
                    _write_journal(journal)
                if journal["phase"] == "backed_up":
                    _, now = gate._gate._session_state(service)
                    if (now or {}).get("user_id") != user_id:
                        raise BindingError("official_account_change_pending")
                    cfg = service.config()
                    client = CommunityAccountClient(cfg.supabase_url, cfg.publishable_key)
                    response = client.delete_contributions(service.get_access_token())
                    if response.get("official_account_released") is not True:
                        raise BindingError("official_account_protocol_required")
                    journal["phase"] = "released"
                    _write_journal(journal)
                _, now = gate._gate._session_state(service)
                if (now or {}).get("user_id") != user_id:
                    raise BindingError("official_account_change_pending")
                if journal["phase"] == "released":
                    # Repeating this phase after a crash is safe: no work can pass the pending gate.
                    database.empty_report_data(engine, before_empty=lambda: CommunityStore.open().rotate_dataset("official_account_change"))
                    crawl_manager.discard_account_pending()
                    journal["phase"] = "wiped"
                    _write_journal(journal)
                if journal["phase"] != "wiped":
                    raise BindingError("official_account_change_pending")
                database.set_meta(engine, database.KAKAO_MEMBER_META_KEY, kakao_id)
                result = persist(command)
                os.unlink(_journal_path())
                gate.invalidate("official_account_changed")
        except (RestoreBlocked, write_barrier.BarrierTimeout) as exc:
            raise exchange.RestoreRefused(str(exc)) from None
        except (AccountApiError, cas.CommunityAuthError) as exc:
            raise BindingError(exc.code if exc.code in MESSAGES else "cloud_unavailable") from None
        # New connections are registered only after the local wipe + config save succeeded.
        gate.refresh_now()
        return result
