"""최종 PC 검수(final-pc-review.md) 결정 1~10 회귀 시험. 실제 라우트·가짜 중앙을 쓴다."""
import json
import time
import uuid
import unittest
from unittest import mock

import test_community_gate as G
from services import community_account_ops as ops, community_cloud as cloud, community_gate as gate, community_reset as reset
from test_community_auth import USER_A


def _quiet(cls):
    for name in dir(cls):
        if name.startswith('test') and name not in cls.__dict__:
            setattr(cls, name, None)
    return cls


@_quiet
class _AppBase(G.GateAppTests):
    def route_login(self, token, user=USER_A):
        rid = self.start_and_get_code(user)
        answer = self.post('/settings/community/confirm', {'request_id': rid}, token=token)
        self.assertEqual(answer.status_code, 200, answer.text)
        return answer.json()

    def consent(self, token):
        return self.post('/settings/community/consent', {'accepted': True, 'policy_version': G.POLICY,
                                                          'consent_text_sha256': G.HASH}, token=token)


class LogoutRelogin(_AppBase):
    def test_1_logout_then_relogin_restores_capture_context(self):
        from services import community_capture
        token = self.login()
        self.route_login(token)
        self.consent(token)
        self.assertEqual(gate.refresh_now()['state'], 'ok')
        ops.logout()
        self.assertEqual(cloud.denial(user=USER_A['id']).get('reason'), 'logout')
        answer = self.route_login(token)
        self.assertEqual(answer['gate']['state'], 'ok', answer['gate'])
        context, consent = cloud.capture_context(None)
        self.assertIsNotNone(context, consent)
        self.assertNotEqual(consent, 'unverified_owner')
        self.assertFalse(cloud.denial(user=USER_A['id']).get('reason'), '재로그인 확정 뒤 logout deny 가 남아 있으면 안 된다')

    def test_1_sticky_and_reason_denies_still_block_local_allowed(self):
        from services import community_auth_service as cas
        token = self.login()
        self.route_login(token)
        self.consent(token)
        service = cas.get_service()
        current = service.store.load()['current']
        from services import account_data
        owner = account_data.db_owner()
        dkey = gate.dataset_key(gate.official_username())
        self.assertTrue(cloud.local_allowed(service, current, owner, dkey))
        cloud.deny('logout', service=service)  # non-sticky: 막지 않는다
        self.assertTrue(cloud.local_allowed(service, current, owner, dkey))
        for reason in ('consent_revoked', 'account_removed', 'suspended', 'db_owner_mismatch', 'official_account_mismatch'):
            cloud.deny(reason, service=service, sticky=reason in ('consent_revoked', 'account_removed'))
            self.assertFalse(cloud.local_allowed(service, current, owner, dkey), reason)


class RevokePendingLostSession(_AppBase):
    def _pending_without_session(self, token):
        from services import community_auth_service as cas
        self.route_login(token)
        self.consent(token)
        grant = gate.current_grant_id()
        self.account.fail = [(503, 'server_error')] * 6
        reset.begin(grant)
        cas.get_service().disconnect()  # 철회 대기 중 카카오 세션을 잃음
        self.account.fail = []
        time.sleep(1.1)  # connected_at 은 초 단위 — 새 세션은 다른 시각을 가진다
        return grant

    def test_2_lost_session_shows_login_ui_and_relogin_finishes_revoke_keeping_session(self):
        from services import community_auth_service as cas
        token = self.login()
        self._pending_without_session(token)
        view = gate.status_view()
        self.assertEqual(view['state'], 'revoke_pending')
        self.assertFalse(view['session'])
        page = self.client.get('/onboarding/community')
        self.assertIn('id="cmStartBtn"', page.text, '세션이 없으면 로그인 UI 가 보여야 한다')
        with mock.patch.object(ops, 'soft_reset', wraps=ops.soft_reset) as spy:
            answer = self.route_login(token)
            self.assertEqual(answer['gate']['state'], 'consent_required', answer['gate'])
            self.assertIsNotNone(cas.get_service().store.load().get('current'), '새 세션이 끊기면 안 된다')
            self.assertEqual(spy.call_count, 1)
        self.assertIsNone(reset.pending())

    def test_2_refresh_while_pending_does_not_soft_reset_before_attempt(self):
        from services import community_auth_service as cas
        token = self.login()
        self.route_login(token)
        self.consent(token)
        self.account.fail = [(503, 'server_error')] * 6
        reset.begin(gate.current_grant_id())
        self.account.fail = []
        self.account.consents[USER_A['id']]['state'] = 'revoked'
        with mock.patch.object(ops, 'soft_reset', wraps=ops.soft_reset) as spy:
            gate.refresh_now()
            spy.assert_not_called()
        self.assertIsNotNone(cas.get_service().store.load().get('current'))
        self.assertIsNotNone(reset.pending())

    def test_2_revoke_not_found_rechecks_status_like_stale_grant(self):
        token = self.login()
        self.route_login(token)
        self.consent(token)
        reset.begin('gone-grant')
        from services.community_auth_service import CommunityAuthError
        with mock.patch.object(ops, 'account_call', side_effect=CommunityAuthError('not_found')):
            self.assertFalse(reset.attempt(force=True))
        self.assertIsNone(reset.pending()['grant_id'])


