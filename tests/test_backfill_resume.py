"""2026-10-22 1회 전송 재개·업로드 일시 중지·필수 버전 잠금(규칙 문서 backfill-resume-20261022/CLIENT-RULES.md) 시험.

합성 자료만 쓴다. 네트워크·원격 서버 없음(HTTP 는 모두 가짜 응답).
"""
import errno
import json
import os
import re
import socket
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from services import community_raw_backfill as rb
from services.community_raw_backfill import (RawBackfill, BackfillPaused, BackfillTransient, BackfillCancelled,
                                             classify_backfill_error, transient_reason)
from services.community_backfill_store import SqliteItems
from services.community_store import CommunityStore
from services import community_update_lock as lock
from services import community_upload_pause as pause

ROOT = Path(__file__).resolve().parents[1]


class Backend:
    """test_raw_backfill.Backend 와 같은 모양(5건)."""
    IDS = ['A', 'B', 'C', 'D', 'E']

    def __init__(self):
        self.identity = dict(local_dataset_id='local', contributor_fingerprint='f' * 32, dataset_key='d' * 64, consent_grant_id='grant',
                             parser_target='pc-parser-13', connection_id='connection', writer_epoch=1)
        self.prepared = {};self.receipts = {};self.enqueued = [];self.woken = 0;self.alive = True
        self.paused = None;self.locked = False

    def scope(self):return dict(self.identity)
    def current(self, scope):return self.alive and scope == self.identity
    def optimization_ready(self):return True
    def inventory(self):return dict(list_ok=True, ids=list(self.IDS), eligible_ids=list(self.IDS))
    def local_ids(self):return list(self.IDS)
    def eligible_ids(self):return list(self.IDS)
    def prepare(self, rid):
        out = self.prepared.get(rid, dict(eligible=True, filled=1, unfilled=0))
        if isinstance(out, BaseException):raise out
        if callable(out):return out()
        return dict(out)
    def enqueue(self, rid, run):self.enqueued.append(rid);return 'event-' + rid
    def receipt(self, rid, event):return self.receipts.get(rid, 'pending')
    def wake_upload(self):self.woken += 1
    def upload_pause(self):return self.paused
    def update_locked(self):return self.locked


class _StoreCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store = CommunityStore.open(self.temp.name);self.addCleanup(self.store.close)
        self.addCleanup(CommunityStore._forget, os.path.join(self.temp.name, 'community.db'))
        lock.forget_cache();pause.forget_cache()
        self.addCleanup(lock.forget_cache);self.addCleanup(pause.forget_cache)
        self.backend = Backend();self.clock = 1_000_000.0

    def load(self):return json.loads(self.store.meta('raw_backfill') or 'null')

    def save(self, value, scope):
        if not self.backend.current(scope):raise BackfillCancelled()
        self.store.set_meta('raw_backfill', json.dumps(value))

    def runner(self, sqlite=True):
        return RawBackfill(self.load, self.save, self.backend, clock=lambda: self.clock,
                           items=SqliteItems(self.store) if sqlite else None)

    def run_job(self, sqlite=True, **kw):return self.runner(sqlite).run(**kw)

    def items(self):
        rows = self.store.connect().execute('SELECT * FROM raw_backfill_items ORDER BY position').fetchall()
        return {r['report_id']: {**json.loads(r['detail_json']), 'state': r['state'], 'attempts': r['attempts'], 'eligible': r['eligible']} for r in rows}


# ── 2-2 오류 분류 ───────────────────────────────────────────────────────────────

class ClassifyTests(unittest.TestCase):
    def test_table_vectors(self):
        import requests
        import urllib3.exceptions as u3
        from curl_cffi.requests import exceptions as cce
        transient = [
            (requests.ConnectionError('x'), 'network'), (requests.Timeout('x'), 'timeout'), (requests.ReadTimeout('x'), 'timeout'),
            (u3.ProtocolError('x'), 'network'), (u3.ReadTimeoutError(None, None, 'x'), 'timeout'),
            (ConnectionResetError(), 'network'), (OSError(errno.ENETUNREACH, 'x'), 'network'), (socket.gaierror('dns'), 'network'),
            (socket.timeout('x'), 'timeout'), (TimeoutError(), 'timeout'),
            (cce.ConnectionError('x'), 'network'), (cce.Timeout('x'), 'timeout'), (cce.DNSError('x'), 'network'),
            (RuntimeError('detail_retry'), 'server_5xx'), (BackfillTransient('no_result'), 'no_result'),
            (BackfillTransient('rate_limited'), 'rate_limited'), (BackfillPaused('sync_busy'), 'sync_busy'),
            (sqlite3.OperationalError('database is locked'), 'db_locked'),
        ]
        from sqlalchemy.exc import OperationalError as SAOperational
        transient.append((SAOperational('SELECT 1', {}, sqlite3.OperationalError('database is locked')), 'db_locked'))
        for exc, reason in transient:
            with self.subTest(exc=repr(exc)):
                self.assertEqual(classify_backfill_error(exc), 'transient')
                self.assertEqual(transient_reason(exc), reason)
        fixed = [RuntimeError('parse_retry'), RuntimeError('detail_save_retry'), RuntimeError('missing_report'),
                 RuntimeError('raw_event_not_created'), KeyError('k'), ValueError('v'), TypeError('t'), IndexError('i'),
                 sqlite3.OperationalError('no such table: x'), OSError(errno.ENOENT, 'missing'), cce.InvalidJSONError('x'),
                 RuntimeError('anything else'), Exception('unknown')]
        for exc in fixed:
            with self.subTest(exc=repr(exc)):
                self.assertEqual(classify_backfill_error(exc), 'fixed')

    def test_detail_retry_note_reasons(self):
        from services.community_raw_backfill_runtime import detail_retry_reason
        self.assertEqual(detail_retry_reason('network: Connection reset'), 'network')
        self.assertEqual(detail_retry_reason('network: Read timed out'), 'timeout')
        self.assertEqual(detail_retry_reason('No result key'), 'no_result')
        self.assertEqual(detail_retry_reason('HTTP 429'), 'rate_limited')
        self.assertEqual(detail_retry_reason('HTTP 503'), 'server_5xx')


# ── 2-2 일시적·고정적·보류 자동 재시도 ───────────────────────────────────────────

