"""EO R-09: 인증 서비스 분리(설정·오류·토큰 공급·worker 감독) 뒤에도 같은 이름과 동작을 지키는지."""

import threading
import unittest
from unittest import mock

from test_community_auth import USER_A, CommunityTestBase, wait_for

from services import community_auth_config, community_auth_errors
from services import community_auth_service as cas
from services.community_auth_workers import AuthWorkerSupervisor
from services.community_token_provider import CommunityTokenProvider


class FacadeTest(unittest.TestCase):
    def test_old_names_still_point_at_the_split_modules(self):
        for name in ("SECTION", "CommunityConfig", "build_config", "load_config_from_settings", "normalize_supabase_url",
                     "validate_publishable_key", "DEFAULT_SITE_URL", "ENV_ALIASES"):
            self.assertIs(getattr(cas, name), getattr(community_auth_config, name), name)
        self.assertIs(cas.CommunityAuthError, community_auth_errors.CommunityAuthError)
        self.assertIs(cas.MESSAGES, community_auth_errors.MESSAGES)


class WorkerSupervisorTest(unittest.TestCase):
    def test_cancelled_workers_stay_owned_and_share_one_join_budget(self):
        supervisor = AuthWorkerSupervisor()
        clock = [0.0]

        class Worker:
            timeouts = []

            def is_alive(self):
                return True

            def join(self, timeout):
                self.timeouts.append(timeout)
                clock[0] += timeout

        workers = [Worker(), Worker(), Worker()]
        stops = [threading.Event() for _ in workers]
        supervisor._workers = {str(i): (worker, stops[i]) for i, worker in enumerate(workers)}
        supervisor.stop("2")
        self.assertNotIn("2", supervisor._workers)
        self.assertEqual(supervisor._retiring, [(workers[2], stops[2])])
        with mock.patch("services.community_auth_workers.time.monotonic", lambda: clock[0]):
            self.assertFalse(supervisor.shutdown(timeout=5))
        self.assertEqual(sum(Worker.timeouts), 5)
        self.assertTrue(all(stop.is_set() for stop in stops))
        self.assertEqual((len(supervisor._workers), len(supervisor._retiring)), (2, 1))
        with mock.patch("services.community_auth_workers.threading.Thread") as factory:
            supervisor.start("new", lambda rid, stop: None)
            factory.assert_not_called()

    def test_finished_only_removes_its_own_entry(self):
        supervisor = AuthWorkerSupervisor()
        done = threading.Event()

        def target(request_id, stop):
            done.wait(2)
            supervisor.finished(request_id)

        supervisor.start("r1", target)
        self.assertEqual(supervisor.active(), 1)
        supervisor.finished("r1")  # 다른 스레드(여기)가 부르면 지우지 않는다
        self.assertIn("r1", supervisor._workers)
        done.set()
        self.assertTrue(wait_for(lambda: supervisor.active() == 0))
        self.assertNotIn("r1", supervisor._workers)


class ServiceSplitFlowTest(CommunityTestBase):
    def test_service_uses_its_token_provider_and_worker_supervisor(self):
        self.assertIsInstance(self.service._tokens, CommunityTokenProvider)
        self.assertIsInstance(self.service._workers, AuthWorkerSupervisor)
        self.connect(USER_A)
        with mock.patch.object(self.service._tokens, "get_access_token", return_value="tok") as get:
            self.assertEqual(self.service.get_access_token(rejected="old"), "tok")
        get.assert_called_once_with(rejected="old")

    def test_cancel_while_exchanging_discards_the_new_session(self):
        release, entered = threading.Event(), threading.Event()
        real_client = self.service._client

        def slow_client(cfg):
            client = real_client(cfg)
            original = client.exchange_code

            def exchange_code(**kw):
                entered.set()
                release.wait(5)
                return original(**kw)

            client.exchange_code = exchange_code
            return client

        with mock.patch.object(self.service, "_client", side_effect=slow_client):
            dto = self.service.start()
            rid = dto["pending"]["request_id"]
            self.fake.give_code(rid, USER_A)
            self.assertTrue(entered.wait(5), "교환이 시작돼야 한다")
            self.service.cancel(rid)
            release.set()
            # 취소는 worker 를 곧바로 retiring 으로 넘긴다(active 0) — 교환·로그아웃이 끝날 때까지 기다린다
            self.assertTrue(wait_for(lambda: self.fake.count("/auth/v1/logout") >= 1, 5))
        state = self.state()
        self.assertIsNone(state["pending"])
        self.assertIsNone(state.get("current"))
        self.assertEqual(self.fake.count("/auth/v1/token"), 1, "코드는 한 번만 교환한다")
        self.assertEqual(self.fake.count("/auth/v1/logout"), 1, "취소 뒤에 받은 세션은 로그아웃한다")


if __name__ == "__main__":
    unittest.main()
