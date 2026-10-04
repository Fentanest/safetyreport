import asyncio
import time
import unittest
from unittest import mock

from main import version_latest, health_check


class AsyncRouteLifetimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_exception_still_stops_managed_workers(self):
        import main
        from services.crawl_manager import crawl_manager
        self.addCleanup(crawl_manager.resume_managed)
        from services import community_rebuild, media_proxy_service, rating_service
        with mock.patch.object(community_rebuild, 'stop_background', return_value=True) as rebuild_stop, \
             mock.patch.object(media_proxy_service, 'stop', return_value=True) as media_stop, \
             mock.patch.object(rating_service, 'stop', return_value=True) as rating_stop, \
             mock.patch.object(main, '_checkpoint_wal') as checkpoint:
            with self.assertRaisesRegex(RuntimeError, 'fixture lifespan failure'):
                async with main.lifespan(main.app):
                    raise RuntimeError('fixture lifespan failure')
        rebuild_stop.assert_called_once()
        media_stop.assert_called_once()
        rating_stop.assert_called_once()
        checkpoint.assert_called_once()

    async def test_ws_and_crawler_stop_failures_do_not_skip_other_worker_cleanup(self):
        import main
        from services.crawl_manager import crawl_manager
        from services.ws_manager import ws_manager
        from services import media_proxy_service, rating_service
        self.addCleanup(crawl_manager.resume_managed)
        with mock.patch.object(ws_manager, 'close_all', side_effect=RuntimeError('fixture close')), \
             mock.patch.object(crawl_manager, 'shutdown', side_effect=RuntimeError('fixture process')), \
             mock.patch.object(media_proxy_service, 'stop', return_value=True) as media_stop, \
             mock.patch.object(rating_service, 'stop', return_value=True) as rating_stop, \
             mock.patch.object(main, '_checkpoint_wal') as checkpoint:
            async with main.lifespan(main.app):
                pass
        media_stop.assert_called_once()
        rating_stop.assert_called_once()
        checkpoint.assert_called_once()

    async def test_slow_version_helper_does_not_block_health_and_loop_ticks(self):
        def slow():
            time.sleep(0.15)
            return None
        with mock.patch('core.utils.updater.get_latest_version_cached', side_effect=slow):
            version = asyncio.create_task(version_latest())
            ticks = 0
            while not version.done():
                response = await health_check()
                self.assertEqual(response.status_code, 200)
                ticks += 1
                await asyncio.sleep(0.01)
            result = await version
        self.assertGreaterEqual(ticks, 5)
        self.assertEqual(result.body, b'{"status":"unknown"}')