class TransientAndHoldTests(_StoreCase):
    def test_transient_errors_for_40_seconds_never_hold(self):
        import requests
        self.backend.prepared['A'] = requests.ConnectionError('synthetic outage')
        for _ in range(9):  # 0~40초, 5초마다 다시
            state = self.run_job();self.clock += 5
        self.assertEqual(state['state'], 'paused');self.assertEqual(state['reason'], 'network')
        self.assertEqual(state['stats']['holds'], 0)
        self.assertEqual(self.items()['A']['attempts'], 0);self.assertEqual(state['cursor'], 0)
        self.assertEqual(self.backend.enqueued, [])

    def test_transient_backoff_sequence_and_reset_on_progress(self):
        self.backend.prepared['A'] = BackfillTransient('server_5xx')
        delays = []
        for _ in range(4):
            state = self.run_job();delays.append(state['next_attempt_at'] - self.clock);self.clock = state['next_attempt_at']
        self.assertEqual(delays, [60, 300, 900, 3600])
        self.assertEqual(self.items()['A']['attempts'], 0);self.assertEqual(self.items()['A']['transient_attempts'], 4)
        self.assertEqual(state['stats']['holds'], 0)
        self.backend.prepared = {}
        state = self.run_job();self.assertEqual(state['transient_failures'], 0);self.assertEqual(state['state'], 'uploading')
        self.assertNotIn('transient_attempts', self.items()['A'])  # 성공하면 신고의 연속 일시적 횟수도 0(D1)

    def test_fixed_three_times_hold_then_auto_retry_and_growing_interval(self):
        self.backend.prepared['A'] = ValueError('synthetic parser error')
        for _ in range(3):state = self.run_job()
        item = self.items()['A']
        self.assertEqual((item['state'], item['attempts'], item['hold_count']), ('hold', 3, 1))
        self.assertEqual(item['next_attempt_at'], int(self.clock + 900));self.assertEqual(item['first_held_at'], self.clock)
        self.assertFalse(item['stale'])
        # 시각 전에는 그대로
        self.clock += 899;self.run_job();self.assertEqual(self.items()['A']['state'], 'hold')
        # 시각 경과 → pending(attempts=2) → 한 번 더 실패 → 다시 보류, 간격 1시간
        self.clock += 2;self.run_job()
        item = self.items()['A'];self.assertEqual((item['state'], item['attempts'], item['hold_count']), ('hold', 3, 2))
        self.assertEqual(item['next_attempt_at'], int(self.clock + 3600))
        # 다음 시도에서 성공 → queued. 보류 횟수·처음 보류 시각은 stored 가 될 때까지 남는다(D2), 지난 재시도 시각만 지운다
        self.backend.prepared = {};self.clock += 3601;self.run_job()
        item = self.items()['A'];self.assertEqual(item['state'], 'queued')
        self.assertEqual(item['hold_count'], 2);self.assertNotIn('next_attempt_at', item)
        self.backend.receipts['A'] = 'stored';self.run_job()
        item = self.items()['A'];self.assertEqual(item['state'], 'stored')
        for key in ('hold_count', 'first_held_at', 'stale', 'next_attempt_at'):self.assertNotIn(key, item)

    def test_hold_intervals_and_stale_after_30_days(self):
        item = {}
        now = 0
        for expected in (900, 3600, 21600, 86400, 86400):
            rb.hold_item(item, now);self.assertEqual(item['next_attempt_at'] - now, expected)
        rb.hold_item(item, 31 * 86400);self.assertTrue(item['stale'])
        self.assertEqual(rb.count_states([dict(state='hold', stale=True), dict(state='hold'), dict(state='stored')])['stale_holds'], 1)

    def test_receipt_retry_three_times_holds_and_blocked_is_not_eligible(self):
        state = self.run_job();self.assertEqual(state['state'], 'uploading')
        self.backend.receipts = {'A': 'retry', 'B': 'blocked', 'C': 'stored', 'D': 'stored', 'E': 'stored'}
        for _ in range(3):
            self.run_job();self.backend.receipts.pop('C', None)  # 다시 채운 A 는 다시 queued 된다
            self.backend.receipts['A'] = 'retry'
        items = self.items()
        self.assertEqual(items['A']['state'], 'hold');self.assertEqual(items['A']['error'], 'upload_retry')
        self.assertEqual(items['A']['next_attempt_at'], int(self.clock + 900))
        self.assertEqual((items['B']['state'], items['B']['error']), ('not_eligible', 'upload_rejected'))

    def test_retry_button_releases_every_hold_immediately(self):
        self.backend.prepared['A'] = ValueError('x')
        for _ in range(3):self.run_job()
        self.backend.prepared = {}
        self.run_job(retry_holds=True)  # 시각 전이어도 즉시
        item = self.items()['A'];self.assertEqual(item['state'], 'queued')

    def test_retry_button_sql_resets_hold_fields(self):
        self.backend.prepared['A'] = ValueError('x')
        for _ in range(3):self.run_job()
        self.backend.prepared['A'] = ValueError('again')
        self.run_job(retry_holds=True)
        item = self.items()['A'];self.assertEqual((item['state'], item['attempts'], item['hold_count']), ('pending', 1, 0))
        self.assertNotIn('next_attempt_at', item);self.assertFalse(item['stale'])

    def test_holds_only_run_sleeps_until_earliest_hold(self):
        self.backend.prepared['A'] = ValueError('x')
        for _ in range(3):self.run_job()
        self.backend.receipts = {x: 'stored' for x in 'BCDE'}
        state = self.run_job()
        self.assertEqual(state['state'], 'holds');self.assertEqual(state['next_attempt_at'], self.items()['A']['next_attempt_at'])
        calls = []
        self.backend.prepare = lambda rid: calls.append(rid) or dict(eligible=True)
        self.clock += 60;self.run_job();self.assertEqual(calls, [])  # 시각 전에는 할 일이 없다
        self.clock = state['next_attempt_at'];self.run_job();self.assertEqual(calls, ['A'])

    def test_holds_wake_is_scheduled_once(self):
        from services import community_schedule
        with mock.patch.object(community_schedule, 'schedule_once') as once:
            rb._schedule_wake(dict(state='holds', next_attempt_at=4_000_000_000))
            rb._schedule_wake(dict(state='done', next_attempt_at=4_000_000_000))
        once.assert_called_once()
        self.assertEqual(once.call_args.args[:2], (rb.WAKE_JOB_ID, 4_000_000_000))

    def test_memory_items_follow_the_same_rules(self):
        self.backend.prepared['A'] = ValueError('x')
        for _ in range(3):state = self.run_job(sqlite=False)
        self.assertEqual(state['items']['A']['state'], 'hold')
        self.clock += 901;self.backend.prepared = {}
        state = self.run_job(sqlite=False);self.assertEqual(state['items']['A']['state'], 'queued')


# ── 2-4 업로더 깨우기 ───────────────────────────────────────────────────────────

