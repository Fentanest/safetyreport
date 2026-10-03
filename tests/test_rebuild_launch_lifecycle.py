"""프로세스 시작/완료 감시/알림 실패 경계를 외부 프로세스 없이 검증한다."""
import unittest
from unittest import mock
from services import crawl_control as control, community_rebuild as rebuild
from test_community_rebuild import RebuildEnv


class RebuildLaunchLifecycle(unittest.TestCase):
    def setUp(self):
        self.env=RebuildEnv(self).install()
        self.bind=mock.Mock()
        for patch in (
            mock.patch.object(control,'_build_rebuild_command',return_value=['fixture-no-execution']),
            mock.patch.object(control,'_log_header',return_value=('fixture.log',mock.Mock())),
            mock.patch.object(control,'get_work_dir',return_value=self.env.tmp),
            mock.patch.object(control.crawl_manager,'is_crawling',return_value=False),
            mock.patch.object(control,'_prepare_after_crawl_hook',return_value=self.bind),
        ):
            patch.start();self.addCleanup(patch.stop)

    def _real_launch(self, run_id):
        # RebuildEnv의 launcher patch는 유지하고 decorated 원본만 직접 호출한다.
        return self.real_start(run_id)

    def test_notification_failure_keeps_running_job_and_completion_watcher(self):
        # Env가 설치되기 전 원본 함수는 class attribute로 보관한다.
        process=mock.Mock(name='fixture-process')
        with mock.patch.object(control.crawl_manager,'start_crawl',return_value=True) as launch, \
             mock.patch.object(control.crawl_manager,'get_process',return_value=process), \
             mock.patch.object(control.ws_manager,'broadcast_from_thread',side_effect=RuntimeError('fixture notification')), \
             mock.patch.object(rebuild,'_launch_crawl',side_effect=self._real_launch):
            job=rebuild.start('tester')
        self.assertEqual(job['state'],'running')
        self.bind.assert_called_once_with(process)
        launch.assert_called_once()
        self.assertIsNotNone(rebuild._store().connect().execute(
            "SELECT 1 FROM leases WHERE name='rebuild' AND owner=?",(job['run_id'],)).fetchone())

    def test_completion_thread_failure_happens_before_process_launch(self):
        with mock.patch.object(control,'_prepare_after_crawl_hook',side_effect=RuntimeError('fixture thread')), \
             mock.patch.object(control.crawl_manager,'start_crawl') as launch, \
             mock.patch.object(rebuild,'_launch_crawl',side_effect=self._real_launch):
            job=rebuild.start('tester')
        self.assertEqual(job['state'],'failed')
        launch.assert_not_called()

    def test_process_launch_failure_releases_waiting_watcher(self):
        for outcome in (False,OSError('fixture launch')):
            self.bind.reset_mock()
            with self.subTest(outcome=outcome), \
                 mock.patch.object(control.crawl_manager,'start_crawl',**(
                     {'side_effect':outcome} if isinstance(outcome,Exception) else {'return_value':outcome})):
                with self.assertRaises((RuntimeError,OSError)):
                    self.real_start('fixture')
            self.bind.assert_called_once_with(None)

    real_start=staticmethod(control.start_rebuild)


if __name__=='__main__': unittest.main()