class BackfillYieldsToCrawlAndRestore(unittest.TestCase):
    """3: 원본 1회 전송이 rating 토큰을 쥐고 있어도 예약 크롤·복원이 실패하거나 건너뛰어지지 않는다."""

    def setUp(self):
        import threading
        from services.crawl_manager import CrawlManager
        self.manager = object.__new__(CrawlManager)
        for key, value in dict(_state_lock=threading.Lock(), _active_process=None, _preparing=False,
                               _post_upload_active=False, _restore_hold=False, _restore_generation=1,
                               _rating_token=None).items():
            setattr(self.manager, key, value)
        self.manager.BACKFILL_YIELD_WAIT_SECONDS = 5.0
        self.stop = threading.Event()
        self.addCleanup(self.stop.set)

    def start_backfill_holder(self, owner='raw_backfill'):
        """RuntimeBackend 와 같은 규칙: 건마다 yield_requested 를 보고 토큰을 놓는다."""
        import threading
        token = self.manager.reserve_rating(1, owner=owner)
        released = threading.Event()

        def loop():
            while not self.stop.is_set():
                if self.manager.backfill_should_yield():
                    self.manager.release_rating(token)
                    released.set()
                    return
                time.sleep(0.01)
        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        return token, released

    def test_scheduled_crawl_runs_while_backfill_holds_the_token(self):
        self.start_backfill_holder()
        with mock.patch('services.crawl_manager.subprocess.Popen') as spawn, \
                mock.patch('services.crawl_manager.block_if_fixture'), \
                mock.patch('services.crawl_run_state.create', return_value='run-1'), \
                mock.patch('services.crawl_run_state.write'):
            import tempfile, os
            log = os.path.join(tempfile.mkdtemp(), 'crawl.log')
            self.assertTrue(self.manager.start_crawl(['x'], '.', log))
            spawn.assert_called_once()

    def test_restore_succeeds_while_backfill_holds_the_token(self):
        self.start_backfill_holder()
        with mock.patch.object(self.manager, '_resume_pending_after_restore'):
            with self.manager.hold_for_restore():
                self.assertTrue(self.manager._restore_hold)

    def test_other_rating_holder_is_not_waited_for(self):
        from services.crawl_manager import RestoreBlocked
        self.manager.reserve_rating(1)  # 별점 작업 — 원본 전송이 아니므로 양보를 요청하지 않는다
        started = time.monotonic()
        with self.assertRaises(RestoreBlocked):
            with self.manager.hold_for_restore():
                pass
        self.assertLess(time.monotonic() - started, 1.0)

    def test_backfill_backend_stops_after_current_item_when_a_waiter_exists_and_will_not_regrab(self):
        import tempfile
        from services.community_store import CommunityStore
        from services.community_raw_backfill_runtime import RuntimeBackend
        import threading
        with tempfile.TemporaryDirectory() as tmp, mock.patch('core.database.engine.get_engine', return_value=mock.Mock()):
            store = CommunityStore.open(tmp)
            try:
                backend = RuntimeBackend(store, threading.Event())
            finally:
                pass
            backend.manager = self.manager
            backend.slice_until = time.monotonic() + 60
            self.assertFalse(backend.yield_requested())
            self.manager.reserve_rating(1, owner='raw_backfill')
            self.manager._yield_waiters = 1
            self.assertTrue(backend.yield_requested())
            store.close()

    def test_waiter_gives_up_after_the_bounded_wait(self):
        self.manager.BACKFILL_YIELD_WAIT_SECONDS = 0.2
        self.manager.reserve_rating(1, owner='raw_backfill')  # 놓지 않는 점유
        started = time.monotonic()
        self.assertFalse(self.manager.start_crawl(['x'], '.', '/fake/log'))
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(self.manager._yield_waiters, 0)


