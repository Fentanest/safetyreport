from __future__ import annotations

import pandas as pd


TITLE_COLUMNS = ["ID", "상태", "신고번호", "신고명", "신고일", "만족도조사여부", "감시목록"]


def build_title_dataframe(
    report_id: str,
    state: str,
    report_number: str,
    report_title: str,
    report_date: str,
    poll_status: str,
    *,
    watchlist_status: str = "N",
):
    return pd.DataFrame(
        [[report_id, state, report_number, report_title, report_date, poll_status, watchlist_status]],
        columns=TITLE_COLUMNS,
    )
