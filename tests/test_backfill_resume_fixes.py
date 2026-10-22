"""출시 전 검수(R2·R5) 버그 수정 회귀 시험 — 검수자 결정 D1~D6, R2-S5, R5-1, R5-의심3.

R2 재현 시험(scratchpad r2/r2tests)의 "버그가 있다" 단언을 "고쳐졌다" 단언으로 옮겼다. 합성 자료만 쓰고 네트워크는 없다.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from services import community_update_lock as lock
from services import community_upload_pause as pause
from services.community_raw_backfill import BackfillPaused, BackfillTransient, TRANSIENT_HOLD_AFTER
from tests.test_backfill_resume import Backend, _StoreCase

ROOT = Path(__file__).resolve().parents[1]


# ── R2-B1 / D2: 업로드 확인 retry 보류도 횟수가 이어서 늘고, stored 가 될 때만 지운다 ─────────────────

class HoldCountKeptTests(_StoreCase):
    def _cycle(self, holds):
        history = []
        for _ in range(5000):
            self.run_job()
            item = self.items()['A']
            if item['state'] == 'hold':
                history.append((item['hold_count'], item['next_attempt_at'] - self.clock, item['first_held_at'], item['stale']))
                self.clock = item['next_attempt_at'] + 1
            else:
                self.clock += 1
            if len(history) >= holds:
                break
        return history

    def test_upload_retry_hold_backoff_grows(self):
        self.backend.IDS = ['A']
        self.backend.receipts['A'] = 'retry'
        history = self._cycle(5)
        self.assertEqual([h[0] for h in history], [1, 2, 3, 4, 5])
        self.assertEqual([h[1] for h in history], [900, 3600, 21600, 86400, 86400])
        self.assertEqual(len({h[2] for h in history}), 1, '처음 보류 시각(first_held_at)이 매번 새로 찍힘')
        self.assertEqual(self.items()['A']['error'], 'upload_retry')

    def test_upload_retry_hold_becomes_stale_after_30_days(self):
        self.backend.IDS = ['A']
        self.backend.receipts['A'] = 'retry'
        history = self._cycle(40)
        self.assertTrue(history[-1][3], '30일이 지나도 stale 표시가 생기지 않음')
        self.assertFalse(history[0][3])
        self.assertEqual(self.load()['stats']['holds'], 1)

    def test_stored_clears_hold_fields(self):
        self.backend.IDS = ['A']
        self.backend.receipts['A'] = 'retry'
        self._cycle(2)
        self.backend.receipts['A'] = 'stored'
        for _ in range(10):
            self.run_job();self.clock += 1
        item = self.items()['A']
        self.assertEqual(item['state'], 'stored')
        for key in ('hold_count', 'first_held_at', 'stale', 'next_attempt_at', 'transient_attempts'):
            self.assertNotIn(key, item)


# ── R2-B2 / D1: 한 신고의 연속 일시적 오류는 5번에서 그 신고만 보류하고 넘어간다 ──────────────────────

class PoisonTransientTests(_StoreCase):
    def _drive(self, runs):
        marker = None
        for _ in range(runs):
            marker = self.run_job()
            self.clock = max(self.clock + 1, (marker or {}).get('next_attempt_at') or 0)
        return marker

    def test_one_bad_report_no_longer_blocks_rest(self):
        self.backend.prepared['A'] = BackfillTransient('no_result')
        marker = self._drive(50)
        items = self.items()
        self.assertEqual(items['A']['state'], 'hold')
        self.assertEqual(items['A']['error'], 'transient_no_result')
        self.assertEqual(items['A']['hold_count'], 1)
        self.assertNotIn('transient_attempts', items['A'])  # 보류 때 0
        self.assertTrue(all(items[k]['state'] in ('queued', 'stored') for k in 'BCDE'), {k: v['state'] for k, v in items.items()})
        self.assertEqual(marker['transient_failures'], 0)  # 다른 신고가 성공했다
        self.assertEqual(TRANSIENT_HOLD_AFTER, 5)

    def test_hold_on_exactly_fifth_consecutive_transient(self):
        self.backend.prepared['A'] = BackfillTransient('server_5xx')
        for expected in (1, 2, 3, 4):
            marker = self.run_job();self.clock = marker['next_attempt_at']
            self.assertEqual((self.items()['A']['state'], self.items()['A']['transient_attempts']), ('pending', expected))
            self.assertEqual(marker['state'], 'paused')
        self.run_job()
        self.assertEqual(self.items()['A']['state'], 'hold')
        self.assertIn('B', self.backend.enqueued)

    def test_reasons_not_caused_by_report_are_not_counted(self):
        # db_locked(로컬 DB 잠김)은 일시적이지만 신고 탓이 아니다. sync_busy·공식 로그인은 BackfillPaused 로 바로 run 을 쉰다.
        for exc in (sqlite3.OperationalError('database is locked'), BackfillPaused('sync_busy'), BackfillPaused('official_login_retry')):
            with self.subTest(exc=repr(exc)):
                self.store.set_meta('raw_backfill', 'null')
                self.backend.prepared['A'] = exc
                self._drive(20)
                item = self.items()['A']
                self.assertEqual(item['state'], 'pending')
                self.assertNotIn('transient_attempts', item)
                self.assertEqual(self.backend.enqueued, [])


# ── R2-B3 / D3: 잠금은 읽을 때마다 지금 버전과 다시 비교하고, 잠금 중에도 카카오 로그인 경로를 연다 ─────────

class LockRecheckTests(unittest.TestCase):
    def setUp(self):
        from services.community_store import CommunityStore
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store = CommunityStore.open(self.tmp.name);self.addCleanup(self.store.close)
        self.addCleanup(CommunityStore._forget, os.path.join(self.tmp.name, 'community.db'))
        lock.forget_cache();self.addCleanup(lock.forget_cache)
        status = {"client_requirement": {"min_version": {"pc": "3.6.3", "mobile": None}, "url": {}, "message": None}}
        lock.apply_status(self.store, status, version="3.6.2")

    def test_lock_released_after_upgrade_without_status(self):
        with mock.patch.object(lock, 'app_version', return_value='3.6.2'):
            self.assertTrue(lock.locked(self.store))
        lock.forget_cache()  # 재시작
        with mock.patch.object(lock, 'app_version', return_value='3.6.3'):
            self.assertFalse(lock.locked(self.store))
            self.assertEqual(lock.view(self.store), {'locked': False})
        saved = json.loads(self.store.meta(lock.META_KEY))
        self.assertFalse(saved['locked'])  # 저장값도 지웠다
        with mock.patch.object(lock, 'app_version', return_value='3.6.2'):
            self.assertFalse(lock.locked(self.store))  # 지운 뒤에는 status 없이 새로 잠그지 않는다

    def test_unparseable_or_lower_version_keeps_lock(self):
        for version in ('', 'dev-build', '3.6.2'):
            with self.subTest(version=version), mock.patch.object(lock, 'app_version', return_value=version):
                lock.forget_cache()
                self.assertTrue(lock.locked(self.store))
        self.assertTrue(json.loads(self.store.meta(lock.META_KEY))['locked'])


class LockRecoveryRouteTests(unittest.TestCase):
    VIEW = {"locked": True, "min_version": "3.6.3", "url": "https://github.com/Fentanest/safetyreport/releases/latest",
            "message": "<img src=x onerror=alert(1)>", "current_version": "3.6.2"}

    def _client(self):
        from fastapi import FastAPI, Request
        from fastapi.responses import PlainTextResponse
        from fastapi.testclient import TestClient
        from starlette.middleware.sessions import SessionMiddleware
        from core.utils import logger
        logger.LoggerFactory.create_logger(mode="crawl")
        import main as m

        async def page(request: Request):
            return PlainTextResponse('route ' + ('lock' if getattr(request.state, 'update_lock', None) else 'free'))
        app = FastAPI()

        async def token(request: Request):
            from fastapi.responses import JSONResponse
            from core.utils import csrf
            return JSONResponse({'csrf': csrf.get_or_create_token(request)})
        app.add_api_route('/test-csrf', token, methods=['GET'])
        for path in ('/', '/settings/', '/onboarding/community', '/settings/community/policy', '/data/all'):
            app.add_api_route(path, page, methods=['GET'])
        for path in ('/settings/save', '/crawl/start', '/settings/community/consent', '/settings/community/settings',
                     *(f'/settings/community/{a}' for a in ('start', 'confirm', 'cancel', 'logout', 'reset-session'))):
            app.add_api_route(path, page, methods=['POST'])

        async def admin(request, call_next):
            request.session['admin_logged_in'] = True;request.session['admin_username'] = 'admin'
            return await call_next(request)
        app.middleware('http')(m.inject_version_middleware)
        app.middleware('http')(m.update_lock_middleware)
        app.middleware('http')(m.auth_middleware)
        app.middleware('http')(admin)
        app.add_middleware(SessionMiddleware, secret_key='lock-recovery')
        return TestClient(app)

    def test_kakao_login_paths_open_while_locked(self):
        client = self._client()
        with mock.patch('services.community_update_lock.view', lambda store=None: {"locked": False}):
            headers = {'X-CSRF-Token': client.get('/test-csrf').json()['csrf']}
        with mock.patch('services.community_update_lock.view', lambda store=None: dict(self.VIEW)):
            self.assertEqual(client.post('/settings/community/start').status_code, 403)  # CSRF 는 그대로
            for path in ('/settings/', '/onboarding/community', '/settings/community/policy'):
                with self.subTest(path=path):
                    self.assertEqual(client.get(path).text, 'route lock')
            for action in ('start', 'confirm', 'cancel', 'logout', 'reset-session'):
                with self.subTest(action=action):
                    self.assertEqual(client.post(f'/settings/community/{action}', headers=headers).text, 'route lock')
            for path in ('/settings/save', '/crawl/start', '/settings/community/consent', '/settings/community/settings'):
                with self.subTest(path=path):
                    r = client.post(path, headers=headers)
                    self.assertEqual((r.status_code, r.json()['code']), (409, 'COMMUNITY_UPDATE_REQUIRED'))
            r = client.get('/data/all')
            self.assertIn('srUpdateLock', r.text);self.assertNotIn('route', r.text)
            self.assertIn('href="/settings/#communityCard"', r.text);self.assertIn('카카오 다시 로그인', r.text)
            self.assertNotIn('<img src=x', r.text)
        with mock.patch('services.community_update_lock.view', lambda store=None: {"locked": False}):
            self.assertEqual(client.get('/settings/').text, 'route free')

    def test_banner_partial(self):
        from types import SimpleNamespace
        from core.utils.templating import _env
        tpl = _env.get_template('partials/update_lock_banner.html')
        html = tpl.render(request=SimpleNamespace(state=SimpleNamespace(update_lock=dict(self.VIEW))))
        self.assertIn('srUpdateLockBanner', html);self.assertIn('필요한 버전: 3.6.3 이상', html)
        self.assertIn('카카오 로그인·다시 로그인·로그아웃만', html);self.assertNotIn('앱', html)

        class _State:  # starlette State 처럼 없는 속성은 AttributeError
            def __getattr__(self, name):raise AttributeError(name)
        self.assertEqual(tpl.render(request=SimpleNamespace(state=_State())).strip(), '')


# ── R2-B4 / D5: 60초 규칙에 막힌 상태 변화는 다음 tick 에 나간다 ────────────────────────────────────

class ProgressStateChangeTests(unittest.TestCase):
    def test_suppressed_state_change_is_sent_next_tick(self):
        from services import community_progress_trigger as trig, community_progress_report as rep
        trig._reset_for_tests();self.addCleanup(trig._reset_for_tests)
        sent = []
        now = [1_000_000.0]
        store = mock.Mock()
        store.active_context.return_value = {'connection_id': '00000000-0000-4000-8000-000000000000'}
        meta = {}
        store.meta.side_effect = lambda k: meta.get(k)
        store.set_meta.side_effect = lambda k, v: meta.__setitem__(k, v)
        state = {'v': 'filling'}

        def bf(store_, marker=None):
            return {'state': state['v'], 'phase': state['v'] if state['v'] != 'done' else 'uploading', 'reason': None,
                    'next_attempt_at': None, **{k: 1 for k in ('total', 'stored', 'queued', 'pending', 'holds', 'stale_holds',
                                                             'raw_unavailable', 'not_eligible')},
                    'run_started_at': None, 'updated_at': None}
        with mock.patch.object(trig, 'backfill_view', bf), \
                mock.patch.object(trig, '_marker', lambda s: {}), \
                mock.patch.object(trig, 'optimize_view', lambda: {'required': False, 'running': False, 'holds_only': False}), \
                mock.patch.object(lock, 'locked', lambda s=None: False), \
                mock.patch('services.community_upload_pause.paused', lambda s=None: False), \
                mock.patch('services.community_upload_pause.note_report_maintenance', lambda *a: False), \
                mock.patch.object(rep, '_now', lambda: now[0]), \
                mock.patch.object(rep, '_credentials', lambda: ('https://x.supabase.co', 'k', 't')), \
                mock.patch.object(rep, '_http_post', lambda u, h, b: (sent.append(json.loads(b)['backfill']['state']) or (200, b'{}', {}))), \
                mock.patch('core.utils.runtime_mode.block_if_fixture', lambda *a: None):
            self.assertEqual(trig.tick(store), 'start')     # t=0 filling 보냄
            now[0] += 59.5
            state['v'] = 'uploading'
            self.assertEqual(trig.tick(store), 'state_change')  # 60초 규칙으로 못 보냄
            self.assertEqual(sent, ['filling'])
            now[0] += 60
            self.assertEqual(trig.tick(store), 'state_change')  # 키를 갱신하지 않았으므로 다시 state_change → 보냄
            self.assertEqual(sent, ['filling', 'uploading'])
            now[0] += 30
            state['v'] = 'done'
            trig.tick(store)                                # 30초: 막힘
            now[0] += 31
            trig.tick(store)                                # done 도 다음 날까지 늦지 않는다
            self.assertEqual(sent, ['filling', 'uploading', 'done'])
            now[0] += 60
            self.assertEqual(trig.tick(store), 'daily')
            self.assertEqual(sent, ['filling', 'uploading', 'done'])

    def test_start_not_lost_when_suppressed(self):
        from services import community_progress_trigger as trig
        trig._reset_for_tests();self.addCleanup(trig._reset_for_tests)
        self.assertEqual(trig.decide_trigger(('a',), 'filling'), 'start')
        self.assertEqual(trig.decide_trigger(('a',), 'filling'), 'start')  # 보내지 않았으면 그대로 start
        trig.mark_sent(('a',))
        self.assertEqual(trig.decide_trigger(('a',), 'filling'), 'periodic')
        self.assertEqual(trig.decide_trigger(('b',), 'filling'), 'state_change')


# ── R2-B5 / D6: 옛 서버(404)·400 은 보낸 것으로 기록하고 24시간 다시 보내지 않는다 ─────────────────────

class SetupReportOldServerTests(unittest.TestCase):
    def _run(self, status, rounds, *, stage='connection_registered', step=61):
        from services import community_setup_report as sr
        posts = []
        now = [1_000_000.0]
        meta = {}
        store = mock.Mock()
        store.meta.side_effect = lambda k: meta.get(k)
        store.set_meta.side_effect = lambda k, v: meta.__setitem__(k, v)

        def post(u, h, b):
            posts.append(json.loads(b)['stage'])
            if isinstance(status, Exception):raise status
            return status, b'{"error":{"code":"not_found"}}', {}
        with mock.patch.object(sr, '_store', lambda: store), mock.patch.object(sr, '_now', lambda: now[0]), \
                mock.patch.object(sr, '_credentials', lambda: ('https://x.supabase.co', 'k', 't')), \
                mock.patch.object(sr, '_http_post', post), \
                mock.patch('core.utils.runtime_mode.block_if_fixture', lambda *a: None):
            for _ in range(rounds):
                sr.report(stage=stage)
                now[0] += step
        return posts

    def test_old_server_404_sent_once_per_24h(self):
        self.assertEqual(len(self._run(404, 30)), 1)                 # 30분: 1번
        self.assertEqual(len(self._run(404, 50, step=1800)), 2)      # 25시간: 하루 지나 1번 더
        self.assertEqual(len(self._run(400, 30)), 1)

    def test_retryable_failures_resent_on_next_call(self):
        for status in (429, 500, 503, ConnectionError('x')):
            with self.subTest(status=status):
                self.assertEqual(len(self._run(status, 3)), 3)

    def test_success_never_repeated(self):
        self.assertEqual(len(self._run(200, 50, stage='official_login_ok', step=1800)), 1)


# ── R2-B6 / D4: 재개 시각이 지났는데 아직 중지면 status 를 한 번 부르고 다음 시도는 지금+60초 ────────────

class OverdueResumeTests(unittest.TestCase):
    def setUp(self):
        from services.community_store import CommunityStore
        self.tmp = tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store = CommunityStore.open(self.tmp.name);self.addCleanup(self.store.close)
        self.addCleanup(CommunityStore._forget, os.path.join(self.tmp.name, 'community.db'))
        pause.forget_cache();self.addCleanup(pause.forget_cache)
        now = time.time()
        self.store.set_meta('upload_pause', json.dumps({"paused": True, "resume_at": now - 300, "message": None, "source": "status",
                                                        "saved_at": now - 4000, "wake_at": now - 250, "fallback_at": now - 300}))

    def test_past_wake_at_does_not_spin(self):
        from services import community_uploader as up
        checks = []
        with mock.patch.object(up, '_store', lambda data_dir=None: self.store), \
                mock.patch('services.community_capture.deletion_cleanup_pending', lambda d=None: False), \
                mock.patch.object(pause, 'check_resume', lambda: checks.append(1)):
            r = up._run_upload('run1', 'recovery', self.tmp.name)
            up._refresh_next_due(self.tmp.name, r)
            self.assertEqual((r['result'], r['error_code']), ('cooldown', 'maintenance'))
            self.assertIsNotNone(up._next_due)
            self.assertGreaterEqual((up._next_due - up._now()).total_seconds(), 55)  # 지금+60초(지연 0 반복 금지)
            r = up._run_upload('run2', 'recovery', self.tmp.name)  # 60초 안 두 번째: status 를 또 부르지 않는다
            for _ in range(50):
                if checks:break
                time.sleep(0.02)
        self.assertEqual(checks, [1])

    def test_check_overdue_once_per_60_seconds(self):
        clock = [time.time()]
        calls = []
        with mock.patch.object(pause, '_now', lambda: clock[0]), mock.patch.object(pause, 'check_resume', lambda: calls.append(1)):
            self.assertTrue(pause.check_overdue(self.store))
            self.assertFalse(pause.check_overdue(self.store))
            clock[0] += 61
            self.assertTrue(pause.check_overdue(self.store))
            for _ in range(50):
                if len(calls) == 2:break
                time.sleep(0.02)
        self.assertEqual(len(calls), 2)
        self.assertEqual(pause.next_attempt({'wake_at': clock[0] + 500}, now=clock[0]), clock[0] + 500)
        self.assertEqual(pause.next_attempt({'wake_at': clock[0] - 5}, now=clock[0]), clock[0] + 60)
        self.assertIsNone(pause.next_attempt({'wake_at': None}, now=clock[0]))

    def test_future_wake_does_not_call_status(self):
        with mock.patch.object(pause, 'check_resume') as check:
            self.store.set_meta('upload_pause', json.dumps({"paused": True, "resume_at": time.time() + 600, "wake_at": time.time() + 650,
                                                            "source": "status", "message": None, "saved_at": time.time()}))
            pause.forget_cache()
            self.assertFalse(pause.check_overdue(self.store))
        check.assert_not_called()

    def test_early_resume_releases_maintenance_rows_even_without_saved_pause(self):
        far = '2099-01-01T00:00:00.000Z'
        with self.store.transaction() as tx:
            tx.execute("DELETE FROM meta WHERE key='upload_pause'")  # 저장은 이미 없다(다른 경로가 먼저 지움)
            tx.execute("INSERT INTO source_journal(event_id,project_namespace,local_dataset_id,source_report_id,source_revision,event_type,captured_at,"
                       "capture_trigger,schema_version,parser_version,payload_json,payload_sha256,eligible) VALUES ('e1','ns','l','r',1,'upsert','t','manual',1,'p','{}','h',1)")
            tx.execute("INSERT INTO outbox(event_id,state,attempt_count,next_retry_at,last_error_code,enqueued_trigger,enqueued_at)"
                       " VALUES ('e1','retry_wait',0,?,'maintenance','manual','t')", (far,))
        pause.forget_cache()
        with mock.patch.object(pause, '_on_resume') as woke:
            self.assertEqual(pause.apply_status(self.store, {'ingest': {'paused': False}}), 'resumed')
            woke.assert_called_once()
            due = self.store.connect().execute("SELECT next_retry_at FROM outbox WHERE event_id='e1'").fetchone()[0]
            self.assertLess(due, far)
            self.assertEqual(pause.apply_status(self.store, {'ingest': {'paused': False}}), 'unchanged')  # 되돌릴 행이 없으면 깨우지 않는다
            woke.assert_called_once()


# ── R2-S5: 공지 id·보고 코드 검사는 끝 줄바꿈을 받지 않는다 ─────────────────────────────────────────

class TrailingNewlineTests(unittest.TestCase):
    def test_announcement_id_with_newline_rejected(self):
        from services import app_announcements as aa
        base = {"id": "2026-10-client-to-standalone", "title": "제목", "body": "본문", "primary_action": None,
                "snooze_days": 7, "starts_at": "2026-10-23T00:00:00.000Z", "ends_at": None, "priority": 10}
        self.assertIsNotNone(aa.validate_item(dict(base)))
        self.assertIsNone(aa.validate_item(dict(base, id="2026-10-client-to-standalone\n")))

    def test_report_codes_with_newline_rejected(self):
        from services import community_setup_report as sr, community_progress_report as pr
        body = sr.build_body(stage='official_login_failed', reason='captcha_required\n', platform='linux', client_version='3.6.1')
        self.assertIsNone(body['reason'])
        self.assertIsNone(pr._code('network\n'))


# ── R5-1: contracts/app-announcements/ 의 json·md 가 git 에 잡힌다(.gitignore 예외), MANIFEST 와 같다 ─────

class AnnouncementContractCopyTests(unittest.TestCase):
    FOLDER = ROOT / 'contracts' / 'app-announcements'

    def test_manifest_matches_files(self):
        listed = {}
        for line in (self.FOLDER / 'MANIFEST.sha256').read_text(encoding='utf-8').splitlines():
            digest, name = line.split('  ', 1)
            listed[name] = digest
        actual = {str(p.relative_to(self.FOLDER)) for p in self.FOLDER.rglob('*') if p.is_file()} - {'MANIFEST.sha256'}
        self.assertEqual(set(listed), actual)
        for name, digest in listed.items():
            self.assertEqual(hashlib.sha256((self.FOLDER / name).read_bytes()).hexdigest(), digest, name)

    def test_files_not_git_ignored(self):
        git = shutil.which('git')
        if git is None or not (ROOT / '.git').exists():
            self.skipTest('git 작업 트리가 아님(배포 사본)')
        for path in sorted(self.FOLDER.rglob('*')):
            if path.is_file():
                rel = str(path.relative_to(ROOT))
                code = subprocess.run([git, '-C', str(ROOT), 'check-ignore', '-q', rel], capture_output=True).returncode
                self.assertEqual(code, 1, f'{rel} 이 .gitignore 에 걸린다')


if __name__ == '__main__':
    unittest.main()
