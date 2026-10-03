import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd
from core.utils import export, logger


class ExcelAtomicExportTests(unittest.TestCase):
    def test_failure_preserves_existing_workbook_and_success_publishes_all_sheets(self):
        logger.LoggerFactory.create_logger(mode='bot')
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch.object(export.settings, 'resultpath', directory), \
             mock.patch.object(export.settings, 'resultfile', 'fixture.xlsx'), \
             mock.patch.object(export.settings, 'telegram_enabled', False):
            path = Path(directory) / 'fixture.xlsx'
            path.write_bytes(b'previous complete workbook')
            first = pd.DataFrame({'신고내용': ['쉼표, 인용 "한글"\n둘째 줄'], '금액': [30000]})
            broken = mock.Mock(empty=False)
            broken.to_excel.side_effect = OSError('fixture partial write')
            with self.assertRaises(OSError): export.save_to_excel({'교통위반': first, '주정차위반': broken})
            self.assertEqual(path.read_bytes(), b'previous complete workbook')
            self.assertEqual(list(Path(directory).iterdir()), [path])
            export.save_to_excel({'교통위반': first, '주정차위반': pd.DataFrame({'ID': ['parking']})})
            with pd.ExcelFile(path) as workbook:
                self.assertEqual(workbook.sheet_names, ['교통위반', '주정차위반'])
                self.assertEqual(pd.read_excel(workbook, '교통위반').to_dict('records'), first.to_dict('records'))
            self.assertEqual(list(Path(directory).iterdir()), [path])
