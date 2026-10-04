"""EO R-03: 중복 대표건 투영의 공개 어댑터(duplicate_group_service.ProjectionContext·canonical_sql).

같은 snapshot 에서 SQL·DataFrame·레코드·페이지 투영이 같은 행과 메타를 내는지 본다.
상황: 수동 대표건, 비대표건만 감시 중인 확정 그룹, not_duplicate 그룹.
"""

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("SAFETYREPORT_DATA_DIR", tempfile.mkdtemp(prefix="sr-r03-"))

import pandas as pd
from sqlalchemy import create_engine, select

from core.database import database, models
from core.utils import logger
from scripts.dev import fixture_server
from services import duplicate_group_service as dgs
from services import report_query_service as rqs
from services import report_stats_service as rss

TABLES = {"traffic": database.merge_traffic_table, "parking": database.merge_parking_table,
          "other": database.merge_other_table}
GETTERS = {"traffic": rqs.get_traffic_records, "parking": rqs.get_parking_records, "other": rqs.get_other_records}
META = ("duplicate_group_id", "duplicate_member_count", "is_duplicate_representative", "감시목록")


class ProjectionContextTest(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode="crawl")
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(tmp.name) / 'data.db'}")
        self.addCleanup(self.engine.dispose)
        fixture_server.seed_engine(self.engine)
        with self.engine.connect() as conn:
            group = conn.execute(select(models.duplicate_group_table)).mappings().one()
            members = sorted(r[0] for r in conn.execute(select(models.duplicate_member_table.c.report_id)))
            self.category = next(name for name, table in TABLES.items()
                                 if conn.execute(select(table.c.ID).where(table.c.ID == members[0])).first())
            others = [(r[0], r[1]) for r in conn.execute(
                select(TABLES[self.category].c.ID, TABLES[self.category].c.신고번호)
                .where(TABLES[self.category].c.ID.not_in(members)).order_by(TABLES[self.category].c.ID))]
        # 수동 대표건: 원래 대표가 아닌 쪽을 대표로 고르고, 원래 대표(이제 비대표)만 감시한다.
        self.group_id = group["group_id"]
        self.rep, self.non_rep = members[1], members[0]
        self.assertTrue(dgs.update_duplicate_group(self.engine, self.group_id, duplicate_status="confirmed_duplicate",
                                                   representative_mode="manual", representative_id=self.rep))
        with self.engine.begin() as conn:
            number = conn.execute(select(TABLES[self.category].c.신고번호)
                                  .where(TABLES[self.category].c.ID == self.non_rep)).scalar_one()
            conn.execute(models.watchlist_table.delete())
            conn.execute(models.watchlist_table.insert().values(신고번호=number))
            # not_duplicate 그룹: 두 신고 모두 canonical 에도 남아야 한다.
            self.loose = [rid for rid, _ in others[:2]]
            conn.execute(models.duplicate_group_table.insert().values(
                group_id="loose", fingerprint="loose", match_type="field", status="not_duplicate",
                representative_mode="auto", representative_id=self.loose[0], member_count=2))
            for i, (rid, number) in enumerate(others[:2]):
                conn.execute(models.duplicate_member_table.insert().values(
                    group_id="loose", report_id=rid, report_number=number, category=self.category,
                    is_representative=1 if i == 0 else 0))

    def _frame(self, conn, table):
        # 목록과 같은 기본 조건(취하 제외 설정 포함)
        frame = pd.read_sql_query(rqs._build_records_query(table), conn)
        watch = {r[0] for r in conn.execute(select(models.watchlist_table.c.신고번호))}
        frame["감시목록"] = frame["신고번호"].map(lambda n: "Y" if n in watch else "N")
        return frame

    def test_sql_frame_records_and_pages_agree(self):
        for mode in ("raw", "canonical"):
            for category, table in TABLES.items():
                with self.subTest(mode=mode, category=category), self.engine.connect() as conn:
                    projection = dgs.ProjectionContext.load(conn, mode)
                    sql_ids = sorted(str(r.ID) for r in conn.execute(
                        dgs.canonical_sql(table, rqs._build_records_query(table), mode)))
                    frame = projection.project_frame(self._frame(conn, table))
                    self.assertEqual(sorted(frame["ID"].astype(str)), sql_ids)
                    records = GETTERS[category](self.engine, mode=mode)
                    self.assertEqual(sorted(str(r["ID"]) for r in records), sql_ids)
                    pages, offset = [], 0
                    while offset is not None:
                        page = rqs.get_report_page(self.engine, category, offset=offset, limit=1, mode=mode)
                        pages.extend(page["data"])
                        offset = page["next_offset"]
                    self.assertEqual(sorted(str(r["ID"]) for r in pages), sql_ids)
                    if mode == "canonical":
                        full = {str(r["ID"]): r for r in records}
                        for row in pages:
                            self.assertEqual({k: row.get(k) for k in META}, {k: full[str(row["ID"])].get(k) for k in META},
                                             row["ID"])

    def test_canonical_keeps_representative_with_inherited_watch_and_not_duplicate_members(self):
        table = TABLES[self.category]
        with self.engine.connect() as conn:
            projection = dgs.ProjectionContext.load(conn, "canonical")
            frame = projection.project_frame(self._frame(conn, table))
        self.assertEqual(projection.excluded_ids, {self.non_rep})
        self.assertNotIn(self.non_rep, set(frame["ID"]))
        self.assertEqual(frame.loc[frame["ID"] == self.rep, "감시목록"].tolist(), ["Y"])
        self.assertTrue(set(self.loose) <= set(frame["ID"]))
        records = {str(r["ID"]): r for r in GETTERS[self.category](self.engine, mode="canonical")}
        self.assertEqual(records[self.rep]["감시목록"], "Y")
        self.assertTrue(records[self.rep]["is_duplicate_representative"])
        page = rqs.get_report_page(self.engine, self.category, offset=0, limit=500, mode="canonical")
        rep = next(r for r in page["data"] if str(r["ID"]) == self.rep)
        self.assertEqual(rep["감시목록"], "Y")

    def test_raw_record_projection_adds_meta_but_frame_projection_does_not(self):
        table = TABLES[self.category]
        records = {str(r["ID"]): r for r in GETTERS[self.category](self.engine, mode="raw")}
        self.assertIn(self.non_rep, records)
        with self.engine.connect() as conn:
            projection = dgs.ProjectionContext.load(conn, "raw")
            frame = self._frame(conn, table)
        self.assertEqual(projection.members, {})
        self.assertIs(projection.project_frame(frame), frame)

    def test_stats_frames_use_the_same_projection(self):
        _, df_t, df_p, df_o = rss._load_stats_frames(self.engine, None, "canonical")
        ids = set(pd.concat([df_t, df_p, df_o])["ID"].astype(str))
        self.assertNotIn(self.non_rep, ids)
        self.assertIn(self.rep, ids)
        self.assertTrue(set(self.loose) <= ids)

    def test_mode_normalization_is_shared(self):
        for value, expected in ((None, "raw"), ("", "raw"), (" Canonical ", "canonical"), ("x", "raw")):
            self.assertEqual(dgs.normalize_mode(value), expected)
        self.assertIs(rss._normalize_mode, dgs.normalize_mode)
        self.assertIs(rqs._normalize_mode, dgs.normalize_mode)


if __name__ == "__main__":
    unittest.main()
