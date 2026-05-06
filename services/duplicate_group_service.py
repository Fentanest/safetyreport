from __future__ import annotations

import hashlib
import re
from datetime import datetime

import pandas as pd
from sqlalchemy import select

from core.database import models
from core.utils import logger


_WHITESPACE_RE = re.compile(r"\s+")
_STATUS_PRIORITY = {
    "수용": 5,
    "일부수용": 4,
    "기타": 3,
    "불수용": 2,
    "답변완료": 1,
    "처리중": 0,
    "진행": 0,
    "진행중": 0,
    "취하": -1,
}
_DUPLICATE_STATUS_LABELS = {
    "review_required": "검토 필요",
    "confirmed_duplicate": "중복 확정",
    "not_duplicate": "중복 아님",
}
_REPRESENTATIVE_MODE_LABELS = {
    "auto": "자동 선정",
    "manual": "수동 고정",
}
_DUPLICATE_STATUS_OPTIONS = {"review_required", "confirmed_duplicate", "not_duplicate"}
_REPRESENTATIVE_MODE_OPTIONS = {"auto", "manual"}
_LEGACY_STATUS_MAP = {
    "auto": "confirmed_duplicate",
    "confirmed": "confirmed_duplicate",
    "review_required": "review_required",
    "excluded": "not_duplicate",
}


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def _log_info(message: str) -> None:
    active_logger = logger.LoggerFactory.get_logger()
    if active_logger is not None:
        active_logger.info(message)


def _normalize_duplicate_status(value, *, default: str = "") -> str:
    normalized = _text(value).lower()
    if normalized in _DUPLICATE_STATUS_OPTIONS:
        return normalized
    if normalized in _LEGACY_STATUS_MAP:
        return _LEGACY_STATUS_MAP[normalized]
    return default


def _normalize_representative_mode(value, *, existing_status: str = "") -> str:
    normalized = _text(value).lower()
    if normalized in _REPRESENTATIVE_MODE_OPTIONS:
        return normalized
    legacy_status = _text(existing_status).lower()
    if legacy_status == "confirmed":
        return "manual"
    return "auto"


def _text(value) -> str:
    if value is None or pd.isna(value):
        return ""
    return str(value).strip()


def _normalize_inline(value) -> str:
    return _WHITESPACE_RE.sub(" ", _text(value))


def normalize_raw_content(raw_content) -> str:
    text = _text(raw_content).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n+", "\n", text)
    return text.strip()


def _payload_hash(raw_content: str) -> str:
    return hashlib.sha256(raw_content.encode("utf-8")).hexdigest()


def _field_fingerprint(record: dict) -> str:
    parts = [
        _normalize_inline(record.get("category")),
        _normalize_inline(record.get("entry_value")),
        _normalize_inline(record.get("차량번호")),
        _normalize_inline(record.get("신고내용")),
        _normalize_inline(record.get("발생일자")),
        _normalize_inline(record.get("발생시각")),
        _normalize_inline(record.get("위반장소")),
    ]
    return "|".join(parts)


def _parse_millis(value) -> int:
    text = _text(value)
    if not text:
        return 0
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return 0
    return int(pd.Timestamp(parsed).to_pydatetime().timestamp() * 1000)


def _priority_tuple(record: dict):
    fine_text = _text(record.get("범칙금_과태료"))
    if "과태료" in fine_text:
        fine_rank = 2
    elif re.search(r"경고|범칙금", fine_text):
        fine_rank = 1
    else:
        fine_rank = 0

    status_rank = _STATUS_PRIORITY.get(_text(record.get("처리상태")), -2)
    answer_rank = _parse_millis(record.get("답변일"))
    synced_rank = 0
    try:
        synced_rank = int(record.get("synced_at") or 0)
    except (TypeError, ValueError):
        synced_rank = 0
    report_number = _text(record.get("신고번호"))
    return (fine_rank, status_rank, answer_rank, synced_rank, report_number)


