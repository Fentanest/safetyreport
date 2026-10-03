import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from web import log_stream


class LogStreamLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_tail_is_bounded_and_rotation_truncation_and_gate_loss_are_observed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.log'
            path.write_bytes(('가나다\n' * 30000).encode())
            ws = mock.Mock(send_text=mock.AsyncMock(), close=mock.AsyncMock())
            watch = mock.Mock(lost=mock.AsyncMock(side_effect=[False, False, True]))
            ticks = []
            async def tick(_):
                ticks.append(1)
                if len(ticks) == 1:
                    replacement = path.with_suffix('.new')
                    replacement.write_text('회전된 로그\n', encoding='utf-8')
                    os.replace(replacement, path)
                elif len(ticks) == 2:
                    path.write_text('짧음\n', encoding='utf-8')
            with mock.patch.object(log_stream.asyncio, 'sleep', side_effect=tick):
                await log_stream.stream_log(ws, str(path), watch, 'waiting')
            chunks = [call.args[0] for call in ws.send_text.await_args_list]
            self.assertEqual(len(chunks), 3)
            self.assertLessEqual(len(chunks[0].encode()), 64 * 1024)
            self.assertNotIn('�', chunks[0])
            self.assertEqual(chunks[1:], ['회전된 로그\n', '짧음\n'])
            ws.close.assert_awaited_once_with(code=log_stream.ws_auth.CLOSE_GATE)