class ConsentCatchupResendsUnderV2(unittest.TestCase):
    """4: 비활성→active 재동의 작업은 v2 에서 완료 marker 를 지우고 새 run 으로 전체 재전송한다."""

    def setUp(self):
        import tempfile
        from services.community_store import CommunityStore
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CommunityStore.open(self.tmp.name)
        self.addCleanup(self.store.close)
        self.store.set_meta('raw_backfill', json.dumps({'state': 'done', 'local_dataset_id': self.store.local_dataset_id()}))

    def tick(self, job):
        from services import community_consent_jobs as jobs
        updates = []
        with mock.patch.object(jobs.cloud, 'current_job', return_value=('k', 'g', job)), \
                mock.patch.object(jobs.cloud, 'update_job', side_effect=lambda *a, **k: updates.append(k)), \
                mock.patch('services.community_store.CommunityStore.open', return_value=self.store), \
                mock.patch('services.community_raw_ingest.v2_ready', return_value=True), \
                mock.patch('services.community_raw_backfill.start') as start:
            jobs._tick()
        return start, updates

    def test_lineage_changed_job_clears_marker_before_start(self):
        start, updates = self.tick({'state': 'pending', 'phase': 'reshare', 'lineage_changed': True})
        start.assert_called_once()
        self.assertIsNone(self.store.meta('raw_backfill'))
        self.assertEqual(updates[0]['state'], 'succeeded')

    def test_same_lineage_grant_renewal_keeps_the_marker(self):
        start, _ = self.tick({'state': 'pending', 'phase': 'reshare', 'lineage_changed': False})
        start.assert_called_once()
        self.assertIsNotNone(self.store.meta('raw_backfill'))

    def test_observe_flags_nonactive_to_active_job_as_lineage_changed(self):
        from types import SimpleNamespace
        import tempfile
        from services import community_cloud as cl
        state = {'accounts': {}}
        account = cl._account_key('https://x.test', 'u1')
        state['accounts'][account] = {'consent': {'state': 'revoked', 'grant_id': 'g0'}}
        from contextlib import contextmanager

        @contextmanager
        def fake_state():
            yield state
        service = SimpleNamespace(store=SimpleNamespace(load=lambda: {'current': {'user_id': 'u1'}}),
                                  config=lambda: SimpleNamespace(supabase_url='https://x.test'))
        with mock.patch.object(cl, '_state', fake_state):
            cl.observe(service, {'consent': {'state': 'active', 'grant_id': 'g1', 'policy_version': 'p'}})
            self.assertTrue(state['accounts'][account]['jobs']['g1']['lineage_changed'])
            state['accounts'][account] = {'consent': {'state': 'active', 'grant_id': 'g1'}}
            cl.observe(service, {'consent': {'state': 'active', 'grant_id': 'g2', 'policy_version': 'p'}})
            self.assertFalse(state['accounts'][account]['jobs']['g2']['lineage_changed'])


import test_raw_delivery_lifecycle as D