def _priority_score(record: dict) -> int:
    fine_rank, status_rank, answer_rank, synced_rank, _ = _priority_tuple(record)
    answer_rank //= 1000
    synced_rank //= 1000
    return (
        fine_rank * 10**15
        + max(status_rank + 10, 0) * 10**14
        + min(answer_rank, 10**11 - 1) * 10**3
        + min(synced_rank, 999)
    )


def _choose_representative(records: list[dict]) -> dict:
    return sorted(records, key=_priority_tuple, reverse=True)[0]


def _build_duplicate_change_payload(change_kind: str, group: dict) -> dict:
    status = _normalize_duplicate_status(group.get("status"), default="review_required")
    status_label = _DUPLICATE_STATUS_LABELS.get(status, "중복 변경")
    representative_mode = _normalize_representative_mode(group.get("representative_mode"))
    representative_mode_label = _REPRESENTATIVE_MODE_LABELS.get(representative_mode, "자동 선정")
    representative = dict(group.get("representative") or {})
    members = [dict(member) for member in group.get("members") or []]
    report_number = _text(representative.get("신고번호"))
    member_count = int(group.get("member_count") or len(members) or 0)

    if change_kind == "group_added":
        change_label = "신규 중복군"
        body_title = "중복 신고 그룹이 새로 감지되었습니다."
    elif change_kind == "members_changed":
        change_label = "중복군 변경"
        body_title = "중복 신고 그룹의 멤버 구성이 변경되었습니다."
    else:
        change_label = "대표건 변경"
        body_title = "중복 신고 그룹의 대표건이 자동으로 변경되었습니다."

    body_lines = [body_title]
    if report_number:
        body_lines.append(f"대표 신고번호: {report_number}")
    body_lines.append(f"현재 상태: {status_label}")
    body_lines.append(f"대표건 모드: {representative_mode_label}")
    body_lines.append(f"멤버 수: {member_count}건")

    return {
        "notification_kind": "duplicate",
        "duplicate_change_type": change_kind,
        "change_type": change_label,
        "group_id": _text(group.get("group_id")),
        "status": status,
        "status_label": status_label,
        "representative_mode": representative_mode,
        "representative_mode_label": representative_mode_label,
        "member_count": member_count,
        "representative_id": _text(group.get("representative_id")),
        "representative": representative,
        "members": members,
        "title": f"🧩 {status_label} — {change_label}",
        "body": "\n".join(body_lines),
        "신고번호": report_number,
        "신고명": _text(representative.get("신고명")),
        "처리상태": _text(representative.get("처리상태")),
        "처리기관": _text(representative.get("처리기관")),
        "범칙금_과태료": _text(representative.get("범칙금_과태료")),
    }


def _safe_read(conn, table_obj) -> pd.DataFrame:
    try:
        return pd.read_sql_query(select(table_obj), conn)
    except Exception:
        return pd.DataFrame()


def _load_inventory(conn) -> pd.DataFrame:
    frames = []
    for table_obj, category in [
        (models.merge_traffic_table, "traffic"),
        (models.merge_parking_table, "parking"),
        (models.merge_other_table, "other"),
    ]:
        df = _safe_read(conn, table_obj)
        if df.empty:
            continue
        df["category"] = category
        frames.append(df)

    if not frames:
        return pd.DataFrame()

    df_all = pd.concat(frames, ignore_index=True).fillna("")

    df_entry = _safe_read(conn, models.entry_value_table)
    if not df_entry.empty:
        df_all = df_all.merge(df_entry[["ID", "entry_value"]], on="ID", how="left")
    else:
        df_all["entry_value"] = ""

    df_raw = _safe_read(conn, models.raw_content_table)
    if not df_raw.empty:
        df_all = df_all.merge(df_raw[["ID", "raw_content", "raw_type", "saved_at"]], on="ID", how="left")
    else:
        df_all["raw_content"] = ""
        df_all["raw_type"] = ""
        df_all["saved_at"] = None

    df_all["raw_content"] = df_all["raw_content"].fillna("").astype(str)
    df_all["payload_normalized"] = df_all["raw_content"].apply(normalize_raw_content)
    df_all["payload_hash"] = df_all["payload_normalized"].apply(lambda value: _payload_hash(value) if value else "")
    return df_all


