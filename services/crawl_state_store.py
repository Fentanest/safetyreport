from __future__ import annotations

import json
import os
from datetime import datetime

import settings.settings as app_settings

from core.database import database


def _state_file(name: str) -> str:
    return os.path.join(app_settings.datapath, name)


def _write_json(path: str, payload):
    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False)


def _read_json(path: str, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as file_obj:
            return json.load(file_obj)
    except Exception:
        return default


def _take_json(path: str, default):
    payload = _read_json(path, default)
    if payload == default and not os.path.exists(path):
        return default
    try:
        os.remove(path)
    except Exception:
        pass
    return payload


def save_crawl_changes(engine, changed_item_ids, duplicate_changes=None):
    duplicate_changes = list(duplicate_changes or [])
    if not changed_item_ids and not duplicate_changes:
        return

    change_type_map = {item["id"]: item["change_type"] for item in changed_item_ids}
    changed_records = database.get_merged_records_by_ids(engine, list(change_type_map.keys()))
    if not changed_records and not duplicate_changes:
        return

    changes = []
    for record in changed_records:
        record_id = record.get("ID", "")
        changes.append({
            "ID": str(record_id),
            "change_type": change_type_map.get(record_id, "변경"),
            "신고번호": str(record.get("신고번호", "")),
            "신고명": str(record.get("신고명", "")),
            "신고일": str(record.get("신고일", "")),
            "처리기관": str(record.get("처리기관", "")),
            "담당자": str(record.get("담당자", "")),
            "처리상태": str(record.get("처리상태", "")),
            "범칙금_과태료": str(record.get("범칙금_과태료", "")),
            "벌점": str(record.get("벌점", "")),
            "답변일": str(record.get("답변일", "")),
            "차량번호": str(record.get("차량번호", "")),
            "위반법규": str(record.get("위반법규", "")),
            "위반장소": str(record.get("위반장소", "")),
            "발생일자": str(record.get("발생일자", "")),
            "발생시각": str(record.get("발생시각", "")),
            "신고내용": str(record.get("신고내용", "")),
            "처리내용": str(record.get("처리내용", "")),
            "첨부사진": str(record.get("첨부사진", "")),
            "첨부파일": str(record.get("첨부파일", "")),
            "지도": str(record.get("지도", "")),
        })

    for duplicate_change in duplicate_changes:
        item = dict(duplicate_change)
        item["notification_kind"] = "duplicate"
        changes.append(item)

    _write_json(_state_file("crawl_changes.json"), changes)


def clear_crawl_changes():
    path = _state_file("crawl_changes.json")
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def peek_crawl_changes():
    return _read_json(_state_file("crawl_changes.json"), [])


def get_and_clear_crawl_changes():
    return _take_json(_state_file("crawl_changes.json"), [])


def save_crawl_done(changed_count: int, *, report_changed_count: int | None = None, duplicate_changed_count: int = 0):
    _write_json(
        _state_file("crawl_done.json"),
        {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "changed_count": changed_count,
            "report_changed_count": changed_count if report_changed_count is None else report_changed_count,
            "duplicate_changed_count": duplicate_changed_count,
        },
    )


def get_and_clear_crawl_done():
    payload = _take_json(_state_file("crawl_done.json"), None)
    return payload


def save_crawl_done_ext(changed_count: int, changes: list):
    report_changes = [change for change in (changes or []) if change.get("notification_kind") != "duplicate"]
    duplicate_changes = [change for change in (changes or []) if change.get("notification_kind") == "duplicate"]
    _write_json(
        _state_file("crawl_done_ext.json"),
        {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "changed_count": changed_count,
            "changes": [
                {
                    "신고번호": change.get("신고번호", ""),
                    "신고명": change.get("신고명", ""),
                    "처리상태": change.get("처리상태", ""),
                    "notification_kind": "report",
                }
                for change in report_changes
            ] + [
                {
                    "notification_kind": "duplicate",
                    "group_id": change.get("group_id", ""),
                    "duplicate_change_type": change.get("duplicate_change_type", ""),
                    "title": change.get("title", ""),
                    "body": change.get("body", ""),
                    "status_label": change.get("status_label", ""),
                    "representative_mode_label": change.get("representative_mode_label", ""),
                    "member_count": change.get("member_count", 0),
                    "representative_report_number": (change.get("representative") or {}).get("신고번호", "") or change.get("신고번호", ""),
                }
                for change in duplicate_changes
            ],
            "duplicate_changed_count": len(duplicate_changes),
        },
    )


def get_and_clear_crawl_done_ext():
    payload = _take_json(_state_file("crawl_done_ext.json"), None)
    return payload
