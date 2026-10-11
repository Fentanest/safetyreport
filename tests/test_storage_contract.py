"""저장 계약(contracts/storage-contract.json) ↔ 서버 SQLAlchemy 스키마 일치 (저장 계층 재설계 R0).

계약에 없는 열·표가 생기거나 타입이 달라지면 실패한다. 새 열을 넣을 때는 계약 파일을 먼저 고치고
모바일 레포의 같은 파일도 바이트 동일하게 맞춘다(scripts/dev/db_roundtrip_check.py 가 비교).
"""
import json
import unittest
from pathlib import Path

from core.database import models

CONTRACT = json.loads((Path(__file__).resolve().parents[1] / "contracts" / "storage-contract.json").read_text(encoding="utf-8"))
SQL_TYPES = {"String": "text", "Integer": "integer", "Float": "real"}


def _server_columns(table_name):
    table = models.metadata.tables[table_name]
    return {c.name: (SQL_TYPES[type(c.type).__name__], bool(c.primary_key)) for c in table.columns}


class StorageContractServerTests(unittest.TestCase):
    def test_report_tables_match_contract(self):
        """신고 = mysafety_report 한 행(스키마 6). report·effective 두 표가 같은 열 모양이다."""
        report = next(e for e in CONTRACT["entities"] if e["entity"] == "report")
        tables = report["server_tables"]
        for placement, name in tables.items():
            expected = {c["name"]: (c["type"], bool(c.get("pk"))) for c in report["columns"] if placement in c["server"]}
            with self.subTest(table=name):
                self.assertEqual(_server_columns(name), expected)

    def test_other_entities_match_contract(self):
        for entity in CONTRACT["entities"]:
            if entity["entity"] == "report" or not entity.get("server_table"):
                continue
            expected = {c["name"]: (c["type"], bool(c.get("pk"))) for c in entity["columns"] if c["server"]}
            with self.subTest(entity=entity["entity"]):
                self.assertEqual(_server_columns(entity["server_table"]), expected)

    def test_every_server_table_is_in_contract(self):
        covered = set()
        for entity in CONTRACT["entities"]:
            if entity["entity"] == "report":
                covered |= set(entity["server_tables"].values())
            elif entity.get("server_table"):
                covered.add(entity["server_table"])
        self.assertEqual(set(models.metadata.tables), covered)

    def test_change_tracked_columns_match_contract(self):
        from core.storage import reports_repo

        self.assertEqual(list(reports_repo.CHANGE_TRACKED_COLUMNS), CONTRACT["change_tracked"]["columns"])

    def test_owners_are_declared(self):
        owners = set(CONTRACT["owners"])
        for entity in CONTRACT["entities"]:
            for col in entity["columns"]:
                with self.subTest(entity=entity["entity"], column=col["name"]):
                    self.assertIn(col["owner"], owners)


if __name__ == "__main__":
    unittest.main()
