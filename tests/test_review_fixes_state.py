"""검수 결정(pc-review / mobile-review 공통 항목)의 오프라인 시험: 소켓·원격 I/O 없음.

retry 불변식, 큰 DB 일괄 전송의 시간·메모리·쓰기 횟수, 보충 실패·정규화, 삭제 신고 분모 제외,
community.db 권한·ingest_status 최소 저장, v1 개인 저장 격리.
"""
import json
import os
import stat
import tempfile
import threading
import time
import tracemalloc
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from sqlalchemy import create_engine

from services import community_capture as cap, community_raw_ingest as raw
from services.community_backfill_store import SqliteItems
from services.community_raw_backfill import BackfillBlocked, BackfillCancelled, BackfillPaused, RawBackfill
from services.community_store import CommunityStore
from test_raw_backfill import Backend

ROOT = Path(__file__).resolve().parents[1]


class RetryInvariantTests(unittest.TestCase):
    """'원본 전송 다시 시도'는 현재 단계를 유지한다 — 목록 단계에서 눌러도 0건 done 이 되지 않는다."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CommunityStore.open(self.temp.name)
        self.addCleanup(self.store.close)
        self.backend = Backend()
        self.clock = 1000

    def load(self):
        return json.loads(self.store.meta('raw_backfill') or 'null')

    def save(self, value, scope):
        if not self.backend.current(scope):
            raise BackfillCancelled()
        self.store.set_meta('raw_backfill', json.dumps(value))

    def run_job(self, **kwargs):
        runner = RawBackfill(self.load, self.save, self.backend, clock=lambda: self.clock, items=SqliteItems(self.store, lambda: None))
        return runner.run(**kwargs)

    def test_retry_while_listing_is_incomplete_keeps_listing_phase(self):
        self.backend.list_ok = False
        self.assertEqual(self.run_job()['reason'], 'list_incomplete')
        for _ in range(3):
            result = self.run_job(retry_holds=True)  # 휴지(next_attempt_at)를 풀고 목록을 다시 받는다
            self.assertEqual((result['state'], result['reason'], result['phase']), ('paused', 'list_incomplete', 'listing'))
            self.assertFalse(result['listing_complete'])
            self.assertEqual(self.backend.enqueued, [])
        self.assertEqual(self.backend.calls.count('list'), 4)
        self.backend.list_ok = True
        result = self.run_job(retry_holds=True)
        self.assertEqual((result['state'], result['stats']['total']), ('uploading', 3))
        self.assertTrue(result['listing_complete'])

    def test_retry_while_waiting_for_optimizer_never_skips_to_done(self):
        self.backend.ready = False
        self.assertEqual(self.run_job()['state'], 'waiting_optimize')
        result = self.run_job(retry_holds=True)
        self.assertEqual((result['state'], result['phase']), ('waiting_optimize', 'listing'))
        self.assertEqual(self.backend.calls, [])

    def test_done_is_impossible_while_listing_is_incomplete(self):
        self.backend.list_ok = False
        self.run_job()
        marker = self.load()
        # 옛 결함이 남긴 marker: 단계는 uploading 이고 목록은 끝나지 않았으며 항목은 0건
        marker.update(phase='uploading', state='paused', listing_complete=False, next_attempt_at=0)
        self.store.set_meta('raw_backfill', json.dumps(marker))
        result = self.run_job(retry_holds=True)
        self.assertNotEqual(result['state'], 'done')
        self.assertEqual(self.load()['stats'].get('sent', 0), 0)

    def test_zero_items_is_done_only_after_a_complete_listing(self):
        self.backend.inventory = lambda: dict(list_ok=True, ids=[], eligible_ids=[])
        self.backend.local_ids = lambda: []
        self.backend.eligible_ids = lambda: []
        result = self.run_job()
        self.assertEqual(result['state'], 'done')
        self.assertTrue(result['listing_complete'])

    def test_blocked_report_leaves_the_denominator_and_does_not_hold(self):
        self.backend.prepared['A'] = BackfillBlocked('deleted')
        self.backend.prepared['B'] = BackfillBlocked('agency_code_too_long')
        result = self.run_job()
        self.assertEqual(result['stats']['holds'], 0)
        self.assertEqual(result['stats']['total'], 1)  # C 만 남는다
        self.backend.receipts = {'C': 'stored'}
        done = self.run_job()
        self.assertEqual((done['state'], done['stats']['sent'], done['stats']['total']), ('done', 1, 1))


class LargeBackfillTests(unittest.TestCase):
    """합성 5,000건: marker 쓰기는 상수 번, marker 크기는 건수와 무관, 메모리·시간 제한."""
    N = 5000

    def test_5000_reports_use_bounded_meta_memory_and_time(self):
        ids = ['R%05d' % i for i in range(self.N)]

        class Big(Backend):
            def inventory(self):
                self.calls.append('list')
                return dict(list_ok=True, ids=ids, eligible_ids=ids)

            def local_ids(self):
                return ids

            def eligible_ids(self):
                return ids

            def prepare(self, rid):
                return dict(eligible=True, filled=0, unfilled=0)

            def receipt(self, rid, event):
                return 'stored'

        with tempfile.TemporaryDirectory() as temp:
            store = CommunityStore.open(temp)
            self.addCleanup(store.close)
            backend = Big()
            writes = []

            def save(value, scope):
                text = json.dumps(value)
                writes.append(len(text))
                store.set_meta('raw_backfill', text)

            runner = RawBackfill(lambda: json.loads(store.meta('raw_backfill') or 'null'), save, backend, items=SqliteItems(store, lambda: None))
            tracemalloc.start()
            started = time.monotonic()
            result = None
            try:
                for _ in range(40):
                    result = runner.run(budget=1000)
                    if result['state'] == 'done':
                        break
                elapsed = time.monotonic() - started
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
            self.assertEqual(result['state'], 'done')
            self.assertEqual((result['stats']['total'], result['stats']['sent']), (self.N, self.N))
            self.assertEqual(len(backend.enqueued), self.N)
            self.assertLess(elapsed, 60)
            self.assertLess(peak, 64 * 1024 * 1024)
            # O(N²) 방지: 건마다 marker 를 다시 쓰지 않는다. 쓰기는 run 마다 몇 번뿐이고 크기는 상수다.
            self.assertLess(len(writes), 40)
            self.assertLess(max(writes), 2048)
            marker = json.loads(store.meta('raw_backfill'))
            self.assertNotIn('items', marker)
            self.assertIsInstance(marker['holds'], int)
            rows = store.connect().execute('SELECT COUNT(*), COUNT(CASE WHEN state="stored" THEN 1 END) FROM raw_backfill_items').fetchone()
            self.assertEqual(tuple(rows), (self.N, self.N))

    def test_put_many_is_one_transaction_per_flush(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CommunityStore.open(temp)
            self.addCleanup(store.close)
            repo = SqliteItems(store, lambda: None)
            repo.reset('run', [('A%d' % i, dict(state='pending', attempts=0, eligible=True)) for i in range(300)])
            commits = []
            real = store.transaction

            def counting():
                commits.append(1)
                return real()

            with mock.patch.object(store, 'transaction', counting):
                repo.put_many('run', [('A%d' % i, dict(state='queued', attempts=0, eligible=True, event_id='e%d' % i)) for i in range(300)])
            self.assertEqual(len(commits), 1)
            self.assertEqual(repo.summary('run')['unfinished'], 300)

    def test_new_run_drops_the_previous_runs_item_rows(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CommunityStore.open(temp)
            self.addCleanup(store.close)
            repo = SqliteItems(store, lambda: None)
            repo.reset('old', [('A', dict(state='stored', attempts=0, eligible=True))])
            repo.reset('new', [('B', dict(state='pending', attempts=0, eligible=True))])
            self.assertEqual(repo.size('old'), 0)
            self.assertEqual(repo.size('new'), 1)


class FinishedRunDoesNotSpawnThreadsTests(unittest.TestCase):
    def test_start_skips_a_finished_run_but_not_after_retry_or_dataset_change(self):
        from services import community_raw_backfill as module
        with tempfile.TemporaryDirectory() as temp:
            store = CommunityStore.open(temp)
            self.addCleanup(store.close)
            store.set_meta('raw_backfill', json.dumps(dict(state='done', local_dataset_id=store.local_dataset_id())))
            store.set_meta('ingest_status', json.dumps(dict(consent=dict(state='active', grant_id='g', policy_version='p'), ingest=dict(contracts=[1, 2], raw_policy_version='p'))))  # v2 준비됨(꺼져 있으면 start 가 즉시 반환)
            with mock.patch.object(CommunityStore, 'open', return_value=store), \
                    mock.patch('core.utils.runtime_mode.is_fixture_mode', return_value=False), \
                    mock.patch.object(module.threading, 'Thread') as thread:
                module.start()
                thread.assert_not_called()
                store.set_meta('raw_backfill_retry', 'true')
                module.start()
                self.assertEqual(thread.call_count, 1)
                module._thread = None
                store.set_meta('raw_backfill', json.dumps(dict(state='done', local_dataset_id='another')))
                with store.transaction() as tx:
                    tx.execute("DELETE FROM meta WHERE key='raw_backfill_retry'")
                module.start()
                self.assertEqual(thread.call_count, 2)
                module._thread = None


class StoreHygieneTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'POSIX 권한')
    def test_community_db_is_private_to_the_user(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CommunityStore.open(temp)
            self.addCleanup(store.close)
            store.set_meta('probe', '1')
            self.assertEqual(stat.S_IMODE(os.stat(store.path).st_mode), 0o600)

    def test_cached_ingest_status_keeps_only_capability_and_consent_scope(self):
        status = dict(consent=dict(state='active', grant_id='g', policy_version='p', consent_text_sha256='h' * 64, granted_at='2026-01-01'),
                      ingest=dict(contracts=[1, 2], raw_policy_version='p', last_client_version='3.5.1'),
                      account=dict(fingerprint='f', display_name='합성이름'), official_account=dict(dataset_key='d' * 64),
                      gate=dict(kakao=True), contributor=dict(status='active'), client_notice=None)
        cached = raw.cached_status(status)
        text = json.dumps(cached, ensure_ascii=False)
        for forbidden in ('합성이름', 'd' * 64, 'fingerprint', 'display_name', 'dataset_key', 'last_client_version'):
            self.assertNotIn(forbidden, text)
        self.assertEqual(sorted(cached), ['consent', 'ingest'])

    def test_corrupt_ingest_status_reads_as_v1_without_raising(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CommunityStore.open(temp)
            self.addCleanup(store.close)
            store.set_meta('ingest_status', '{not json')
            self.assertEqual(raw.ingest_status(store), {})
            self.assertFalse(raw.selected(store, raw.ingest_status(store), {'consent_grant_id': 'g'}))


class SupplementNormalizationTests(unittest.TestCase):
    """supplement 는 스키마 범위로 정규화한다(범위 밖이면 null, 문자열은 자른다) — 서버 422 → 영구 보류를 막는다."""

    def schema(self):
        return json.loads((ROOT / 'contracts/community-ingest-v2/supplement.schema.json').read_text(encoding='utf-8'))

    def check(self, value, schema):
        for key, rule in schema['properties'].items():
            item = value[key]
            if rule.get('type') == 'object':
                self.check(item, rule)
                continue
            types = rule['type'] if isinstance(rule['type'], list) else [rule['type']]
            if item is None:
                self.assertIn('null', types, key)
                continue
            if 'integer' in types:
                self.assertIs(type(item), int, key)
                self.assertTrue(rule['minimum'] <= item <= rule['maximum'], (key, item))
            else:
                self.assertIs(type(item), str, key)
                self.assertTrue(rule['minLength'] <= len(item) <= rule['maxLength'], (key, len(item)))

    def test_out_of_range_and_oversized_values_are_normalized(self):
        row = {'별점': 0, '별점사유': '가' * 20000, '만족도조사여부': '참' * 500, '사진_첫촬영': '', '사진_끝촬영': 'x' * 200,
               '사진_촬영수': 10 ** 9, '신고명': '제' * 5000, '신고일': 'd' * 200, '상태': '', 'entry_value': 'e' * 999,
               'ssnpc_cd': 'c' * 99, 'ssnpc_type': 't' * 99}
        value = raw.supplement(row)
        self.check(value, self.schema())
        self.assertIsNone(value['rating'])
        self.assertEqual(len(value['rating_cause']), 10000)
        self.assertEqual(len(value['list']['title']), 1000)
        self.assertIsNone(value['photo_first_at'])
        self.assertIsNone(value['photo_count'])

    def test_boundaries_and_wrong_types(self):
        for rating, expected in [(1, 1), (5, 5), (6, None), (-1, None), (True, None), (4.0, None), ('4', None), (None, None)]:
            self.assertEqual(raw.supplement({'별점': rating})['rating'], expected, rating)
        for count, expected in [(0, 0), (1000000, 1000000), (1000001, None), (-1, None), (False, None)]:
            self.assertEqual(raw.supplement({'사진_촬영수': count})['photo_count'], expected, count)
        self.check(raw.supplement({}), self.schema())

    def test_snapshot_never_exceeds_the_schema_even_for_hostile_rows(self):
        snapshot = raw.snapshot('{"result":{"C_NO":"X"}}', '2026-10-21T00:00:00Z', 'crawl', {'별점': 99, '별점사유': 'a' * 10 ** 5, '신고명': 'b' * 10 ** 5})
        self.check(snapshot['supplement'], self.schema())


class RuntimeSupplementTests(unittest.TestCase):
    """실제 개인 DB 저장 경로: 보충이 실패해도 원본은 보낸다."""

    def setUp(self):
        from core.database import database
        from core.utils import logger
        log = mock.patch.object(logger.LoggerFactory, 'logbot', mock.Mock())
        log.start()
        self.addCleanup(log.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.engine = create_engine('sqlite:///' + os.path.join(self.temp.name, 'personal.db'))
        self.addCleanup(self.engine.dispose)
        database.upgrade_schema(self.engine, maintenance=False)
        self.store = CommunityStore.open(self.temp.name)
        self.addCleanup(self.store.close)
        self.ctx = dict(contributor_fingerprint='f' * 32, connection_id='22222222-2222-4222-8222-222222222222', writer_epoch=1, dataset_key='d' * 64,
                        consent_grant_id='33333333-3333-4333-8333-333333333333', policy_version='draft', consent_text_sha256='h' * 64,
                        source_app='safetyreport', source_mode='server')
        self.store.set_context(**self.ctx)
        self.store.set_meta('ingest_status', json.dumps(dict(consent=dict(state='active', grant_id=self.ctx['consent_grant_id'], policy_version='draft'),
                                                             ingest=dict(contracts=[1, 2], raw_policy_version='draft'))))
        from services.community_raw_backfill_runtime import RuntimeBackend
        with mock.patch('core.database.engine.get_engine', return_value=self.engine):
            self.backend = RuntimeBackend(self.store, threading.Event())
        self.backend.scope()
        self.backend.pause = lambda: None
        self.backend.connect = lambda: object()
        patches = [mock.patch.object(cap, '_store', return_value=self.store),
                   mock.patch.object(cap, 'read_account_context', return_value=(dict(self.ctx, state='active'), 'active'))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def insert(self, rid='SYNTHETIC01', **kw):
        from core.database import models
        from core.utils import raw_detail
        row = dict(ID=rid, category='traffic', 처리상태='수용', 신고명='합성', 신고일=datetime.now().strftime('%Y-%m-%d'), 신고번호='SPP-2610-8000001')
        row.update(kw)
        body = raw_detail.canonical_bytes(dict(result=dict(C_NO=rid, C_NOW=10, C_A_TITLE='합성', C_A_CONTENTS='비공개 합성', answers=[dict(C_MANAGER_TYPE_NM='수용')])))
        with self.engine.begin() as c:
            c.execute(models.report_table.insert().values(**row))
            c.execute(models.report_detail_raw_table.insert().values(ID=rid, raw_zlib=raw_detail.compress(body), raw_sha256=raw_detail.sha256_hex(body),
                                                                       raw_size=len(body), fetched_at='2026-10-21T00:00:00Z', source='crawl'))

    def journal_event(self, event_id):
        row = self.store.connect().execute('SELECT raw_event_json FROM source_journal WHERE event_id=?', (event_id,)).fetchone()
        return json.loads(row[0])

    def test_rating_lookup_failure_still_sends_the_raw_with_null_rating(self):
        self.insert(만족도조사여부='참여 완료', 별점=None, 별점사유='')
        with mock.patch('services.satisfaction_fetcher.fetch_score_via_api', side_effect=RuntimeError('network')):
            outcome = self.backend.prepare('SYNTHETIC01')
        self.assertTrue(outcome['eligible'])
        self.assertEqual(outcome['unfilled'], 1)
        event_id = self.backend.enqueue('SYNTHETIC01', 'run-1')
        self.assertTrue(event_id)
        event = self.journal_event(event_id)
        self.assertIn('body', event['raw'])
        self.assertIsNone(event['supplement']['rating'])
        marker = self.store.connect().execute("SELECT status FROM raw_backfill_supplements WHERE report_id='SYNTHETIC01' AND kind='rating'").fetchone()
        self.assertEqual(marker[0], 'unavailable')

    def test_unconfirmed_rating_and_photo_failure_and_login_loss_do_not_block(self):
        from services.satisfaction_fetcher import SatisfactionLookupResult
        self.insert(만족도조사여부='참여 완료', 별점=None, 별점사유='', 첨부사진='https://fixture.invalid/photo.jpg')
        self.backend.connect = mock.Mock(side_effect=BackfillPaused('official_login_required'))  # 로그인 세션을 못 얻어도 막지 않는다
        with mock.patch('services.satisfaction_fetcher.fetch_score_via_api', return_value=SatisfactionLookupResult(None, '', False)), \
                mock.patch('services.photo_capture_time.collect', return_value=None):
            outcome = self.backend.prepare('SYNTHETIC01')
        self.assertTrue(outcome['eligible'])
        self.assertEqual(outcome['unfilled'], 2)
        self.assertTrue(self.backend.enqueue('SYNTHETIC01', 'run-1'))

    def test_already_looked_up_report_is_not_looked_up_again(self):
        self.insert(만족도조사여부='참여 완료', 별점=None, 별점사유='')
        with mock.patch('services.satisfaction_fetcher.fetch_score_via_api', side_effect=RuntimeError('network')) as first:
            self.backend.prepare('SYNTHETIC01')
        self.assertEqual(first.call_count, 1)
        with mock.patch('services.satisfaction_fetcher.fetch_score_via_api', side_effect=AssertionError('second lookup')):
            self.backend.prepare('SYNTHETIC01')

    def test_user_deleted_report_is_blocked_not_held(self):
        self.insert()
        first = self.backend.enqueue('SYNTHETIC01', 'run-1')
        with self.store.transaction() as tx:
            tx.execute("UPDATE source_journal SET blocked_reason='deleted_by_user' WHERE event_id=?", (first,))
        with self.assertRaises(BackfillBlocked):
            self.backend.prepare('SYNTHETIC01')

    def test_ineligible_capture_is_blocked_not_retried_forever(self):
        self.insert()
        with mock.patch.object(cap, 'capture', return_value=SimpleNamespace(event_id=None, event_type=None, eligible=False, payload_sha256='x')):
            with self.assertRaises(BackfillBlocked):
                self.backend.enqueue('SYNTHETIC01', 'run-1')


class PersonalSaveIsolationTests(unittest.TestCase):
    """v2 원본 스냅숏 준비가 어떤 이유로 실패해도 개인 저장(공유 캡처)을 막지 않는다."""

    def test_snapshot_failure_does_not_abort_capture(self):
        from core.storage import reports_repo
        rec = SimpleNamespace(id='SYNTHETIC01', detail={}, title_fields=None, entry_value=None, progress_status=None, ssnpc_cd=None,
                              ssnpc_type=None, raw_canonical=b'{"result":{}}', raw_source='crawl')
        sentinel = object()
        with mock.patch.object(cap, 'add_retry_id'), mock.patch.object(cap, 'remove_retry_id'), \
                mock.patch.object(cap, '_store', side_effect=RuntimeError('store')), \
                mock.patch.object(cap, 'capture', return_value=sentinel) as capture:
            self.assertIs(reports_repo._community_capture_for(rec, {}, None), sentinel)
        self.assertIsNone(capture.call_args.kwargs['raw_snapshot'])


if __name__ == '__main__':
    unittest.main()


class RuntimeListFieldsTests(RuntimeSupplementTests):
    """실데이터 검증(2026-10-21)에서 찾은 결함: 3초 slice·재기동 뒤 목록 값(신고명·신고일·상태·만족도조사여부)이 비어 저장됐다."""

    def list_frame(self):
        import pandas as pd
        return pd.DataFrame([dict(ID='NEW01', 상태='불수용', 신고번호='SPP-2610-8000002', 신고명='합성 목록 제목',
                                  신고일='2026-10-01', 만족도조사여부='참여 가능', 감시목록='')])

    def envelope(self):
        return dict(result=dict(C_NO='NEW01', C_NOW=10, C_A_TITLE='합성 목록 제목', C_A_CONTENTS='비공개 합성',
                                answers=[dict(C_MANAGER_TYPE_NM='불수용')]))

    def test_new_report_keeps_list_fields_after_a_new_pass(self):
        with mock.patch('core.crawler.crawltitle_api.crawl_titles', return_value=([self.list_frame()], 1)):
            self.backend.inventory()
        self.backend.list_rows = {}  # 다음 slice/재기동: 메모리의 목록 값이 없다
        with mock.patch('services.db_optimize._fetch_envelope', return_value=(self.envelope(), 'ok', None)):
            self.backend.prepare('NEW01')
        row = self.backend.row('NEW01')
        self.assertEqual((row['신고명'], row['신고일'], row['상태'], row['만족도조사여부']),
                         ('합성 목록 제목', '2026-10-01', '불수용', '참여 가능'))

    def test_report_saved_without_list_fields_is_repaired_from_the_kept_list(self):
        with mock.patch('core.crawler.crawltitle_api.crawl_titles', return_value=([self.list_frame()], 1)):
            self.backend.inventory()
        self.insert('NEW01', 처리상태='불수용', 신고명=None, 신고일=None, 상태=None, 만족도조사여부=None, 신고번호='SPP-2610-8000002')
        self.backend.list_rows = {}
        self.backend.prepare('NEW01')
        row = self.backend.row('NEW01')
        self.assertEqual((row['신고명'], row['상태'], row['만족도조사여부']), ('합성 목록 제목', '불수용', '참여 가능'))

    def test_run_without_kept_list_refetches_the_list_once_and_repairs_saved_rows(self):
        # 옛 코드로 목록 단계를 마친 run: 보관된 목록 값이 없다 → 한 번 다시 받고, 이미 저장된 빈 행도 채운다.
        self.insert('NEW01', 처리상태='불수용', 신고명=None, 신고일=None, 상태=None, 만족도조사여부=None, 신고번호='SPP-2610-8000002')
        self.insert('OLD02', 처리상태='수용', 신고명=None, 신고일=None, 상태=None, 만족도조사여부=None, 신고번호='SPP-2610-8000003')
        frame = self.list_frame()
        import pandas as pd
        frame = pd.concat([frame, pd.DataFrame([dict(ID='OLD02', 상태='수용', 신고번호='SPP-2610-8000003', 신고명='두 번째',
                                                      신고일='2026-10-02', 만족도조사여부='참여 완료', 감시목록='')])])
        with mock.patch('core.crawler.crawltitle_api.crawl_titles', return_value=([frame], 1)) as listed:
            self.backend.prepare('NEW01')
            self.backend.prepare('NEW01')
        self.assertEqual(listed.call_count, 1)
        self.assertEqual(self.backend.row('OLD02')['신고명'], '두 번째')
        self.assertEqual(self.backend.row('NEW01')['만족도조사여부'], '참여 가능')
