"""Synthetic multi-law rows for isolated browser contract checks."""
from sqlalchemy import update

def seed_law_rows(engine):
    from core.utils.runtime_mode import is_fixture_mode
    from core.database import models
    if not is_fixture_mode():
        raise RuntimeError('Law-search fixtures require fixture mode')
    values = {
        '90000001': '도로교통법 제32조\n도로교통법 제33조\n도로교통법 제34조',
        '90000002': '도로교통법 제17조제4항\n자동차관리법 제10조',
        '90000003': '도로교통법 제170조',
        '90000004': '도로교통법 제17조의2',
        '90000005': '도로교통법 제17조',
    }
    with engine.begin() as conn:
        for rid, law in values.items():
            for table in (models.report_table, models.report_effective_table):
                conn.execute(update(table).where(table.c.ID == rid).values(위반법규=law, 처리상태='수용', 상태='답변완료', 범칙금_과태료='과태료: 40,000원', category='traffic'))
