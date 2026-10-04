import settings.settings as settings
import pandas as pd
import os
import gspread
from gspread.exceptions import WorksheetNotFound, SpreadsheetNotFound, APIError
from core.utils import logger
import sys
import subprocess
import time
import tempfile
from core.utils.path_utils import resource_path, is_frozen

import threading
from core.utils.runtime_mode import block_if_fixture

gc = spreadsheet = None
_google_identity = None
_google_lock = threading.RLock()


def _get_google_spreadsheet():
    global gc, spreadsheet, _google_identity
    block_if_fixture('google sheet export')
    info = os.stat(settings.google_api_auth_file)
    identity = (settings.google_api_auth_file, settings.google_sheet_key,
                info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if spreadsheet is None or _google_identity != identity:
        client = gspread.service_account(settings.google_api_auth_file)
        client.set_timeout(20)
        opened = client.open_by_key(settings.google_sheet_key)
        gc, spreadsheet, _google_identity = client, opened, identity
    return spreadsheet

def _process_dataframe(df):
    """Handles the common logic of splitting and reordering columns."""
    df_processed = df.copy()
    
    photo_cols = []
    attachment_cols = []

    # Clean and split photo attachment URLs
    if '첨부사진' in df_processed.columns:
        df_processed['첨부사진'] = df_processed['첨부사진'].str.strip()
        df_processed['첨부사진'] = df_processed['첨부사진'].replace('', pd.NA)
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
        df_processed['첨부파일'] = df_processed['첨부파일'].replace('', pd.NA)
        if df_processed['첨부파일'].notna().any():
            attachments = df_processed['첨부파일'].str.split('\n', expand=True)
            for i in range(attachments.shape[1]):
                col_name = f'첨부파일{i+1}'
                df_processed[col_name] = attachments[i]
                attachment_cols.append(col_name)
        df_processed = df_processed.drop(columns=['첨부파일'])

    # Reorder columns: 만족도조사여부 → 별점 → 별점사유 → 감시목록 순으로 끝에 배치
    original_cols = df.columns.tolist()
    for col in ('첨부파일', '첨부사진', '지도', '만족도조사여부', '별점', '별점사유', '감시목록'):
        if col in original_cols:
            original_cols.remove(col)

    new_order = (original_cols + ['지도'] + photo_cols + attachment_cols
                 + ['만족도조사여부', '별점', '별점사유', '감시목록'])
    new_order = [col for col in new_order if col in df_processed.columns]
    
    df_processed = df_processed[new_order]
    
    if '신고번호' in df_processed.columns:
        df_processed = df_processed.sort_values(by='신고번호', ascending=False)
        
    return df_processed, photo_cols

def save_to_excel(data):
    """Saves data to an Excel file.

    data: pd.DataFrame (legacy) → 'data' 시트 단일 저장
          dict[str, pd.DataFrame] → 카테고리별 시트로 저장 ('교통위반' 등)"""
    out_path = os.path.join(settings.resultpath, settings.resultfile)
    fd, temporary = tempfile.mkstemp(prefix='.excel-', suffix='.xlsx', dir=settings.resultpath)
    os.close(fd)
    try:
        if isinstance(data, dict):
            with pd.ExcelWriter(temporary, engine='openpyxl') as writer:
                wrote_any = False
                for sheet_name, df in data.items():
                    if df is None or df.empty:
                        continue
                    df.to_excel(writer, sheet_name=sheet_name, index=False)
                    wrote_any = True
                if not wrote_any:
                    pd.DataFrame().to_excel(writer, sheet_name='교통위반', index=False)
        else:
            data.to_excel(temporary, index=False)
        with open(temporary, 'r+b') as output:
            os.fsync(output.fileno())
        os.replace(temporary, out_path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    logger.LoggerFactory.logbot.info(f"데이터 엑셀 저장 성공, 저장경로 : {out_path}")
    if settings.telegram_enabled:
        if is_frozen:
            subprocess.run([sys.executable, "--mode", "notify", "3/5. 엑셀 파일 생성을 완료했습니다."], timeout=120, check=True)
        else:
            notifier_path = resource_path("core/utils/notifier.py")
            subprocess.run([sys.executable, notifier_path, "3/5. 엑셀 파일 생성을 완료했습니다."], timeout=120, check=True)

_LEGACY_SHEET_NAME = "data"  # 기존 통합 시트(있으면 삭제)
_CATEGORY_SHEETS = ["교통위반", "주정차위반", "기타위반"]


def _ensure_worksheet(title, rows, cols):
    """워크시트가 없으면 생성, 있으면 반환 + 크기 보정."""
    try:
        ws = spreadsheet.worksheet(title)
        if ws.row_count < rows or ws.col_count < cols:
            ws.resize(rows=max(ws.row_count, rows), cols=max(ws.col_count, cols))
        return ws
    except WorksheetNotFound:
        logger.LoggerFactory.logbot.info(f"'{title}' 시트가 없어 새로 생성합니다.")
        return spreadsheet.add_worksheet(title=title, rows=rows + 100, cols=cols + 5)


def _drop_legacy_data_sheet():
    """기존 통합 'data' 시트가 있다면 삭제 (사용자가 카테고리 분리 요청)."""
    try:
        ws = spreadsheet.worksheet(_LEGACY_SHEET_NAME)
        spreadsheet.del_worksheet(ws)
        logger.LoggerFactory.logbot.info("레거시 'data' 시트를 삭제했습니다.")
    except WorksheetNotFound:
        pass
    except Exception as e:
        logger.LoggerFactory.logbot.warning(f"레거시 'data' 시트 삭제 실패: {e}")


def _upload_to_worksheet(worksheet, data_to_upload, *, deadline=None):
    """리트라이/청크 업로드 + 시트 크기 정렬 + 행 픽셀 조정."""
    max_retries = 4
    retry_delay = 8
    chunk_size = 200

    for attempt in range(max_retries):
        if deadline is not None and time.monotonic() >= deadline:
            return False
        try:
            logger.LoggerFactory.logbot.info(
                f"'{worksheet.title}' 시트 업로드 시작 (시도 {attempt + 1}/{max_retries}, {len(data_to_upload)}행)"
            )
            worksheet.clear()
            for i in range(0, len(data_to_upload), chunk_size):
                if deadline is not None and time.monotonic() >= deadline:
                    return False
                chunk = data_to_upload[i:i + chunk_size]
                start_range = f'A{i + 1}'
                worksheet.update(chunk, range_name=start_range, value_input_option='USER_ENTERED')
                logger.LoggerFactory.logbot.debug(f"{i + len(chunk) - 1}행까지 업로드 완료...")
                time.sleep(2)

            worksheet.resize(rows=len(data_to_upload), cols=len(data_to_upload[0]))

            if len(data_to_upload) > 1:
                requests = {
                    "requests": [{
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
                    }]
                }
                spreadsheet.batch_update(requests)

            logger.LoggerFactory.logbot.info(f"'{worksheet.title}' 시트 업로드 완료")
            return True

        except APIError as e:
            logger.LoggerFactory.logbot.error(f"'{worksheet.title}' 업로드 중 API 오류 (시도 {attempt + 1}): {e}")
            if attempt < max_retries - 1:
                if deadline is not None and time.monotonic() + retry_delay >= deadline:
                    return False
                time.sleep(retry_delay)
            else:
                logger.LoggerFactory.logbot.error(f"'{worksheet.title}' 최대 재시도 초과. 업로드 실패.")
                return False


def _df_to_sheet_data(df, photo_cols):
    """DataFrame → 구글 시트 업로드용 2D 리스트로 변환 (이미지 수식 포함)."""
    df_g = df.copy()
    image_formula = lambda url: f'=image("{url}")' if pd.notna(url) and url and url != "6개월 초과" else url
    if '지도' in df_g.columns:
        df_g['지도'] = df_g['지도'].apply(image_formula)
    for col in photo_cols:
        if col in df_g:
            df_g[col] = df_g[col].apply(image_formula)
    df_g = df_g.fillna('')
    return [df_g.columns.values.tolist()] + df_g.astype(str).values.tolist()


def save_to_google_sheet(data, photo_cols):
    """Saves to a Google Sheet.

    data: pd.DataFrame (legacy) → 'data' 시트 단일 저장
          dict[str, (df, photo_cols)] → 카테고리별 시트 저장
    photo_cols: 레거시 호환용. dict 모드에서는 무시됨 (각 항목이 자기 photo_cols 보유)"""
    if not settings.google_sheet_enabled:
        logger.LoggerFactory.logbot.info("Google Sheet 기능이 비활성화되어 구글 시트 저장을 건너뜁니다.")
        return

    with _google_lock:
        target = _get_google_spreadsheet()
        if isinstance(data, dict):
            payloads = {}
            for name in _CATEGORY_SHEETS:
                entry = data.get(name)
                if entry is None:
                    continue
                frame, photos = entry if isinstance(entry, tuple) else (entry, [])
                if frame is not None and len(frame.columns):
                    payloads[name] = _df_to_sheet_data(frame, photos)  # 0건이면 머리글 한 줄(시트를 비운다)
        else:
            payloads = {'data': _df_to_sheet_data(data, photo_cols)}
        from core.utils.sheet_export import publish
        deadline = time.monotonic() + 600
        return publish(target, payloads,
                       lambda sheet, values: _upload_to_worksheet(sheet, values, deadline=deadline),
                       drop_legacy=isinstance(data, dict))

def save_results(df):
    """Legacy: 단일 DataFrame을 받아 'data' 시트에 저장."""
    if df.empty:
        logger.LoggerFactory.logbot.info("결과 데이터프레임이 비어 있어 저장을 건너뜁니다.")
        return

    processed_df, photo_cols = _process_dataframe(df)

    save_to_excel(processed_df)
    save_to_google_sheet(processed_df, photo_cols)