def refresh_duplicate_groups(engine, *, track_changes: bool = False) -> dict[str, object]:
    with engine.begin() as conn:
        df_all = _load_inventory(conn)
        existing_groups = {
            row.group_id: dict(row._mapping)
            for row in conn.execute(select(models.duplicate_group_table)).fetchall()
        }
        existing_members_by_group: dict[str, set[str]] = {}
        if track_changes and existing_groups:
            existing_member_rows = conn.execute(select(models.duplicate_member_table)).fetchall()
            for row in existing_member_rows:
                existing_members_by_group.setdefault(row.group_id, set()).add(_text(row.report_id))

        current_ts = _now_ms()
        group_records = []
        member_records = []
        duplicate_changes: list[dict] = []
        if not df_all.empty:
            duplicate_rows = df_all[df_all["payload_hash"] != ""].copy()
            counts = duplicate_rows["payload_hash"].value_counts()
            duplicate_rows = duplicate_rows[duplicate_rows["payload_hash"].isin(counts[counts > 1].index)]

            for payload_hash, group_df in duplicate_rows.groupby("payload_hash", sort=False):
                records = group_df.to_dict(orient="records")
                if len(records) <= 1:
                    continue

                group_id = payload_hash
                recommended_representative = _choose_representative(records)
                recommended_representative_id = _text(recommended_representative.get("ID"))
                representative_id = recommended_representative_id

                car_values = {value for value in group_df["차량번호"].fillna("").astype(str).str.strip().tolist() if value}
                category_values = {value for value in group_df["category"].fillna("").astype(str).str.strip().tolist() if value}
                entry_values = {value for value in group_df["entry_value"].fillna("").astype(str).str.strip().tolist() if value}
                has_conflict = len(car_values) > 1 or len(category_values) > 1 or len(entry_values) > 1

                existing = existing_groups.get(group_id, {})
                preserved_status = _normalize_duplicate_status(existing.get("status"))
                representative_mode = _normalize_representative_mode(
                    existing.get("representative_mode"),
                    existing_status=existing.get("status"),
                )
                preserved_rep = _text(existing.get("representative_id"))
                preserved_note = existing.get("note")
                created_at = existing.get("created_at") or current_ts

                member_ids = {_text(record.get("ID")) for record in records}
                if representative_mode == "manual" and preserved_rep in member_ids:
                    representative_id = preserved_rep

                default_status = "review_required" if has_conflict else "confirmed_duplicate"
                status = preserved_status or default_status
                apply_globally = 1 if status == "confirmed_duplicate" else 0

                field_fingerprints = [_field_fingerprint(record) for record in records]
                majority_field_fingerprint = max(set(field_fingerprints), key=field_fingerprints.count)

                group_records.append({
                    "group_id": group_id,
                    "fingerprint": payload_hash,
                    "match_type": "payload_exact",
                    "status": status,
                    "representative_mode": representative_mode,
                    "representative_id": representative_id,
                    "member_count": len(records),
                    "apply_globally": apply_globally,
                    "note": preserved_note or "",
                    "created_at": created_at,
                    "updated_at": current_ts,
                })

                ranked_records = sorted(records, key=_priority_tuple, reverse=True)
                members_payload = []
                for rank, record in enumerate(ranked_records, start=1):
                    is_representative = 1 if _text(record.get("ID")) == representative_id else 0
                    member_records.append({
                        "group_id": group_id,
                        "report_id": _text(record.get("ID")),
                        "report_number": _text(record.get("신고번호")),
                        "category": _text(record.get("category")),
                        "is_representative": is_representative,
                        "priority_score": len(ranked_records) - rank + 1,
                        "raw_match": 1,
                        "field_match": 1 if _field_fingerprint(record) == majority_field_fingerprint else 0,
                        "created_at": current_ts,
                        "updated_at": current_ts,
                    })
                    member_payload = dict(record)
                    member_payload.update({
                        "group_id": group_id,
                        "report_id": _text(record.get("ID")),
                        "report_number": _text(record.get("신고번호")),
                        "category": _text(record.get("category")),
                        "is_representative": is_representative,
                        "priority_score": len(ranked_records) - rank + 1,
                        "raw_match": 1,
                        "field_match": 1 if _field_fingerprint(record) == majority_field_fingerprint else 0,
                    })
                    members_payload.append(member_payload)

                if track_changes:
                    previous_group = existing_groups.get(group_id)
                    previous_member_ids = existing_members_by_group.get(group_id, set())
                    current_member_ids = {_text(record.get("ID")) for record in ranked_records}
                    auto_representative_changed = (
                        previous_group is not None
                        and representative_mode == "auto"
                        and _text(previous_group.get("representative_id")) != representative_id
                    )
                    members_changed = previous_group is not None and previous_member_ids != current_member_ids

                    change_kind = ""
                    if previous_group is None:
                        change_kind = "group_added"
                    elif members_changed:
                        change_kind = "members_changed"
                    elif auto_representative_changed:
                        change_kind = "representative_changed"

                    if change_kind:
                        representative_payload = next(
                            (
                                member for member in members_payload
                                if int(member.get("is_representative") or 0) == 1
                            ),
                            {},
                        )
                        duplicate_changes.append(
                            _build_duplicate_change_payload(
                                change_kind,
                                {
                                    "group_id": group_id,
                                    "status": status,
                                    "representative_mode": representative_mode,
                                    "representative_id": representative_id,
                                    "member_count": len(members_payload),
                                    "members": members_payload,
                                    "representative": representative_payload,
                                },
                            )
                        )

        conn.execute(models.duplicate_member_table.delete())
        conn.execute(models.duplicate_group_table.delete())
        if group_records:
            conn.execute(models.duplicate_group_table.insert(), group_records)
        if member_records:
            conn.execute(models.duplicate_member_table.insert(), member_records)

        _log_info(f"[duplicate] payload exact 중복군 {len(group_records)}개, 멤버 {len(member_records)}건 재생성")
        return {
            "group_count": len(group_records),
            "member_count": len(member_records),
            "changes": duplicate_changes,
        }