class RawCopyIsCompacted(D.DeliveryTests):
    """5: 형식·경로와 무관하게 원본 전문(raw_event_json)이 남지 않는다."""

    def raw_json(self, event):
        return self.store.connect().execute('SELECT raw_event_json FROM source_journal WHERE event_id=?', (event.event_id,)).fetchone()[0]

    def test_5_v1_ack_of_a_row_that_holds_a_raw_copy_is_compacted(self):
        event = self.capture(trigger='realtime')
        self.assertNotIn('"compact":true', self.raw_json(event))
        status = dict(consent=dict(state='active', grant_id=self.ctx['consent_grant_id'], policy_version='draft'),
                      ingest=dict(contracts=[1], raw_policy_version='draft'))
        self.store.set_meta('ingest_status', json.dumps(status))

        def v1(u, h, b, t):
            env = json.loads(b)
            return 200, json.dumps(dict(protocol=1, request_id='synthetic-v1', results=[dict(event_id=e['event_id'], status='accepted', durable=True, receipt_id=str(uuid.uuid4()), projection_status='published') for e in env['events']])).encode(), {}
        with mock.patch.object(D.client, '_http_post', side_effect=v1):
            D.up.request_upload('recovery', self.tmp.name)
        self.assertTrue(json.loads(self.raw_json(event))['compact'])

    def test_5_rotate_dataset_compacts_old_copies_and_drops_old_supplements(self):
        event = self.capture(trigger='realtime')
        from services.community_backfill_store import SCHEMA
        old = self.store.local_dataset_id()
        with self.store.transaction() as tx:
            tx.executescript if False else None
            for statement in SCHEMA.split(';'):
                if statement.strip():
                    tx.execute(statement)
            tx.execute("INSERT INTO raw_backfill_supplements VALUES (?,?,?,?)", (old, 'R1', 'list', '{"신고명":"x"}'))
            tx.execute("INSERT INTO raw_backfill_supplements VALUES (?,?,?,?)", ('other', 'R2', 'list', '{}'))
        self.store.rotate_dataset('test')
        self.assertTrue(json.loads(self.raw_json(event))['compact'])
        left = [r[0] for r in self.store.connect().execute('SELECT local_dataset_id FROM raw_backfill_supplements')]
        self.assertEqual(left, ['other'])

    def test_5_soft_reset_compacts_blocked_rows_and_drops_supplements(self):
        event = self.capture(trigger='realtime')
        from services import community_account_ops as o, community_reset as rs, community_gate as g
        from services.community_backfill_store import SCHEMA
        with self.store.transaction() as tx:
            for statement in SCHEMA.split(';'):
                if statement.strip():
                    tx.execute(statement)
            tx.execute("INSERT INTO raw_backfill_supplements VALUES (?,?,?,?)", (self.store.local_dataset_id(), 'R1', 'list', '{}'))
        service = SimpleNamespace(store=SimpleNamespace(load=lambda: {'current': {'user_id': 'u'}}, save_writer=lambda w: None),
                                  disconnect=lambda: None)
        with mock.patch.object(o.cas, 'get_service', return_value=service), \
                mock.patch.object(o.cloud, 'deny'), mock.patch.object(o.cloud, 'reset_evidence'), \
                mock.patch.object(g, 'invalidate'), mock.patch.object(g, 'clear_after_soft_reset'), \
                mock.patch.object(rs, 'stop_work'), mock.patch('services.community_store.CommunityStore.open', return_value=self.store):
            o.soft_reset('consent_revoked')
        self.assertTrue(json.loads(self.raw_json(event))['compact'])
        self.assertEqual(self.store.connect().execute('SELECT COUNT(*) FROM raw_backfill_supplements').fetchone()[0], 0)


from types import SimpleNamespace
_quiet(RawCopyIsCompacted)
for _n in ('test_5_v1_ack_of_a_row_that_holds_a_raw_copy_is_compacted', 'test_5_rotate_dataset_compacts_old_copies_and_drops_old_supplements', 'test_5_soft_reset_compacts_blocked_rows_and_drops_supplements'):
    pass

import test_raw_backfill as B