class WakeTests(_StoreCase):
    def test_filling_slice_with_new_queued_wakes_uploader_once(self):
        runner = self.runner();runner.run(budget=2)
        self.assertEqual(runner.woke, 1);self.assertEqual(self.backend.woken, 1)

    def test_no_new_queued_no_wake(self):
        self.backend.prepared = {x: dict(eligible=True, raw_unavailable=True) for x in Backend.IDS}
        runner = self.runner();state = runner.run(budget=2)
        self.assertEqual(state['state'], 'filling');self.assertEqual(self.backend.woken, 0)

    def test_paused_or_locked_never_wakes(self):
        self.backend.locked = True
        self.assertIsNone(self.run_job());self.assertEqual(self.backend.woken, 0);self.assertIsNone(self.load())
        self.backend.locked = False;self.backend.paused = {'paused': True, 'wake_at': None}
        state = self.run_job();self.assertEqual(self.backend.woken, 0)
        self.assertEqual((state['state'], state['reason'], state['next_attempt_at']), ('paused', 'maintenance', None))


# ── 2-9 점검 중 1회 전송 ────────────────────────────────────────────────────────

class MaintenanceBackfillTests(_StoreCase):
    def test_pause_keeps_attempts_and_resume_continues(self):
        self.run_job(budget=1)
        self.backend.paused = {'paused': True, 'wake_at': self.clock + 5400}
        for _ in range(5):state = self.run_job();self.clock += 30
        self.assertEqual((state['state'], state['reason'], state['next_attempt_at']), ('paused', 'maintenance', self.clock - 150 + 5400))
        self.assertEqual(state['transient_failures'], 0)
        self.assertTrue(all(i['attempts'] == 0 for i in self.items().values()))
        self.backend.paused = None  # status 가 재개를 알렸다(저장 지움) — 재개 시각을 기다리지 않는다
        state = self.run_job();self.assertEqual(state['state'], 'uploading')


# ── 2-3 첫 실행 1회 초기화 ─────────────────────────────────────────────────────

class UnstickTests(_StoreCase):
    def _seed(self, marker, items):
        with self.store.transaction() as tx:
            tx.execute('DELETE FROM raw_backfill_items')
            for n, (rid, state, detail, eligible, attempts) in enumerate(items):
                tx.execute('INSERT INTO raw_backfill_items(run_id,report_id,position,state,attempts,eligible,detail_json) VALUES (?,?,?,?,?,?,?)',
                           ('run-1', rid, n, state, attempts, eligible, json.dumps(detail)))
            tx.execute("DELETE FROM meta WHERE key IN ('raw_backfill','raw_backfill_unstick')")
            if marker is not None:
                self.store.set_meta('raw_backfill', json.dumps(dict(run_id='run-1', local_dataset_id='local', **marker)), tx)

    ITEMS = [('H', 'hold', {'error': 'ValueError', 'hold_count': 2, 'next_attempt_at': 9, 'stale': True}, 1, 3),
             ('U', 'not_eligible', {'error': 'upload_rejected'}, 0, 3),
             ('X', 'not_eligible', {'error': 'deleted'}, 0, 0),
             ('Y', 'not_eligible', {'error': 'agency_code_too_long'}, 0, 0),
             ('S', 'stored', {}, 1, 0), ('Q', 'queued', {}, 1, 0), ('R', 'raw_unavailable', {}, 1, 0), ('P', 'pending', {}, 1, 1)]

    def test_table(self):
        cases = [
            (dict(state='done', phase='uploading', cursor=8), self.ITEMS, dict(state='filling', phase='filling', cursor=0), 2),
            (dict(state='done', phase='uploading', cursor=8), self.ITEMS[2:], dict(state='done', phase='uploading', cursor=8), 0),
            (dict(state='holds', phase='uploading', cursor=8, next_attempt_at=99), self.ITEMS, dict(state='filling', phase='filling', cursor=0, next_attempt_at=0), 2),
            (dict(state='paused', phase='filling', reason='network', next_attempt_at=5e9, transient_failures=4, failures=2), self.ITEMS,
             dict(state='paused', next_attempt_at=0, transient_failures=0, failures=0), 2),
            (dict(state='paused', phase='filling', reason='official_login_required', login_fp='fp', next_attempt_at=5e9), self.ITEMS,
             dict(state='paused', next_attempt_at=5e9, login_fp='fp'), 2),
            (dict(state='filling', phase='filling', cursor=4), self.ITEMS, dict(state='filling', cursor=4), 2),
            (dict(state='uploading', phase='uploading', cursor=8), self.ITEMS, dict(state='uploading', cursor=8), 2),
        ]
        for marker, items, expect, reverted in cases:
            with self.subTest(marker=marker, reverted=reverted):
                self._seed(marker, items)
                result = rb.unstick_once(self.store)
                self.assertEqual(result['reverted'], reverted)
                after = self.load()
                for key, value in expect.items():self.assertEqual(after[key], value, key)
                rows = self.items()
                if 'H' in rows:
                    self.assertEqual((rows['H']['state'], rows['H']['attempts'], rows['H']['hold_count'], rows['H']['stale']), ('pending', 0, 0, False))
                    self.assertNotIn('next_attempt_at', rows['H'])
                    self.assertEqual((rows['U']['state'], rows['U']['attempts'], rows['U']['eligible']), ('pending', 0, 1));self.assertNotIn('error', rows['U'])
                self.assertEqual(rows['X']['state'], 'not_eligible');self.assertEqual(rows['Y']['state'], 'not_eligible')
                self.assertEqual(rows['S']['state'], 'stored');self.assertEqual(rows['Q']['state'], 'queued')
                self.assertEqual(rows['R']['state'], 'raw_unavailable');self.assertEqual(rows['P']['attempts'], 1)
                self.assertEqual(self.store.meta(rb.UNSTICK_KEY), '2026-10-22.1')
                # 두 번째 실행에서는 다시 바꾸지 않는다
                with self.store.transaction() as tx:
                    tx.execute("UPDATE raw_backfill_items SET state='hold' WHERE report_id='S'")
                self.assertIsNone(rb.unstick_once(self.store));self.assertEqual(self.items()['S']['state'], 'hold')

    def test_uploading_run_with_pending_goes_back_to_filling(self):
        """B 와 맞춤: 업로드 확인 단계에 pending 이 남아 있으면(첫 실행 초기화로 되돌린 항목) 채우기로 돌아가 다시 보낸다."""
        self._seed(dict(state='uploading', phase='uploading', cursor=2, listing_complete=True, contract='v2',
                        **{k: v for k, v in self.backend.identity.items() if k != 'local_dataset_id'}),
                   [('U', 'not_eligible', {'error': 'upload_rejected'}, 0, 3), ('S', 'stored', {}, 1, 0)])
        rb.unstick_once(self.store)
        self.assertEqual(self.load()['state'], 'uploading')  # 표: uploading 은 그대로
        self.backend.IDS = ['U', 'S']
        state = self.run_job();self.assertEqual(state['phase'], 'filling')
        state = self.run_job();self.assertEqual(self.backend.enqueued, ['U']);self.assertEqual(state['state'], 'uploading')
        self.backend.receipts = {'U': 'stored'};self.assertEqual(self.run_job()['state'], 'done')

    def test_login_reason_without_manual_mark_is_released(self):
        """B 와 같음(이미 PC 동작): official_login_required 라도 수동 표시(login_fp)가 없으면 대기 0."""
        self._seed(dict(state='paused', phase='filling', reason='official_login_required', next_attempt_at=5e9), self.ITEMS)
        rb.unstick_once(self.store);self.assertEqual(self.load()['next_attempt_at'], 0)

    def test_pc_notice_wording_uses_program_not_app(self):
        from services import user_action_notice as notice
        items = notice.evaluate(backfill_reason='official_login_required', official_login_manual=True, stale_holds=2, update_required=True)
        self.assertEqual([i['code'] for i in items], ['update_required', 'official_login_required', 'stale_holds'])
        self.assertIn('안전신문고 로그인이 필요합니다. 설정에서 다시 로그인해 주세요.', [i['text'] for i in items])
        for text in [i['text'] for i in items]:self.assertNotIn('앱', text)
        from services.community_gate import BLOCK_MESSAGES, UPDATE_REQUIRED
        self.assertNotIn('앱', BLOCK_MESSAGES[UPDATE_REQUIRED])

    def test_no_run_only_marks(self):
        self._seed(None, [])
        self.assertEqual(rb.unstick_once(self.store), dict(reverted=0, state=None))
        self.assertEqual(self.store.meta(rb.UNSTICK_KEY), '2026-10-22.1');self.assertIsNone(self.store.meta('raw_backfill'))

    def test_unstuck_done_run_finishes_again(self):
        self._seed(dict(state='done', phase='uploading', cursor=8, listing_complete=True, contract='v2', **{k: v for k, v in self.backend.identity.items() if k != 'local_dataset_id'}),
                   [('H', 'hold', {}, 1, 3), ('S', 'stored', {}, 1, 0)])
        rb.unstick_once(self.store)
        self.backend.IDS = ['H', 'S']
        state = self.run_job();self.assertEqual(state['state'], 'uploading');self.assertEqual(self.backend.enqueued, ['H'])
        self.backend.receipts = {'H': 'stored'};self.assertEqual(self.run_job()['state'], 'done')


