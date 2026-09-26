"""큐 신고번호 → 내부 ID 해석(서버 요청 검사와 크롤러가 같은 규칙을 쓴다, S-24·감사 R6-02·R7-03).

정확 일치 → 'SPP-' 를 붙인 정확 일치 → 부분 일치가 딱 1건일 때만. 부분 일치가 2건 이상이면 '모호' — 어느 신고인지 정할 수 없다.
"""
from __future__ import annotations

from sqlalchemy import select


def resolve_exact(conn, item: str):
    """정확 일치 또는 'SPP-' 를 붙인 정확 일치만(부분 일치 없음). 목록 일부만 받은 상태에서도 믿을 수 있는 해석(감사 R8-01)."""
    from core.database import database

    title = database.title_table
    for candidate in dict.fromkeys([item, item if item.startswith("SPP-") else f"SPP-{item}"]):
        found = conn.execute(select(title.c.ID).where(title.c.신고번호 == candidate)).scalar()
        if found:
            return found
    return None


def resolve_detail(conn, item: str):
    """(ID 또는 None, 모호 여부). 부분 일치는 **목록 전체를 받은 DB** 에서만 믿을 수 있다 — 호출자가 보장한다(R8-01)."""
    from core.database import database

    title = database.title_table
    found = resolve_exact(conn, item)
    if found:
        return found, False
    matches = conn.execute(select(title.c.ID).where(title.c.신고번호.like(f"%{item}%")).limit(2)).scalars().all()
    return (matches[0], False) if len(matches) == 1 else (None, len(matches) > 1)


def ambiguous_numbers(engine, numbers) -> list[str]:
    """지금 DB 에서 여러 신고에 걸리는 번호들(요청 거부용). DB 에 없는 번호는 모호가 아니다(목록 탐색으로 찾는다)."""
    out = []
    with engine.connect() as conn:
        for n in numbers:
            n = str(n).strip()
            if n and (n.startswith("SPP-") or "-" in n) and resolve_detail(conn, n)[1]:
                out.append(n)
    return out
