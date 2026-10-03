"""임시 worksheet를 완성한 뒤 기존 worksheet ID를 유지하며 atomic batch로 게시한다."""
import json
import os
import secrets
import uuid
from core.utils.atomic_file import write_bytes
import settings.settings as settings


class SheetExportError(RuntimeError):
    def __init__(self, state, run_id):
        self.state, self.run_id = state, run_id
        super().__init__(f'구글 시트 저장 {state}. 기존 시트와 내보내기 기록을 확인하세요. ({run_id[:8]})')


def publish(spreadsheet, payloads, upload, *, drop_legacy=False):
    if not payloads:
        return {'state': 'skipped', 'sheets': []}
    run_id = uuid.uuid4().hex
    path = os.path.join(settings.datapath, 'export_runs', run_id + '.json')
    stages = []

    def record(state):
        write_bytes(path, json.dumps({'run_id': run_id, 'state': state,
            'sheets': list(payloads), 'staging': stages}, ensure_ascii=False).encode('utf-8'))

    record('preparing')
    try:
        existing = {sheet.title: sheet for sheet in spreadsheet.worksheets()}
        used_ids = {sheet.id for sheet in existing.values()}
        requests = []
        for name, values in payloads.items():
            temporary_name = f'_sr_{run_id}_{len(stages)}'
            stages.append({'name': temporary_name, 'id': None})
            record('uploading')
            staged = spreadsheet.add_worksheet(title=temporary_name, rows=len(values) + 100,
                                                cols=len(values[0]) + 5)
            stages[-1]['id'] = staged.id
            used_ids.add(staged.id)
            record('uploading')
            if not upload(staged, values):
                raise SheetExportError('partial', run_id)
            rows, columns = len(values), len(values[0])
            current = existing.get(name)
            if current is None:
                while True:
                    identifier = secrets.randbelow(2 ** 31 - 1)
                    if identifier not in used_ids:
                        used_ids.add(identifier)
                        break
                requests.append({'addSheet': {'properties': {'sheetId': identifier, 'title': name,
                    'gridProperties': {'rowCount': rows, 'columnCount': columns}}}})
            else:
                identifier = current.id
                requests.append({'updateSheetProperties': {'properties': {'sheetId': identifier,
                    'gridProperties': {'rowCount': rows, 'columnCount': columns}},
                    'fields': 'gridProperties(rowCount,columnCount)'}})
                requests.append({'updateCells': {'range': {'sheetId': identifier,
                    'startRowIndex': 0, 'endRowIndex': rows, 'startColumnIndex': 0,
                    'endColumnIndex': columns}, 'fields': 'userEnteredValue'}})
            requests.append({'copyPaste': {'source': {'sheetId': staged.id,
                'startRowIndex': 0, 'endRowIndex': rows, 'startColumnIndex': 0, 'endColumnIndex': columns},
                'destination': {'sheetId': identifier, 'startRowIndex': 0, 'endRowIndex': rows,
                    'startColumnIndex': 0, 'endColumnIndex': columns},
                'pasteType': 'PASTE_NORMAL'}})
            if rows > 1:
                requests.append({'updateDimensionProperties': {'range': {'sheetId': identifier,
                    'dimension': 'ROWS', 'startIndex': 1, 'endIndex': rows},
                    'properties': {'pixelSize': 300}, 'fields': 'pixelSize'}})
        if drop_legacy and 'data' in existing and 'data' not in payloads:
            requests.append({'deleteSheet': {'sheetId': existing['data'].id}})
        requests.extend({'deleteSheet': {'sheetId': stage['id']}} for stage in stages)
        record('publishing')
    except Exception as exc:
        record('partial')
        raise SheetExportError('partial', run_id) from exc
    try:
        spreadsheet.batch_update({'requests': requests})
    except Exception as exc:
        # 게시 요청의 응답 유실은 실패/성공 어느 쪽으로도 단정하지 않고 재전송하지 않는다.
        record('unknown')
        raise SheetExportError('unknown', run_id) from exc
    try:
        record('succeeded')
    except OSError as exc:
        # 원격 게시 후 로컬 기록 실패를 이유로 이미 적용된 요청을 재전송하지 않는다.
        raise SheetExportError('unknown', run_id) from exc
    return {'state': 'succeeded', 'sheets': list(payloads), 'run_id': run_id}