class SmallFixes(B.BackfillRuntimeTests):
    def test_6_enqueue_fills_missing_title_fields_without_name_error(self):
        from services import community_capture as cap
        self.insert(신고명=None)
        self.backend.list_rows = {'SYNTHETIC01': {'신고명': '목록 제목'}}
        with mock.patch.object(cap, 'capture', return_value=SimpleNamespace(event_id=None, eligible=False)):
            from services.community_raw_backfill import BackfillBlocked
            with self.assertRaises(BackfillBlocked):  # NameError 가 아니라 정상 경로의 '보낼 수 없음'
                self.backend.enqueue('SYNTHETIC01', 'run-1')
        self.assertEqual(self.backend.row('SYNTHETIC01')['신고명'], '목록 제목')

    def test_8_scope_change_never_blocks_personal_save(self):
        from services import community_capture as cap
        ctx = dict(self.ctx, state='active')
        changed = dict(ctx, connection_id='33333333-3333-4333-8333-000000000000')
        # 읽은 문맥(changed)이 저장소의 활성 문맥과 다르다
        result = cap.capture(dict(processing_status='수용', report_date='2026-10-20', geocode={}),
                             source_report_id='SYNTHETIC01', trigger='realtime', data_dir=self.temp.name,
                             account_context=(changed, 'active'))
        self.assertIsNotNone(result)
        self.assertEqual(self.store.connect().execute('SELECT COUNT(*) FROM outbox').fetchone()[0], 0)

    def test_9_chmod_failure_does_not_break_store_open(self):
        import tempfile
        from services.community_store import CommunityStore
        with tempfile.TemporaryDirectory() as tmp, mock.patch('services.community_store.os.chmod', side_effect=PermissionError('nope')):
            store = CommunityStore.open(tmp)
            try:
                self.assertIsNotNone(store.connect().execute('SELECT 1').fetchone())
            finally:
                store.close()
                CommunityStore._forget(tmp + '/community.db')

    def test_7_v1_envelope_never_carries_the_raw_backfill_trigger(self):
        from services import community_uploader as up
        ctx = dict(self.ctx)
        v1 = up._envelope(ctx, 'raw_backfill', [{'event_id': 'e1', 'payload': {}}])
        self.assertEqual((v1['protocol'], v1['trigger']), (1, 'recovery'))
        v2 = up._envelope(ctx, 'raw_backfill', [{'event_id': 'e2', 'raw': {}}])
        self.assertEqual((v2['protocol'], v2['trigger']), (2, 'raw_backfill'))

    def test_10_start_is_a_noop_when_v2_is_not_ready(self):
        from services import community_raw_backfill as rb
        self.store.set_meta('ingest_status', json.dumps(dict(consent=dict(state='active'), ingest=dict(contracts=[1]))))
        with mock.patch('services.community_store.CommunityStore.open', return_value=self.store), \
                mock.patch('core.utils.runtime_mode.is_fixture_mode', return_value=False), \
                mock.patch.object(rb.threading, 'Thread') as thread:
            rb.start()
        thread.assert_not_called()

    def test_10_seed_g1_reads_before_locking(self):
        from services import community_cloud as cl
        service = SimpleNamespace(config=lambda: SimpleNamespace(supabase_url='https://x.test'))
        with mock.patch.object(cl, '_read_state', return_value={'accounts': {cl._account_key('https://x.test', 'u'): {'last_g1': {'at': 1}}}}), \
                mock.patch.object(cl, '_state', side_effect=AssertionError('lock taken')):
            self.assertFalse(cl.seed_g1(service, {'user_id': 'u', 'kakao_id': 'k'}, 'k', 'd'))


_quiet(SmallFixes)

