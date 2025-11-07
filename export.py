import settings.settings as settings
import pandas as pd
import os
import gspread
from gspread.exceptions import WorksheetNotFound
import logger
import sys
import subprocess

if settings.google_sheet_enabled:
    gc = gspread.service_account(settings.google_api_auth_file)
    spreadsheet = gc.open_by_key(settings.google_sheet_key)
else:
    gc = None
    spreadsheet = None

def _process_dataframe(df):
    """Handles the common logic of splitting and reordering columns."""
    df_processed = df.copy()
    
    photo_cols = []
    attachment_cols = []

    # Clean and split photo attachment URLs
    if '첨부사진' in df_processed.columns:
        df_processed['첨부사진'] = df_processed['첨부사진'].str.strip()
        df_processed['첨부사진'].replace('', pd.NA, inplace=True)
        if df_processed['첨부사진'].notna().any():
            photos = df_processed['첨부사진'].str.split('\n', expand=True)
            for i in range(photos.shape[1]):
                col_name = f'첨부사진{i+1}'
                df_processed[col_name] = photos[i]
                photo_cols.append(col_name)
        df_processed = df_processed.drop(columns=['첨부사진'])

    # Clean and split file attachment URLs
    if '첨부파일' in df_processed.columns:
        df_processed['첨부파일'] = df_processed['첨부파일'].str.strip()
        df_processed['첨부파일'].replace('', pd.NA, inplace=True)
        if df_processed['첨부파일'].notna().any():
            attachments = df_processed['첨부파일'].str.split('\n', expand=True)
            for i in range(attachments.shape[1]):
                col_name = f'첨부파일{i+1}'
                df_processed[col_name] = attachments[i]
                attachment_cols.append(col_name)
        df_processed = df_processed.drop(columns=['첨부파일'])

    # Reorder columns
    original_cols = df.columns.tolist()
    if '첨부파일' in original_cols: original_cols.remove('첨부파일')
    if '첨부사진' in original_cols: original_cols.remove('첨부사진')
    if '지도' in original_cols: original_cols.remove('지도')

    new_order = original_cols + ['지도'] + photo_cols + attachment_cols
    new_order = [col for col in new_order if col in df_processed.columns]
    
    return df_processed[new_order], photo_cols

def save_to_excel(df):
    """Saves the DataFrame to an Excel file."""
    df.to_excel(os.path.join(settings.resultpath, settings.resultfile), index=False)
    logger.LoggerFactory.logbot.info(f"데이터 엑셀 저장 성공, 저장경로 : {os.path.join(settings.resultpath, settings.resultfile)}")
    if settings.telegram_enabled:
        subprocess.run([sys.executable, "notifier.py", "4/5. 엑셀 파일 생성을 완료했습니다."])

def save_to_google_sheet(df, photo_cols):
    """Saves the DataFrame to a Google Sheet."""
    if not settings.google_sheet_enabled:
        logger.LoggerFactory.logbot.info("Google Sheet 기능이 비활성화되어 구글 시트 저장을 건너뜁니다.")
        return

    df_gsheet = df.copy()
    image_formula = lambda url: f'=image("{url}")' if pd.notna(url) and url and url != "6개월 초과" else url

    # Apply image formulas
    df_gsheet['지도'] = df_gsheet['지도'].apply(image_formula)
    for col in photo_cols:
        if col in df_gsheet:
            df_gsheet[col] = df_gsheet[col].apply(image_formula)

    try:
        worksheet = spreadsheet.worksheet("data")
        logger.LoggerFactory.logbot.debug("data시트를 선택합니다.")
    except WorksheetNotFound:
        logger.LoggerFactory.logbot.warning("data시트가 확인되지 않습니다.")
        worksheet = spreadsheet.add_worksheet(title="data", rows="1000", cols=len(df_gsheet.columns) + 1)
        logger.LoggerFactory.logbot.info("data시트를 생성합니다.")

    worksheet.clear()
    logger.LoggerFactory.logbot.debug("기존 구글 스프레드시트 데이터를 삭제합니다.")

    data_to_upload = [df_gsheet.columns.values.tolist()] + df_gsheet.astype(str).values.tolist()
    worksheet.update(data_to_upload, value_input_option='USER_ENTERED')
    
    worksheet.resize(rows=len(data_to_upload), cols=len(data_to_upload[0]))
    
    if len(data_to_upload) > 1:
        requests = {
            "requests": [
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": worksheet.id,
                            "dimension": "ROWS",
                            "startIndex": 1,
                            "endIndex": len(data_to_upload)
                        },
                        "properties": {"pixelSize": 300},
                        "fields": "pixelSize"
                    }
                }
            ]
        }
        spreadsheet.batch_update(requests)

    logger.LoggerFactory.logbot.info("구글 스프레드시트에 새로운 데이터를 성공적으로 입력하였습니다.")
    if settings.telegram_enabled:
        subprocess.run([sys.executable, "notifier.py", "5/5. 구글 시트 업로드를 완료했습니다."])

def save_results(df):
    """Processes a DataFrame and saves it to both Excel and Google Sheets."""
    if df.empty:
        logger.LoggerFactory.logbot.info("결과 데이터프레임이 비어 있어 저장을 건너뜁니다.")
        return
        
    processed_df, photo_cols = _process_dataframe(df)
    
    save_to_excel(processed_df)
    save_to_google_sheet(processed_df, photo_cols)