# ── 2-9 업로드 일시 중지 저장·문구·업로더 ───────────────────────────────────────

class UploadPauseTests(_StoreCase):
    def status(self, **ingest):return {'ingest': {'contracts': [1, 2], **ingest}}

    def test_status_is_authoritative_and_missing_key_keeps(self):
        with mock.patch.object(pause, '_schedule_resume') as sched, mock.patch.object(pause, '_on_resume') as resumed, \
                mock.patch.object(pause, '_rand', return_value=0.5), mock.patch.object(pause, '_now', return_value=1_000.0):
            self.assertEqual(pause.apply_status(self.store, self.status(paused=True, resume_at='2026-10-22T06:30:00.000Z', pause_message='저장 공간')), 'paused')
            value = pause.read(self.store)
            resume = pause._parse_iso('2026-10-22T06:30:00.000Z')
            self.assertEqual((value['resume_at'], value['wake_at'], value['message'], value['source']), (resume, resume + 60, '저장 공간', 'status'))
            sched.assert_called_once_with(resume + 60)
            self.assertEqual(pause.apply_status(self.store, self.status()), 'kept')  # 키 없음 → 그대로
            self.assertEqual(pause.apply_status(self.store, {'ingest': {'paused': 'yes'}}), 'kept')
            self.assertIsNotNone(pause.read(self.store))
            # 연장(새 재개 시각)
            self.assertEqual(pause.apply_status(self.store, self.status(paused=True, resume_at='2026-10-22T08:00:00Z')), 'paused')
            self.assertEqual(pause.read(self.store)['resume_at'], pause._parse_iso('2026-10-22T08:00:00Z'))
            # 운영자가 일찍 올림
            self.assertEqual(pause.apply_status(self.store, self.status(paused=False)), 'resumed')
            resumed.assert_called_once();self.assertIsNone(pause.read(self.store))

    def test_jitter_range_and_restart_persistence(self):
        for u in (0.0, 0.999):
            with mock.patch.object(pause, '_schedule_resume'), mock.patch.object(pause, '_rand', return_value=u):
                pause.clear(self.store) if pause.read(self.store) else None
                pause.apply_status(self.store, self.status(paused=True, resume_at='2026-10-22T06:30:00Z'))
                wake = pause.read(self.store)['wake_at'] - pause.read(self.store)['resume_at']
                self.assertTrue(0 <= wake <= 120)
        pause.forget_cache()  # 재시작: 디스크에서 다시 읽는다
        self.assertTrue(pause.paused(self.store))

    def test_503_body_and_fallback_when_status_never_knows(self):
        body = json.dumps({'error': {'code': 'maintenance', 'message': 'x', 'request_id': 'ing_1', 'retryable': True,
                                     'retry_after_seconds': 600, 'resume_at': None, 'pause_message': None}})
        with mock.patch.object(pause, '_schedule_resume') as sched, mock.patch.object(pause, '_now', return_value=1_000.0), \
                mock.patch.object(pause, '_on_resume'):
            self.assertTrue(pause.note_maintenance(body, {'Retry-After': '600'}, store=self.store))
            value = pause.read(self.store)
            self.assertEqual((value['resume_at'], value['wake_at'], value['source'], value['fallback_at']), (None, None, '503', 1_600.0))
            sched.assert_not_called()  # 재개 시각이 없으면 타이머를 걸지 않는다
            self.assertEqual(pause.apply_status(self.store, self.status()), 'kept')
        with mock.patch.object(pause, '_now', return_value=1_601.0), mock.patch.object(pause, '_on_resume'):
            self.assertEqual(pause.apply_status(self.store, self.status()), 'resumed')
        self.assertFalse(pause.note_maintenance(json.dumps({'error': {'code': 'busy'}}), store=self.store))

    def test_banner_text_kst(self):
        from datetime import datetime, timezone
        now = datetime(2026, 10, 22, 3, 0, tzinfo=timezone.utc).timestamp()  # KST 12:00
        same_day = datetime(2026, 10, 22, 6, 30, tzinfo=timezone.utc).timestamp()  # KST 15:30
        next_day = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc).timestamp()  # KST 10/23 09:00
        self.assertEqual(pause.banner_text({'resume_at': same_day}, now), '서버 점검 중입니다. 신고 업로드를 잠시 멈췄고, 오후 3시 30분에 자동으로 다시 올립니다.')
        self.assertEqual(pause.banner_text({'resume_at': next_day}, now), '서버 점검 중입니다. 신고 업로드를 잠시 멈췄고, 10월 23일 오전 9시에 자동으로 다시 올립니다.')
        self.assertEqual(pause.banner_text({'resume_at': None}, now), '서버 점검 중입니다. 신고 업로드를 잠시 멈췄고, 점검이 끝나면 자동으로 다시 올립니다.')

    def test_cloud_boundary_503_maintenance_is_not_a_global_cooldown(self):
        from services import community_cloud as cloud
        body = json.dumps({'error': {'code': 'maintenance', 'message': 'x', 'request_id': 'ing_1', 'retryable': True,
                                     'retry_after_seconds': 5400, 'resume_at': '2026-10-22T06:30:00.000Z', 'pause_message': '점검'}}).encode()
        state = {'cloud': {}}
        import contextlib

        @contextlib.contextmanager
        def fake_state():
            yield state
        with mock.patch.object(cloud, '_state', fake_state), mock.patch.object(pause, 'note_maintenance') as noted:
            cloud.run('https://fixture.invalid', lambda: (503, body, {'Retry-After': '5400'}))
            self.assertEqual(state['cloud'], {});noted.assert_called_once()
            cloud.run('https://fixture.invalid', lambda: (503, b'{"error":{"code":"busy"}}', {}))
            self.assertIn(cloud._scope('https://fixture.invalid'), state['cloud'])  # 일반 503 은 그대로 전역 쿨다운