class LoginRejectionBackoff(B.BackfillStateTests):
    """(a) 공식 로그인 거부는 고칠 때까지 멈추고, 일시 실패는 지수 백오프(최대 1시간)."""

    def rejecting(self, permanent):
        def raise_it():
            exc = B.BackfillPaused('official_login_required' if permanent else 'official_login_retry')
            exc.permanent = permanent
            raise exc
        self.backend.prepared['A'] = raise_it

    def test_confirmed_rejection_stops_until_credentials_change(self):
        self.backend.login_fp = 'old'
        self.backend.login_fingerprint = lambda: self.backend.login_fp
        self.rejecting(True)
        paused = self.run_job()
        self.assertEqual((paused['state'], paused['reason']), ('paused', 'official_login_required'))
        calls = len(self.backend.calls)
        for step in (301, 3601, 86400 * 30):
            self.clock += step
            self.run_job()
            self.assertEqual(len(self.backend.calls), calls, '거부가 확정되면 시간이 지나도 반복하지 않는다')
        self.backend.prepared.pop('A')
        self.backend.login_fp = 'new'  # 설정 저장·수동 로그인
        resumed = self.run_job()
        self.assertNotEqual(resumed['state'], 'paused')
        self.assertGreater(len(self.backend.calls), calls)

    def test_transient_login_failure_backs_off_exponentially_to_one_hour(self):
        self.rejecting(False)
        delays = []
        for _ in range(7):
            marker = self.run_job()
            delays.append(marker['next_attempt_at'] - self.clock)
            self.clock = marker['next_attempt_at'] + 1
        self.assertEqual(delays[:4], [300, 600, 1200, 2400])
        self.assertEqual(delays[4:], [3600, 3600, 3600])

    def test_runtime_backend_classifies_login_errors(self):
        from services.community_raw_backfill_runtime import RuntimeBackend
        backend = object.__new__(RuntimeBackend)
        backend.session = None
        backend.check = lambda: None
        with mock.patch('core.crawler.direct_login.make_authorized_session', side_effect=RuntimeError('로그인 거부: 비밀번호 오류')):
            with self.assertRaises(B.BackfillPaused) as cm:
                backend.connect()
        self.assertEqual((str(cm.exception), cm.exception.permanent), ('official_login_required', True))
        with mock.patch('core.crawler.direct_login.make_authorized_session', side_effect=RuntimeError('토큰 요청 실패 (3회 재시도)')):
            with self.assertRaises(B.BackfillPaused) as cm:
                backend.connect()
        self.assertEqual((str(cm.exception), cm.exception.permanent), ('official_login_retry', False))


_quiet(LoginRejectionBackoff)


class OptimizeHoldsOnlyIsReady(B.BackfillRuntimeTests):
    """(b) DB 최적화에 보류만 남은 상태는 원본 전송 준비 완료."""

    def meta(self, **over):
        from services import db_optimize
        base = {'target': db_optimize.PARSER_VERSION, 'cursor': '', 'done': False, 'holds': ['A'], 'round': 1,
                'next_retry_at': '2099-01-01 00:00'}
        base.update(over)
        return base

    def ready(self, meta):
        from services import db_optimize
        self.backend.manager = mock.Mock(is_crawling=lambda: False, backfill_should_yield=lambda: False,
                                         reserve_rating=lambda *a, **k: object(), restore_generation=lambda: self.backend.generation)
        with mock.patch.object(db_optimize, 'read_meta', return_value=meta), \
                mock.patch.object(db_optimize, 'is_running', return_value=False), \
                mock.patch.object(db_optimize, 'maybe_start') as start, \
                mock.patch('services.maintenance_service.is_running', return_value=False):
            return self.backend.optimization_ready(), start

    def test_holds_only_partial_state_is_ready(self):
        ok, start = self.ready(self.meta())
        self.assertTrue(ok)
        start.assert_not_called()

    def test_in_progress_or_stale_target_still_waits(self):
        for meta in (self.meta(next_retry_at=None), self.meta(target='old'), self.meta(holds=[]), None):
            ok, start = self.ready(meta)
            self.assertFalse(ok, meta)
            start.assert_called_once()


_quiet(OptimizeHoldsOnlyIsReady)


class RevokePendingOtherAccount(_AppBase):
    def test_c_other_account_login_shows_notice_and_logout_not_silent(self):
        from test_community_auth import USER_B
        from services import community_auth_service as cas
        token = self.login()
        self.route_login(token)
        self.consent(token)
        self.account.fail = [(503, 'server_error')] * 8
        reset.begin(gate.current_grant_id())
        cas.get_service().disconnect()
        self.account.fail = []
        self.account.grant(USER_B)
        answer = self.route_login(token, USER_B)
        self.assertEqual(answer['gate']['state'], 'revoke_pending')
        self.assertTrue(answer['gate']['revoke_account_mismatch'])
        page = self.client.get('/onboarding/community').text
        self.assertIn('철회 중인 계정', page)
        self.assertIn('로그인해야 철회를 마칠 수 있습니다', page)
        self.assertIn('id="cmLogoutBtn"', page)
        self.assertIsNotNone(reset.pending(), '다른 계정이 철회를 대신 끝내면 안 된다')


if __name__ == '__main__':
    unittest.main()
