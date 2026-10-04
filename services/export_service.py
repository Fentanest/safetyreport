from __future__ import annotations

from core.database import database
from core.utils import export


def build_export_payloads(engine):
    category_dfs = database.load_results_by_category(engine=engine)
    if not any(not dataframe.empty for dataframe in category_dfs.values()):
        return None

    excel_data = {}
    sheet_data = {}
    for label, dataframe in category_dfs.items():
        processed_df, photo_cols = export._process_dataframe(dataframe)
        # 0건 분류도 구글 시트에는 머리글만 보내 이전 행을 비운다 — 빠지면 그 시트의 옛 신고가 남았다(기술일지 A2-07).
        # 엑셀은 파일을 통째로 새로 쓰므로 빈 시트를 만들지 않는다(기존 동작).
        sheet_data[label] = (processed_df, photo_cols)
        if not dataframe.empty:
            excel_data[label] = processed_df
    return excel_data, sheet_data


def export_results(engine, *, save_excel: bool = True, save_sheet: bool = True) -> bool:
    payloads = build_export_payloads(engine)
    if payloads is None:
        return False

    excel_data, sheet_data = payloads
    if save_excel:
        export.save_to_excel(excel_data)
    if save_sheet:
        export.save_to_google_sheet(sheet_data, photo_cols=None)
    return True
