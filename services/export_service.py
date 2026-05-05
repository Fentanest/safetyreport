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
        if dataframe.empty:
            continue
        processed_df, photo_cols = export._process_dataframe(dataframe)
        excel_data[label] = processed_df
        sheet_data[label] = (processed_df, photo_cols)
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
