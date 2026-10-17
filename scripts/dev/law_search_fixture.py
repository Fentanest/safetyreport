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
        # 2026-10-16 막대==드릴다운 경계: 법령명만, 쉼표가 든 한 줄 사용자 수정값, 조의 오타, 두 조 한 줄, 다른 법의 같은 조.
        '90000006': '도로교통법',
        '90000007': '도로교통법 제5조, 제6조',
        '90000008': '도로교통법 제32조의21항',
        '90000009': '자동차관리법 제29조 제30조',
        '90000010': '주차장법 제33조',
        # 2026-10-17 쉼표 뒤 조문에 항이 붙은 한 줄: 목록 검색칸에 다시 넣으면 OR 로 나뉘어 0건이 되던 경계.
        '90000201': '주차장법 제19조, 제29조 제1항',
    }
    with engine.begin() as conn:
        for rid, law in values.items():
            for table in (models.report_table, models.report_effective_table):
                conn.execute(update(table).where(table.c.ID == rid).values(위반법규=law, 처리상태='수용', 상태='답변완료', 범칙금_과태료='과태료: 40,000원', category='traffic'))