class UploaderPauseLockTests(unittest.TestCase):
    """실제 업로더 실행: 중지·잠금 동안 업로드 요청 0회."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store = CommunityStore.open(self.temp.name);self.addCleanup(self.store.close)
        self.addCleanup(CommunityStore._forget, os.path.join(self.temp.name, 'community.db'))
        lock.forget_cache();pause.forget_cache();self.addCleanup(lock.forget_cache);self.addCleanup(pause.forget_cache)

    def test_no_request_while_paused_or_locked(self):
        from services import community_uploader as up, community_ingest_client as client
        with mock.patch.object(client, 'post_envelope', side_effect=AssertionError('no upload during pause')) as post, \
                mock.patch.object(pause, '_schedule_resume'), mock.patch('services.community_capture.deletion_cleanup_pending', return_value=False):
            pause.apply_status(self.store, {'ingest': {'paused': True, 'resume_at': '2099-01-01T00:00:00Z'}})
            for trigger in ('realtime', 'recovery', 'manual'):
                result = up._run_upload('run-' + trigger, trigger, data_dir=self.temp.name)
                self.assertEqual((result['result'], result['error_code']), ('cooldown', 'maintenance'))
                self.assertTrue(result['next_attempt_at'].startswith('2099-01-01T00:0'))
            with mock.patch.object(pause, '_on_resume'):pause.clear(self.store)
            lock._save(self.store, {'locked': True, 'min_version': '9.0.0'})
            result = up._run_upload('run-lock', 'manual', data_dir=self.temp.name)
            self.assertEqual((result['result'], result['error_code']), ('blocked_gate', 'update_required'))
            post.assert_not_called()

    def test_manifest_not_requested_while_paused(self):
        from services import community_ingest_client as client
        with mock.patch.object(client, '_config', return_value=mock.Mock(supabase_url='https://fixture.invalid', publishable_key='p')), \
                mock.patch.object(pause, 'paused', return_value=True), \
                mock.patch.object(client, '_http_post', side_effect=AssertionError('no manifest')):
            self.assertEqual(client.post_manifest(connection_id='c'), (False, {}))

    def test_503_maintenance_row_attempt_not_counted_and_retry_at_resume(self):
        from datetime import datetime, timezone
        from services import community_uploader as up, community_upload_policy as policy
        with self.store.transaction() as tx:
            tx.execute("INSERT INTO source_journal(event_id,project_namespace,local_dataset_id,source_report_id,source_revision,event_type,captured_at,"
                       "capture_trigger,schema_version,parser_version,payload_json,payload_sha256,eligible) VALUES ('e1','ns','l','r',1,'upsert','t','manual',1,'p','{}','h',1)")
            tx.execute("INSERT INTO outbox(event_id,state,attempt_count,enqueued_trigger,enqueued_at) VALUES ('e1','pending',0,'manual','t')")
        rows = [dict(event_id='e1', attempt_count=0)]
        run = up.UploadRunContext(store=self.store, run_id='r', trigger='manual', started='t', owner='o', ctx={}, scopes={'service': 's', 'account': 'a'})
        run.outbox.mark_in_flight(rows, 'o')
        wake = datetime.now(timezone.utc).timestamp() + 5400
        expect = datetime.fromtimestamp(wake - 5, timezone.utc).isoformat()[:19]
        with mock.patch.object(pause, 'read', return_value={'paused': True, 'wake_at': wake, 'fallback_at': wake}):
            outcome = up._apply_error(run, rows, policy._error('server_busy', 503, code='maintenance', hint=5400))
        row = self.store.connect().execute("SELECT state,attempt_count,next_retry_at,last_error_code FROM outbox WHERE event_id='e1'").fetchone()
        self.assertEqual(outcome, 'cooldown')
        self.assertEqual((row['state'], row['attempt_count'], row['last_error_code']), ('retry_wait', 0, 'maintenance'))
        self.assertGreaterEqual(row['next_retry_at'], expect)  # 다음 시도 = 재개 시각(+지터)
        self.assertGreaterEqual(run.next_attempt_at, expect)
        # 운영자가 일찍 재개: 저장을 지우면 그 행은 재개 시각을 기다리지 않는다
        with mock.patch.object(pause, '_on_resume'), mock.patch.object(pause, '_schedule_resume'):
            pause.apply_status(self.store, {'ingest': {'paused': True, 'resume_at': None}})
            pause.clear(self.store)
        row = self.store.connect().execute("SELECT next_retry_at FROM outbox WHERE event_id='e1'").fetchone()
        self.assertLess(row['next_retry_at'], expect)


# ── 2-10 필수 버전 잠금 ─────────────────────────────────────────────────────────

class VersionLockTests(_StoreCase):
    def setUp(self):
        super().setUp()
        # 잠금 판정은 "지금 앱 버전 < 필수 버전"을 본다. 실제 VERSION 파일(출시마다 바뀜)이 아니라 고정 버전으로 본다.
        p = mock.patch('services.community_update_lock.app_version', return_value='3.6.1'); p.start(); self.addCleanup(p.stop)

    def test_version_vectors(self):
        cases = json.loads((ROOT / 'contracts/community-ingest-v2/version-vectors.json').read_text())['cases']
        for case in cases:
            with self.subTest(case=case):
                self.assertEqual(lock.compare(case['a'], case['b']), case['cmp'])

    def req(self, pc='3.6.2', **extra):
        return {'client_requirement': {'min_version': {'pc': pc, 'mobile': '2.5.1'}, 'url': {'pc': 'https://github.com/Fentanest/safetyreport/releases/latest', 'mobile': None},
                                       'message': None, 'enforce_from': '2026-11-01T00:00:00.000Z', **extra}}

    def test_decision_table(self):
        apply = lambda status, version='3.6.1': lock.apply_status(self.store, status, version=version, now=1)
        apply(self.req());self.assertTrue(lock.locked(self.store))
        self.assertEqual(lock.read(self.store)['min_version'], '3.6.2')
        for keep in ({}, {'client_requirement': 'x'}, {'client_requirement': {'min_version': 'x'}}, self.req(pc='3.6.2-beta'),
                     self.req(pc=7)):
            apply(keep);self.assertTrue(lock.locked(self.store), keep)  # 키 없음·모양 틀림·해석 불가 → 유지
        apply(self.req(), version='garbage');self.assertTrue(lock.locked(self.store))
        apply(self.req(pc=None));self.assertFalse(lock.locked(self.store))  # 내 플랫폼 요구 없음 → 해제
        apply(self.req());self.assertTrue(lock.locked(self.store))
        apply({'client_requirement': None});self.assertFalse(lock.locked(self.store))  # 요구 해제 → 즉시 해제
        apply(self.req(), version='3.6.2');self.assertFalse(lock.locked(self.store))
        apply(self.req(), version='3.6.3');self.assertFalse(lock.locked(self.store))
        # 해석 불가는 새로 잠그지 않는다
        apply(self.req(pc='abc'));self.assertFalse(lock.locked(self.store))
        lock.forget_cache();apply(self.req());lock.forget_cache()
        self.assertTrue(lock.locked(self.store))  # 재시작해도 유지

    def test_gate_failures_never_lock(self):
        """시간 초과·5xx·429·JSON 아님: 게이트가 정상 응답을 받지 못하면 잠금 판정을 부르지 않는다."""
        from services import community_gate as gate
        from services.community_account_client import AccountApiError
        service = mock.Mock();service.config.return_value = mock.Mock(supabase_url='https://fixture.invalid', publishable_key='p')
        g = gate._Gate()
        with mock.patch.object(gate.cas, 'get_service', return_value=service), \
                mock.patch.object(gate._Gate, '_session_state', return_value=('valid', {'user_id': 'u'})), \
                mock.patch.object(gate, 'config_state', return_value='ok'), mock.patch.object(gate.cloud, 'observe'), \
                mock.patch.object(lock, 'apply_status') as applied, mock.patch.object(pause, 'apply_status') as paused:
            for exc in (AccountApiError('network_error'), AccountApiError('server_error', 503), AccountApiError('rate_limited', 429),
                        AccountApiError('server_error', 200, transient=False)):
                with mock.patch.object(gate, 'authenticated_call', side_effect=exc):
                    g._refresh_locked()
            with mock.patch.object(gate, 'authenticated_call', return_value=(['not', 'object'], 't')):
                try:g._refresh_locked()  # JSON 객체가 아님(형식 이상)
                except Exception:pass
        applied.assert_not_called();paused.assert_not_called()

    def test_crawl_block_and_backfill_start_stop_while_locked(self):
        from services import community_gate as gate
        with mock.patch.object(lock, 'locked', return_value=True):
            self.assertEqual(gate.crawl_block(), (409, gate.UPDATE_REQUIRED))
            from services import crawl_control
            with self.assertRaises(RuntimeError) as raised:crawl_control._check_crawl_allowed()
            self.assertEqual(gate.block_code(raised.exception), gate.UPDATE_REQUIRED)


class VersionHeaderTests(unittest.TestCase):
    def test_every_account_request_carries_version_headers(self):
        from services import community_account_client as cac
        sent = []

        class Http:
            def post(self, url, **kw):
                sent.append(kw['headers'])
                return mock.Mock(status_code=200, text='{"protocol":1}', headers={})
        client = cac.CommunityAccountClient('https://fixture.invalid', 'publishable', session=Http())
        with mock.patch.object(cac.cloud, 'run', side_effect=lambda base, fn: fn()), \
                mock.patch('services.community_notice.app_version', return_value='3.6.2'), \
                mock.patch('services.community_gate._platform', return_value='docker'):
            client.status('token');client.setup_progress('token', {'protocol': 1, 'stage': 'kakao_ok'})
        for headers in sent:
            self.assertEqual(headers['X-Client-Version'], '3.6.2');self.assertEqual(headers['X-Client-App'], 'pc/docker')
        with mock.patch('services.community_notice.app_version', return_value=''), mock.patch('services.community_gate._platform', return_value='windows'):
            self.assertEqual(cac.version_headers(), {'X-Client-App': 'pc/windows'})
        for platform in ('windows', 'linux', 'macos', 'docker'):
            with mock.patch('services.community_gate._platform', return_value=platform):
                self.assertRegex(cac.version_headers()['X-Client-App'], r'^pc/(windows|linux|macos|docker)$')


class LockWebTests(unittest.TestCase):
    """관리자 웹 전체 잠금 화면·Client API 409(실제 앱)."""
    @classmethod
    def setUpClass(cls):
        import main
        main.get_app()
        from fastapi.testclient import TestClient
        from core.database import database
        from core.database.engine import get_engine
        cls.main, cls.engine = main, get_engine()
        database.upgrade_schema(cls.engine, maintenance=False)
        cls.key = database.create_api_key(cls.engine, 'fixture-lock')
        cls.TestClient = TestClient

    @classmethod
    def tearDownClass(cls):
        from core.database import database
        database.delete_api_key(cls.engine, cls.key)

    def setUp(self):
        # 저장된 필수 버전 3.6.2 보다 낮은 앱으로 본다(실제 VERSION 파일과 무관하게).
        p = mock.patch('services.community_update_lock.app_version', return_value='3.6.1'); p.start(); self.addCleanup(p.stop)
        self.client = self.TestClient(self.main.app);self.addCleanup(self.client.close)
        self.store = CommunityStore.open()
        lock.forget_cache()
        lock._save(self.store, {'locked': True, 'min_version': '3.6.2', 'url': None, 'message': '<b>서버 안내</b>', 'decided_at': 1})

        def unlock():
            with self.store.transaction() as tx:tx.execute("DELETE FROM meta WHERE key='client_lock'")
            lock.forget_cache()
        self.addCleanup(unlock)

    def test_client_api_gets_existing_server_upgrade_required(self):
        headers = {'X-API-Key': self.key, 'X-SafetyReport-Client': 'mobile', 'X-SafetyReport-Version': '2.5.1+180', 'X-SafetyReport-Protocol': '3'}
        r = self.client.get('/api/v1/community/gate', headers=headers)
        self.assertEqual((r.status_code, r.json()['code']), (409, 'SERVER_UPGRADE_REQUIRED'))
        self.assertEqual(set(r.json()), {'status', 'code', 'detail', 'message', 'compatibility'})
        self.assertEqual(r.headers['cache-control'], 'no-store')
        self.assertNotEqual(self.client.get('/api/v1/server/version', headers=headers).status_code, 409)

    def test_admin_web_is_one_lock_screen(self):
        from starlette.middleware.sessions import SessionMiddleware
        from fastapi import FastAPI
        app = FastAPI()

        async def page():
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse('normal page')
        for path in ('/', '/data/all', '/settings/'):
            app.add_api_route(path, page)
        app.add_api_route('/crawl/start', page, methods=['POST'])
        app.add_api_route('/settings/save', page, methods=['POST'])

        async def admin(request, call_next):
            request.session['admin_logged_in'] = True
            return await call_next(request)
        app.middleware('http')(self.main.update_lock_middleware)
        app.middleware('http')(admin)
        app.add_middleware(SessionMiddleware, secret_key='lock-fixture')
        with self.TestClient(app) as client:
            # D3: 설정 화면(카카오 로그인·다시 로그인·로그아웃)은 잠금 중에도 열린다. 설정 저장은 계속 잠금.
            self.assertEqual(client.get('/settings/').text, 'normal page')
            self.assertEqual(client.post('/settings/save').status_code, 409)
            for path in ('/', '/data/all'):
                r = client.get(path)
                self.assertEqual(r.status_code, 200);self.assertIn('프로그램을 업데이트해 주세요', r.text);self.assertIn('PC에 있는 신고 자료는 그대로 남아 있습니다.', r.text)
                self.assertIn('필요한 버전: 3.6.2 이상', r.text);self.assertIn('&lt;b&gt;서버 안내&lt;/b&gt;', r.text)
                self.assertIn('https://github.com/Fentanest/safetyreport/releases/latest', r.text)
                self.assertNotIn('normal page', r.text)
            r = client.post('/crawl/start')
            self.assertEqual((r.status_code, r.json()['code']), (409, 'COMMUNITY_UPDATE_REQUIRED'))
            lock.forget_cache()
            with self.store.transaction() as tx:tx.execute("DELETE FROM meta WHERE key='client_lock'")
            self.assertEqual(client.get('/').text, 'normal page')


# ── 진행 보고 보내는 때(2-1) ─────────────────────────────────────────────────────

class ProgressTriggerTests(_StoreCase):
    def setUp(self):
        super().setUp()
        from services import community_progress_trigger as trig
        self.trig = trig;trig._reset_for_tests();self.addCleanup(trig._reset_for_tests)
        self.store.set_context(contributor_fingerprint='f' * 32, connection_id='c', writer_epoch=1, dataset_key='d' * 64,
                               consent_grant_id='g', policy_version='p', consent_text_sha256='h' * 64, source_app='safetyreport', source_mode='server')
        self.backend.identity['local_dataset_id'] = self.store.local_dataset_id()
        self.sent = []
        patcher = mock.patch('services.community_progress_report.maybe_send', side_effect=lambda **kw: self.sent.append(kw) or True)
        patcher.start();self.addCleanup(patcher.stop)
        opt = mock.patch.object(trig, 'optimize_view', return_value={'required': False, 'running': False, 'holds_only': False})
        opt.start();self.addCleanup(opt.stop)

    def triggers(self):return [x['trigger'] for x in self.sent]

    def test_start_state_change_periodic_daily(self):
        self.trig.tick(self.store)  # run 없음
        self.assertEqual(self.sent[-1]['backfill']['state'], 'none')
        self.run_job(budget=2);self.trig.tick(self.store);self.trig.tick(self.store)
        self.run_job(budget=1);self.trig.tick(self.store)  # 개수만 바뀜 → state_change 아님
        self.backend.receipts = {x: 'stored' for x in Backend.IDS};self.run_job();self.run_job();self.trig.tick(self.store);self.trig.tick(self.store)
        self.assertEqual(self.triggers(), ['start', 'state_change', 'periodic', 'periodic', 'state_change', 'daily'])
        last = self.sent[-1]
        self.assertEqual(last['backfill']['state'], 'done');self.assertEqual(last['backfill']['stored'], 5)
        self.assertEqual(last['official_login'], 'ok');self.assertNotIn('pace', last)

    def test_values_match_progress_schema(self):
        schema = json.loads((ROOT / 'contracts/community-ingest-v2/progress.schema.json').read_text())
        props = schema['properties']['backfill']
        code = re.compile(schema['$defs']['code']['pattern']);instant = re.compile(schema['$defs']['instant']['pattern'])
        self.backend.prepared['A'] = BackfillTransient('network')
        self.run_job();self.trig.tick(self.store)
        view = self.sent[-1]['backfill']
        self.assertEqual(set(view), set(props['required']))
        self.assertIn(view['state'], props['properties']['state']['enum'])
        self.assertEqual((view['state'], view['reason']), ('paused', 'network'))
        for key in ('phase', 'reason'):self.assertTrue(view[key] is None or code.fullmatch(view[key]))
        for key in ('next_attempt_at', 'run_started_at', 'updated_at'):self.assertTrue(view[key] is None or instant.fullmatch(view[key]), key)
        for key in ('total', 'stored', 'queued', 'pending', 'holds', 'stale_holds', 'raw_unavailable', 'not_eligible'):
            self.assertIsInstance(view[key], int)
        self.assertEqual(view['total'], 5);self.assertEqual(view['pending'], 5)
        self.assertIn(self.sent[-1]['official_login'], schema['properties']['official_login']['enum'])

    def test_not_called_while_paused_locked_or_without_connection(self):
        with mock.patch.object(pause, 'paused', return_value=True):self.assertIsNone(self.trig.tick(self.store))
        with mock.patch.object(lock, 'locked', return_value=True):self.assertIsNone(self.trig.tick(self.store))
        self.store.deactivate_context('test');self.assertIsNone(self.trig.tick(self.store))
        self.assertEqual(self.sent, [])

    def test_official_login_values(self):
        self.assertEqual(self.trig.official_login({'state': 'paused', 'reason': 'official_login_required', 'login_fp': 'x'}), 'manual_required')
        self.assertEqual(self.trig.official_login({'state': 'paused', 'reason': 'official_login_retry'}), 'retry')
        self.assertEqual(self.trig.official_login({'state': 'filling'}), 'ok')


# ── 공지 경로·관리자 키 ─────────────────────────────────────────────────────────

class AnnouncementRouteTests(unittest.TestCase):
    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from starlette.middleware.sessions import SessionMiddleware
        import main
        from web.routers import announcement_route
        self.route = announcement_route
        app = FastAPI()

        async def admin(request, call_next):
            if request.headers.get('X-Test-Admin'):
                request.session['admin_logged_in'] = True;request.session['admin_username'] = 'Admin '
            return await call_next(request)

        from fastapi import Request

        async def token(request: Request):
            from starlette.responses import JSONResponse
            from core.utils import csrf
            return JSONResponse({'csrf': csrf.get_or_create_token(request)})
        app.add_api_route('/test-csrf', token)
        app.include_router(announcement_route.router)
        app.middleware('http')(main.inject_version_middleware)
        app.middleware('http')(admin)
        app.add_middleware(SessionMiddleware, secret_key='announcement-fixture')
        self.client = TestClient(app, headers={'X-Test-Admin': '1'});self.addCleanup(self.client.close)
        self.csrf = self.client.get('/test-csrf').json()['csrf']

    def test_snooze_dismiss_and_pending_use_raw_admin_name(self):
        key = 'Admin '  # session['admin_username'] 그대로(E 와 합의)
        with mock.patch('services.app_announcements.snooze') as snooze, mock.patch('services.app_announcements.dismiss') as dismiss, \
                mock.patch('services.app_announcements.pending_for_admin', return_value=[{'id': '2026-10-pc-sunset'}, {'id': 'other'}]) as pending:
            self.assertEqual(self.client.post('/api/announcements/2026-10-pc-sunset/snooze').status_code, 403)  # CSRF 없음
            r = self.client.post('/api/announcements/2026-10-pc-sunset/snooze', headers={'x-csrf-token': self.csrf}, json={})
            self.assertEqual(r.status_code, 200);snooze.assert_called_once_with(key, '2026-10-pc-sunset')
            r = self.client.post('/api/announcements/Bad%20Id/dismiss', headers={'x-csrf-token': self.csrf}, json={})
            self.assertEqual(r.status_code, 400)
            r = self.client.post('/api/announcements/other/dismiss', headers={'x-csrf-token': self.csrf}, json={})
            self.assertEqual(r.status_code, 200);dismiss.assert_called_once_with(key, 'other')
            r = self.client.get('/api/announcements/pending')
            self.assertEqual(r.json(), {'data': [{'id': '2026-10-pc-sunset'}]})  # 이번 로그인에 닫은 것은 뺀다
            pending.assert_called_with(key)

    def test_template_helpers_never_wait_for_network(self):
        from services import app_announcements as ann
        request = mock.Mock(session={'admin_logged_in': True, 'admin_username': 'a', 'announcements_closed': ['x']})
        with mock.patch.object(self.route, '_cache_fresh', return_value=True), \
                mock.patch('services.app_announcements.pending_for_admin', return_value=[{'id': 'x'}, {'id': 'y'}]):
            self.assertEqual(self.route.sr_announcements(request), [{'id': 'y'}])
        # E 구현처럼 캐시가 오래됐으면 이번 화면은 빈 목록, 받기는 백그라운드
        with mock.patch.object(ann, '_cache', {'fetched_at': 0.0, 'items': []}, create=True), \
                mock.patch.object(ann, 'CACHE_SECONDS', 21600, create=True), \
                mock.patch.object(ann, 'refresh', create=True) as refresh, \
                mock.patch('services.app_announcements.pending_for_admin', side_effect=AssertionError('no network in render')):
            self.assertEqual(self.route.sr_announcements(request), [])
            for _ in range(50):
                if refresh.called:break
                __import__('time').sleep(0.01)
            refresh.assert_called_once()
        with mock.patch.object(self.route, '_cache_fresh', return_value=True), \
                mock.patch('services.app_announcements.pending_for_admin', side_effect=RuntimeError('offline')):
            self.assertEqual(self.route.sr_announcements(request), [])
        with mock.patch('services.user_action_notice.evaluate', side_effect=RuntimeError('x')):
            self.assertEqual(self.route.sr_notices(request), [])
        with mock.patch('services.user_action_notice.evaluate', return_value=[]) as evaluate:
            self.route.sr_notices(request)
            self.assertFalse(evaluate.call_args.kwargs['update_required'])  # 잠금은 잠금 화면이 따로 보인다
        self.assertEqual(self.route.sr_notices(mock.Mock(session={})), [])

    def test_login_calls_on_login_with_session_name(self):
        with mock.patch('services.app_announcements.on_login') as on_login:
            self.route.on_login('fixture-admin')
        on_login.assert_called_once_with('fixture-admin')


class ReportMaintenanceTests(_StoreCase):
    def test_progress_report_503_moves_to_upload_pause_once(self):
        with mock.patch.object(pause, '_schedule_resume'), mock.patch.object(pause, '_now', return_value=1_000.0), \
                mock.patch.object(pause, '_on_resume'):
            value = {'resume_at': 6_400.0, 'retry_after': 5400, 'message': '점검', 'seen_at': 900.0}
            self.assertTrue(pause.note_report_maintenance(self.store, value))
            got = pause.read(self.store)
            self.assertEqual((got['resume_at'], got['source'], got['message']), (6_400.0, '503', '점검'))
            pause.apply_status(self.store, {'ingest': {'paused': False}})  # status 가 재개를 알림
            self.assertFalse(pause.note_report_maintenance(self.store, value))  # 같은 옛 기록으로 다시 멈추지 않는다
            self.assertIsNone(pause.read(self.store))
            self.assertFalse(pause.note_report_maintenance(self.store, None))

    def test_meta_shapes_for_e(self):
        lock.apply_status(self.store, {'client_requirement': {'min_version': {'pc': '9.0.0', 'mobile': None}, 'url': {'pc': None, 'mobile': None},
                                                              'message': None, 'enforce_from': None}}, version='3.6.1', now=5)
        stored = json.loads(self.store.meta('client_lock'))
        self.assertEqual(set(stored), {'locked', 'min_version', 'url', 'message', 'decided_at'});self.assertTrue(stored['locked'])
        with mock.patch.object(pause, '_schedule_resume'):
            pause.apply_status(self.store, {'ingest': {'paused': True, 'resume_at': None, 'pause_message': None}})
        stored = json.loads(self.store.meta('upload_pause'))
        self.assertTrue({'paused', 'resume_at', 'message', 'source', 'saved_at'} <= set(stored))
        self.assertIs(stored['paused'], True);self.assertIsNone(stored['resume_at'])


if __name__ == '__main__':
    unittest.main()
