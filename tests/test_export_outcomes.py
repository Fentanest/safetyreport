import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import settings.settings as settings
from core.utils import notifier, sheet_export


class FakeSheets:
    def __init__(self, lose_response=False):
        self.sheets = {7: SimpleNamespace(id=7, title='교통위반', values=[['old']], row_count=1, col_count=1)}
        self.published = 0
        self.lose_response = lose_response

    def worksheets(self):
        return list(self.sheets.values())

    def add_worksheet(self, title, rows, cols):
        identifier = max(self.sheets) + 1
        sheet = SimpleNamespace(id=identifier, title=title, values=[], row_count=rows, col_count=cols)
        self.sheets[identifier] = sheet
        return sheet

    def batch_update(self, body):
        self.published += 1
        next_sheets = copy.deepcopy(self.sheets)
        for request in body['requests']:
            if 'addSheet' in request:
                props = request['addSheet']['properties']
                if props['sheetId'] in next_sheets:
                    raise ValueError('duplicate sheet id')
                next_sheets[props['sheetId']] = SimpleNamespace(id=props['sheetId'], title=props['title'], values=[])
            elif 'updateCells' in request:
                next_sheets[request['updateCells']['range']['sheetId']].values = []
            elif 'copyPaste' in request:
                operation = request['copyPaste']
                next_sheets[operation['destination']['sheetId']].values = copy.deepcopy(next_sheets[operation['source']['sheetId']].values)
            elif 'deleteSheet' in request:
                del next_sheets[request['deleteSheet']['sheetId']]
        self.sheets = next_sheets
        if self.lose_response:
            raise TimeoutError('response lost after commit')


class ExportOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.object(settings._instance, 'datapath', self.temp.name)
        patch.start()
        self.addCleanup(patch.stop)

    def journal(self, name):
        return json.loads(next((Path(self.temp.name) / name).glob('*.json')).read_text())

    def test_staging_failure_preserves_all_live_sheets(self):
        remote = FakeSheets()
        def upload(stage, values):
            self.assertEqual(remote.sheets[7].values, [['old']])
            stage.values = values
            return stage.title.endswith('_0')
        with self.assertRaises(sheet_export.SheetExportError) as result:
            sheet_export.publish(remote, {'교통위반': [['new']], '기타위반': [['new2']]}, upload)
        self.assertEqual(result.exception.state, 'partial')
        self.assertEqual(remote.sheets[7].values, [['old']])
        self.assertEqual(remote.published, 0)
        self.assertEqual(self.journal('export_runs')['state'], 'partial')

    def test_atomic_publish_preserves_existing_id_and_avoids_staging_ids(self):
        remote = FakeSheets()
        def upload(stage, values):
            self.assertEqual(remote.sheets[7].values, [['old']])
            stage.values = values
            return True
        with mock.patch.object(sheet_export.secrets, 'randbelow', side_effect=[9, 12]):
            result = sheet_export.publish(remote, {'교통위반': [['new']], '기타위반': [['new2']]}, upload)
        self.assertEqual(result['state'], 'succeeded')
        self.assertEqual(remote.sheets[7].values, [['new']])
        self.assertEqual(remote.sheets[12].values, [['new2']])
        self.assertEqual(len(remote.sheets), 2)
        self.assertEqual(remote.published, 1)

    def test_lost_publish_response_is_unknown_without_retry(self):
        remote = FakeSheets(lose_response=True)
        def upload(stage, values):
            stage.values = values
            return True
        with self.assertRaises(sheet_export.SheetExportError) as result:
            sheet_export.publish(remote, {'교통위반': [['new']]}, upload)
        self.assertEqual(result.exception.state, 'unknown')
        self.assertEqual(remote.published, 1)
        self.assertEqual(remote.sheets[7].values, [['new']])
        self.assertEqual(self.journal('export_runs')['state'], 'unknown')

    def test_notification_partial_failure_never_replays_confirmed_chunks(self):
        bot = SimpleNamespace(send_message=mock.AsyncMock(side_effect=[None, TimeoutError('lost')]))
        with mock.patch.object(notifier.asyncio, 'sleep', new=mock.AsyncMock()):
            with self.assertRaises(TimeoutError):
                asyncio.run(notifier.deliver(bot, '가' * 9000))
        self.assertEqual(bot.send_message.await_count, 2)
        state = self.journal('notification_runs')
        self.assertEqual((state['state'], state['confirmed'], state['total']), ('unknown', 1, 3))
        self.assertNotIn('가', json.dumps(state))

    def test_telegram_chunk_limit_handles_astral_unicode_without_loss(self):
        text = '한글😀' * 4096
        chunks = notifier.plan_chunks(text)
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(len(chunk.encode('utf-16-le')) // 2 <= 4096 for chunk in chunks))


if __name__ == '__main__':
    unittest.main()
