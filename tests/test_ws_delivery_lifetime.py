import asyncio
import json
import tempfile
import unittest
from unittest import mock

from services import ws_event_store
from services.ws_manager import WsManager


class Socket:
    def __init__(self, blocked=False):
        self.sent, self.closed = [], []
        self.blocked = blocked
        self.active = 0

    async def accept(self):
        pass

    async def send_text(self, payload):
        self.active += 1
        try:
            if self.active > 1:
                raise AssertionError('simultaneous socket writers')
            if self.blocked:
                await asyncio.Event().wait()
            await asyncio.sleep(0)
            self.sent.append(json.loads(payload))
        finally:
            self.active -= 1

    async def close(self, code, reason=''):
        self.closed.append(code)


class WsDeliveryLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(ws_event_store.settings, 'datapath', self.temp.name).start()
        mock.patch('services.community_gate.evaluate', return_value={'can_enter': True}).start()
        mock.patch('services.ws_manager.SEND_TIMEOUT', .03).start()
        mock.patch('services.ws_manager.CLOSE_TIMEOUT', .03).start()
        self.previous = WsManager._instance
        WsManager._instance = None
        self.manager = WsManager()
        self.addCleanup(setattr, WsManager, '_instance', self.previous)

    async def asyncTearDown(self):
        await self.manager.close_all(1001)

    async def test_slow_socket_does_not_block_fast_peer_or_health_and_terminal_replays(self):
        fast, slow = Socket(), Socket(True)
        await self.manager.connect('fast', fast)
        await self.manager.connect('slow', slow)
        task = asyncio.create_task(self.manager.broadcast('crawl_finished', {'run_id': 'a' * 32, 'outcome': 'failed', 'changed_count': 0}))
        for _ in range(100):
            await asyncio.sleep(.001)
            if fast.sent:
                break
        self.assertTrue(fast.sent, 'fast peer receives before slow peer deadline')
        self.assertFalse(task.done())
        await asyncio.wait_for(task, .3)
        self.assertEqual(slow.closed, [1013])
        self.assertNotIn('slow', self.manager._connections)
        replay = Socket()
        await self.manager.connect('replay', replay)
        await self.manager.initialize('replay', replay, after=0)
        self.assertEqual(replay.sent[0]['type'], 'connected')
        self.assertEqual(replay.sent[1], fast.sent[0])
        self.assertEqual(replay.sent[1]['data']['outcome'], 'failed')
        current = Socket()
        await self.manager.connect('current', current)
        await self.manager.initialize('current', current, after=fast.sent[0]['event_id'])
        self.assertEqual(len(current.sent), 1)

    async def test_overflow_closes_explicitly_and_single_writer_serializes_ping_events(self):
        slow = Socket(True)
        await self.manager.connect('slow', slow)
        with mock.patch('services.ws_manager.QUEUE_LIMIT', 2):
            requests = [asyncio.create_task(self.manager.send('slow', slow, {'type': 'ping'})) for _ in range(8)]
            results = await asyncio.wait_for(asyncio.gather(*requests), .3)
        self.assertFalse(any(results))
        self.assertIn(1013, slow.closed)
        self.assertNotIn('slow', self.manager._deliveries)
        fast = Socket()
        await self.manager.connect('fast', fast)
        sent = await asyncio.gather(*[self.manager.send('fast', fast, {'type': 'ping', 'i': i}) for i in range(20)])
        self.assertTrue(all(sent))
        self.assertEqual([m['i'] for m in fast.sent], list(range(20)))

    async def test_old_connection_cleanup_cannot_remove_replacement_and_close_is_bounded(self):
        old, new = Socket(), Socket()
        await self.manager.connect('same', old)
        await self.manager.connect('same', new)
        self.manager.disconnect('same', old)
        self.assertIs(self.manager._connections['same'], new)
        new.close = mock.AsyncMock(side_effect=lambda **kwargs: asyncio.Event().wait())
        # A transport that never completes its close handshake.
        async def never(**kwargs):
            await asyncio.Event().wait()
        new.close.side_effect = never
        await asyncio.wait_for(self.manager.close_all(4403, 'gate'), .3)
        self.assertFalse(self.manager._connections)

    async def test_terminal_is_durable_without_loop_and_retention_gap_is_explicit(self):
        self.manager.broadcast_from_thread('crawl_finished', {'run_id': 'a' * 32, 'outcome': 'cancelled'})
        info, events = ws_event_store.replay(0)
        self.assertEqual(events[0]['data']['outcome'], 'cancelled')
        self.manager.broadcast_from_thread('crawl_finished', {'run_id': 'a' * 32, 'outcome': 'cancelled'})
        self.assertEqual(len(ws_event_store.replay(0)[1]), 1, 'duplicate completion is not journalled twice')
        with mock.patch.object(ws_event_store, 'RETENTION', 2):
            for letter in ['b', 'c', 'd']:
                self.manager.broadcast_from_thread('crawl_finished', {'run_id': letter * 32, 'outcome': 'succeeded'})
        info, events = ws_event_store.replay(0)
        self.assertTrue(info['replay_gap'])
        self.assertEqual(len(events), 2)
        info, events = ws_event_store.replay(999)
        self.assertTrue(info['cursor_reset'])
        self.assertEqual(len(events), 2)