def _load_group_rows(conn):
    rows = conn.execute(select(models.duplicate_group_table)).fetchall()
    return [dict(row._mapping) for row in rows]


def _load_member_rows(conn, group_ids: list[str]):
    if not group_ids:
        return []
    rows = conn.execute(
        select(models.duplicate_member_table).where(models.duplicate_member_table.c.group_id.in_(group_ids))
    ).fetchall()
    return [dict(row._mapping) for row in rows]


def _load_inventory_lookup(conn) -> dict[str, dict]:
    df_all = _load_inventory(conn)
    if df_all.empty:
        return {}
    records = df_all.to_dict(orient="records")
    return {_text(record.get("ID")): record for record in records}


def get_duplicate_groups(engine, *, status: str | None = None) -> list[dict]:
    with engine.connect() as conn:
        groups = _load_group_rows(conn)
        normalized_status = _normalize_duplicate_status(status)
        if normalized_status:
            groups = [group for group in groups if _normalize_duplicate_status(group.get("status")) == normalized_status]
        if not groups:
            return []

        group_ids = [group["group_id"] for group in groups]
        members = _load_member_rows(conn, group_ids)
        inventory = _load_inventory_lookup(conn)

    members_by_group: dict[str, list[dict]] = {}
    for member in members:
        report_id = _text(member.get("report_id"))
        record = dict(inventory.get(report_id, {}))
        record.update(member)
        members_by_group.setdefault(member["group_id"], []).append(record)

    payload = []
    for group in groups:
        group_members = members_by_group.get(group["group_id"], [])
        group_members.sort(
            key=lambda item: (
                _text(item.get("신고번호")),
                _text(item.get("report_id")),
            ),
            reverse=True,
        )
        representative = next((item for item in group_members if int(item.get("is_representative") or 0) == 1), None)
        payload.append({
            **group,
            "members": group_members,
            "representative": representative or {},
        })

    payload.sort(
        key=lambda item: (
            _text(item.get("representative", {}).get("신고번호")),
            int(item.get("member_count") or 0),
        ),
        reverse=True,
    )
    return payload


def _load_records_for_group(conn, group_id: str) -> list[dict]:
    member_rows = conn.execute(
        select(models.duplicate_member_table).where(models.duplicate_member_table.c.group_id == group_id)
    ).fetchall()
    if not member_rows:
        return []
    inventory = _load_inventory_lookup(conn)
    records = []
    for row in member_rows:
        record = dict(inventory.get(_text(row.report_id), {}))
        if record:
            records.append(record)
    return records


def _resolve_representative_choice(
    records: list[dict],
    *,
    representative_mode: str,
    requested_representative_id: str = "",
    fallback_representative_id: str = "",
) -> str:
    member_ids = {_text(record.get("ID")) for record in records}
    if not member_ids:
        return ""

    if representative_mode == "manual":
        if requested_representative_id in member_ids:
            return requested_representative_id
        if fallback_representative_id in member_ids:
            return fallback_representative_id

    recommended = _choose_representative(records)
    return _text(recommended.get("ID"))


def update_duplicate_group(
    engine,
    group_id: str,
    *,
    representative_id: str | None = None,
    duplicate_status: str | None = None,
    representative_mode: str | None = None,
    note: str | None = None,
) -> bool:
    group_id = _text(group_id)
    if not group_id:
        return False

    with engine.begin() as conn:
        group_row = conn.execute(
            select(models.duplicate_group_table).where(models.duplicate_group_table.c.group_id == group_id)
        ).first()
        if not group_row:
            return False

        current_group = dict(group_row._mapping)
        current_status = _normalize_duplicate_status(current_group.get("status"), default="confirmed_duplicate")
        current_mode = _normalize_representative_mode(
            current_group.get("representative_mode"),
            existing_status=current_group.get("status"),
        )
        updates = {"updated_at": _now_ms()}
        normalized_status = _normalize_duplicate_status(duplicate_status, default=current_status)
        normalized_mode = _normalize_representative_mode(representative_mode, existing_status=current_group.get("status"))
        updates["status"] = normalized_status or current_status
        updates["representative_mode"] = normalized_mode or current_mode

        updates["apply_globally"] = 1 if updates["status"] == "confirmed_duplicate" else 0

        if note is not None:
            updates["note"] = _text(note)

        records = _load_records_for_group(conn, group_id)
        requested_representative_id = _text(representative_id)
        auto_representative_id = _resolve_representative_choice(
            records,
            representative_mode="auto",
            requested_representative_id="",
            fallback_representative_id=_text(current_group.get("representative_id")),
        )
        record_ids = {_text(record.get("ID")) for record in records}
        if (
            updates["representative_mode"] == "auto"
            and requested_representative_id
            and requested_representative_id in record_ids
            and requested_representative_id != auto_representative_id
        ):
            updates["representative_mode"] = "manual"

        resolved_representative_id = _resolve_representative_choice(
            records,
            representative_mode=updates["representative_mode"],
            requested_representative_id=requested_representative_id,
            fallback_representative_id=_text(current_group.get("representative_id")),
        )
        if resolved_representative_id:
            updates["representative_id"] = resolved_representative_id

        conn.execute(
            models.duplicate_group_table.update()
            .where(models.duplicate_group_table.c.group_id == group_id)
            .values(**updates)
        )

        if "representative_id" in updates:
            conn.execute(
                models.duplicate_member_table.update()
                .where(models.duplicate_member_table.c.group_id == group_id)
                .values(is_representative=0, updated_at=updates["updated_at"])
            )
            conn.execute(
                models.duplicate_member_table.update()
                .where(models.duplicate_member_table.c.group_id == group_id)
                .where(models.duplicate_member_table.c.report_id == updates["representative_id"])
                .values(is_representative=1, updated_at=updates["updated_at"])
            )

    return True


def bulk_update_duplicate_status(
    engine,
    group_ids: list[str],
    duplicate_status: str,
    representative_mode: str | None = None,
) -> int:
    normalized_status = _normalize_duplicate_status(duplicate_status)
    normalized_mode = _normalize_representative_mode(representative_mode)
    normalized_ids = [_text(group_id) for group_id in group_ids if _text(group_id)]
    if not normalized_ids:
        return 0

    updates = {"updated_at": _now_ms()}
    if normalized_status:
        updates["status"] = normalized_status
        updates["apply_globally"] = 1 if normalized_status == "confirmed_duplicate" else 0
    if normalized_mode:
        updates["representative_mode"] = normalized_mode

    if len(updates) == 1:
        return 0

    with engine.begin() as conn:
        result = conn.execute(
            models.duplicate_group_table.update()
            .where(models.duplicate_group_table.c.group_id.in_(normalized_ids))
            .values(**updates)
        )
        return int(result.rowcount or 0)


def build_projection_map(conn) -> tuple[dict[str, dict], dict[str, dict]]:
    groups = conn.execute(
        select(models.duplicate_group_table).where(
            models.duplicate_group_table.c.status == "confirmed_duplicate"
        )
    ).fetchall()
    if not groups:
        return {}, {}

    group_map = {row.group_id: dict(row._mapping) for row in groups}
    group_ids = list(group_map.keys())
    members = conn.execute(
        select(models.duplicate_member_table).where(models.duplicate_member_table.c.group_id.in_(group_ids))
    ).fetchall()

    member_map: dict[str, dict] = {}
    group_members: dict[str, dict] = {group_id: {"member_ids": [], "member_count": 0} for group_id in group_ids}
    for row in members:
        meta = dict(row._mapping)
        group_id = meta["group_id"]
        group_members[group_id]["member_ids"].append(meta["report_id"])
        group_members[group_id]["member_count"] += 1
        member_map[meta["report_id"]] = {
            "group_id": group_id,
            "representative_id": group_map[group_id].get("representative_id"),
            "is_representative": int(meta.get("is_representative") or 0) == 1,
            "member_count": group_members[group_id]["member_count"],
        }

    for group_id, info in group_members.items():
        for member_id in info["member_ids"]:
            if member_id in member_map:
                member_map[member_id]["member_count"] = info["member_count"]

    return group_map, member_map


def project_records(engine, records: list[dict], *, mode: str = "raw") -> list[dict]:
    normalized_mode = _text(mode).lower() or "raw"
    if normalized_mode not in {"raw", "canonical"}:
        normalized_mode = "raw"
    if not records:
        return []

    with engine.connect() as conn:
        _, member_map = build_projection_map(conn)

    if not member_map:
        return records

    group_watch_flags: dict[str, str] = {}
    for record in records:
        record_id = _text(record.get("ID"))
        meta = member_map.get(record_id)
        if not meta:
            continue
        if _text(record.get("감시목록")) == "Y":
            group_watch_flags[meta["group_id"]] = "Y"
        elif meta["group_id"] not in group_watch_flags:
            group_watch_flags[meta["group_id"]] = "N"

    projected = []
    for record in records:
        item = dict(record)
        record_id = _text(item.get("ID"))
        meta = member_map.get(record_id)
        if not meta:
            projected.append(item)
            continue

        item["duplicate_group_id"] = meta["group_id"]
        item["duplicate_member_count"] = meta["member_count"]
        item["is_duplicate_representative"] = meta["is_representative"]
        if group_watch_flags.get(meta["group_id"]) == "Y":
            item["감시목록"] = "Y"

        if normalized_mode == "canonical" and not meta["is_representative"]:
            continue
        projected.append(item)

    return projected
