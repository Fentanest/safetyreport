"""Binding fake-server integration, destructive ordering and recovery boundaries."""
import os
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from services import official_account as official, community_gate as gate, settings_service
from services.community_account_client import AccountApiError, CommunityAccountClient
from test_community_gate import GateTestBase, USER_A, USER_B, OFFICIAL_ID


class _BindingBase(GateTestBase):
    @classmethod
    def setUpClass(cls):
        # GateTestBase temporarily patches sys.modules for its fake uploader. Import
        # app dependencies before that patch so C extensions are never re-imported.
        import main
        main.get_app()

    def setUp(self):
        from core.utils import logger
        logger.LoggerFactory.create_logger(mode="crawl")
        super().setUp()


class BindingGateTests(_BindingBase):
    def test_manual_edit_blocks_requests_and_stops_crawl_without_registering(self):
        self.open_gate()
        before = self.account.count('connections')
        self.official = 'another-account'
        with mock.patch('services.crawl_manager.crawl_manager.stop_crawl') as stop:
            result = gate.evaluate()
        self.assertEqual(result['state'], 'official_account_mismatch')
        self.assertFalse(result['can_enter'])
        stop.assert_called_once()
        self.assertEqual(self.store.context()['state'], 'inactive')
        self.assertEqual(self.account.count('connections'), before)
        self.official = OFFICIAL_ID
        self.assertTrue(gate.refresh_now()['can_enter'])

    def test_startup_with_remote_mismatch_never_registers(self):
        self.connect(USER_A)
        self.account.grant(USER_A)
        self.account.bindings[USER_A['id']] = gate.dataset_key('another-account')
        self.assertEqual(gate.refresh_now()['state'], 'official_account_mismatch')
        self.assertEqual(self.account.count('connections'), 0)

    def test_old_server_skips_only_remote_comparison_and_reuses_writer(self):
        self.connect(USER_A)
        self.account.grant(USER_A)
        original = self.account._status
        def old(*args):
            code, value = original(*args)
            value.pop('official_account')
            return code, value
        with mock.patch.object(self.account, '_status', side_effect=old):
            self.assertTrue(gate.refresh_now()['can_enter'])
            self.assertNotIn('official_account', gate._gate._status)
            self.assertEqual(self.store.context()['state'], 'active')
            self.assertTrue(gate.refresh_now()['can_enter'])
            self.assertNotIn('official_account', gate._gate._status)
        self.assertEqual(self.account.count('connections'), 1)

    def test_connections_race_error_blocks_entire_gate(self):
        self.connect(USER_A)
        self.account.grant(USER_A)
        for code in ('official_account_taken', 'official_account_mismatch'):
            with self.subTest(code=code), mock.patch.object(self.account, '_connections', return_value=(409, {'error': {'code': code}})):
                result = gate.refresh_now()
                self.assertFalse(result['can_enter'])
                self.assertEqual(result['state'], code)

    def test_status_timeout_blocks_and_next_retry_recovers(self):
        self.open_gate()
        with mock.patch.object(CommunityAccountClient, 'status', side_effect=AccountApiError('network_error')):
            self.assertEqual(gate.refresh_now()['state'], 'cloud_unavailable')
        self.assertEqual(self.store.context()['state'], 'inactive')
        self.assertTrue(gate.refresh_now()['can_enter'])
        from services.community_auth_service import CommunityAuthError
        for method in ('get_access_token', 'current_kakao_id'):
            with self.subTest(method=method), mock.patch.object(self.service, method, side_effect=CommunityAuthError('auth_unavailable')):
                self.assertEqual(gate.refresh_now()['state'], 'cloud_unavailable')
            self.assertTrue(gate.refresh_now()['can_enter'])

    def test_pending_journal_blocks_even_after_restart(self):
        self.open_gate()
        official._write_journal({'target': gate.dataset_key('new'), 'phase': 'released'})
        gate._gate._reset_for_tests()
        self.assertEqual(gate.evaluate()['state'], 'official_account_change_pending')
        self.assertEqual(gate.refresh_now()['state'], 'official_account_change_pending')

    def test_explicit_unbound_registers_config_account_without_db_identity(self):
        self.open_gate()
        self.account.bindings.pop(USER_A['id'])
        self.official = 'new'
        result = gate.refresh_now()
        self.assertTrue(result['can_enter'])
        self.assertEqual(self.account.count('connections'), 2)
        self.assertEqual(self.account.bindings[USER_A['id']], gate.dataset_key('new'))
        self.assertEqual(self.store.context()['state'], 'active')

    def test_gate_never_stores_official_id_or_hash_in_personal_db(self):
        from core.database import database
        from core.database.engine import get_engine
        with mock.patch.object(database, 'stamp_meta_if_missing', wraps=database.stamp_meta_if_missing) as stamp, \
             mock.patch.object(database, 'set_meta', wraps=database.set_meta) as write:
            self.assertTrue(self.open_gate()['can_enter'])
            self.assertTrue(gate.refresh_now()['can_enter'])
        self.assertTrue(stamp.called)  # Kakao owner is still stamped.
        for call in stamp.call_args_list + write.call_args_list:
            self.assertNotIn(call.args[2], (OFFICIAL_ID, gate.dataset_key(OFFICIAL_ID)))
        self.assertIsNone(database.get_meta(get_engine(), 'official_account_dataset_key'))

    def test_legacy_db_identity_does_not_override_config_and_remote_binding(self):
        from core.database import database
        from core.database.engine import get_engine
        database.set_meta(get_engine(), 'official_account_dataset_key', gate.dataset_key('old'))
        self.assertTrue(self.open_gate()['can_enter'])
        self.assertEqual(database.get_meta(get_engine(), 'official_account_dataset_key'), gate.dataset_key('old'))

    def test_malformed_extended_status_blocks_without_registering(self):
        self.connect(USER_A)
        self.account.grant(USER_A)
        original = self.account._status
        for value in (None, {}, {'dataset_key': 'bad', 'bound_at': None}):
            def malformed(*args):
                code, status = original(*args)
                status['official_account'] = value
                return code, status
            with self.subTest(value=value), mock.patch.object(self.account, '_status', side_effect=malformed):
                self.assertEqual(gate.refresh_now()['state'], 'official_account_protocol_required')
        self.assertEqual(self.account.count('connections'), 0)

    def test_remote_change_is_checked_after_five_minutes(self):
        self.assertTrue(self.open_gate()['can_enter'])
        self.account.bindings[USER_A['id']] = gate.dataset_key('changed-remotely')
        self.clock.now += 301
        self.assertEqual(gate.check_for_request()['state'], 'official_account_mismatch')
        self.assertEqual(self.store.context()['state'], 'inactive')



class ReplacementTests(_BindingBase):
    def setUp(self):
        super().setUp()
        self.open_gate()
        self.command = settings_service.SettingsCommand((('LOGIN', 'username', 'new-account'),),
                                                        official_account_confirm=official.CONFIRM)
        self.persist = mock.Mock(side_effect=self._persist)
        self.backups = []
        from core.database import database
        from core.database.engine import get_engine
        database.set_meta(get_engine(), 'binding_test_marker', 'personal-data')

    def _persist(self, command):
        self.official = next(v for s, k, v in command.values if k == 'username')
        return settings_service.SettingsResult(login_changed=True)

    def test_unconfirmed_change_never_deletes_or_saves(self):
        with self.assertRaises(official.BindingError) as exc:
            official.save_settings(replace(self.command, official_account_confirm=''), self.persist)
        self.assertEqual(exc.exception.code, 'official_account_change_confirmation')
        self.persist.assert_not_called()
        self.assertEqual(self.account.count('contributions-delete'), 0)
        self.assertIsNone(official.pending())

    def test_backup_failure_leaves_db_and_cloud_untouched(self):
        from core.database.engine import get_engine
        from core.database import database
        with mock.patch('core.storage.exchange._backup_live', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                official.save_settings(self.command, self.persist)
        self.assertEqual(self.account.count('contributions-delete'), 0)
        self.persist.assert_not_called()
        self.assertEqual(database.get_meta(get_engine(), 'binding_test_marker'), 'personal-data')
        self.assertIsNone(database.get_meta(get_engine(), 'official_account_dataset_key'))
        self.assertEqual(official.pending()['phase'], 'started')

    def test_verified_backup_delete_wipe_save_connections_order(self):
        from core.storage import exchange
        from core.database import database
        from core.database.engine import get_engine
        events = []
        real_backup, real_wipe = exchange._backup_live, database.empty_report_data
        def backup(path):
            events.append('backup')
            value = real_backup(path)
            self.backups.append(value)
            return value
        def wipe(*args, **kwargs):
            self.assertEqual(self.account.count('contributions-delete'), 1)
            events.append('wipe')
            return real_wipe(*args, **kwargs)
        def persist(command):
            self.assertIsNone(database.get_meta(get_engine(), 'binding_test_marker'))
            self.assertIsNone(database.get_meta(get_engine(), 'official_account_dataset_key'))
            events.append('save')
            return self._persist(command)
        with mock.patch.object(exchange, '_backup_live', side_effect=backup), mock.patch.object(database, 'empty_report_data', side_effect=wipe):
            official.save_settings(self.command, persist)
        self.assertEqual(events, ['backup', 'wipe', 'save'])
        self.assertIsNone(official.pending())
        self.assertEqual(self.account.bindings[USER_A['id']], gate.dataset_key('new-account'))
        actions = [a for a, _ in self.account.calls]
        self.assertLess(actions.index('contributions-delete'), len(actions) - 1 - actions[::-1].index('connections'))
        with sqlite3.connect(self.backups[0]) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertIsNone(conn.execute('SELECT value FROM mysafety_sync_meta WHERE key=?', ('official_account_dataset_key',)).fetchone())
            self.assertEqual(conn.execute('SELECT value FROM mysafety_sync_meta WHERE key=?', ('binding_test_marker',)).fetchone()[0], 'personal-data')

    def test_delete_failure_preserves_personal_data_and_resumes_same_backup(self):
        with mock.patch.object(CommunityAccountClient, 'delete_contributions', side_effect=AccountApiError('network_error')):
            with self.assertRaises(official.BindingError):
                official.save_settings(self.command, self.persist)
        journal = official.pending()
        self.assertEqual(journal['phase'], 'backed_up')
        self.persist.assert_not_called()
        from core.database import database
        from core.database.engine import get_engine
        self.assertEqual(database.get_meta(get_engine(), 'binding_test_marker'), 'personal-data')
        self.assertIsNone(database.get_meta(get_engine(), 'official_account_dataset_key'))
        with mock.patch('core.storage.exchange._backup_live', side_effect=AssertionError('must reuse backup')):
            official.save_settings(self.command, self.persist)
        self.assertTrue(Path(journal['backup']).exists())
        self.assertIsNone(official.pending())

    def test_old_delete_response_never_wipes(self):
        with mock.patch.object(CommunityAccountClient, 'delete_contributions', return_value={'protocol': 1}), mock.patch('core.database.database.empty_report_data') as wipe:
            with self.assertRaises(official.BindingError) as exc:
                official.save_settings(self.command, self.persist)
        self.assertEqual(exc.exception.code, 'official_account_protocol_required')
        wipe.assert_not_called()
        self.persist.assert_not_called()

    def test_config_save_failure_stays_blocked_and_resume_does_not_delete_twice(self):
        with self.assertRaises(OSError):
            official.save_settings(self.command, mock.Mock(side_effect=OSError('replace failed')))
        self.assertEqual(official.pending()['phase'], 'wiped')
        self.assertEqual(gate.evaluate()['state'], 'official_account_change_pending')
        self.assertEqual(self.account.count('contributions-delete'), 1)
        official.save_settings(self.command, self.persist)
        self.assertEqual(self.account.count('contributions-delete'), 1)
        self.assertIsNone(official.pending())

    def test_resuming_with_different_target_is_refused(self):
        with mock.patch('core.storage.exchange._backup_live', side_effect=OSError):
            with self.assertRaises(OSError):
                official.save_settings(self.command, self.persist)
        different = replace(self.command, values=(('LOGIN', 'username', 'third-account'),))
        with self.assertRaises(official.BindingError) as exc:
            official.save_settings(different, self.persist)
        self.assertEqual(exc.exception.code, 'official_account_change_pending')

    def test_reverting_config_to_bound_account_keeps_data(self):
        self.official = 'manual-edit'
        command = replace(self.command, values=(('LOGIN', 'username', OFFICIAL_ID),), official_account_confirm='')
        official.save_settings(command, self.persist)
        self.assertEqual(self.account.count('contributions-delete'), 0)
        from core.database import database
        from core.database.engine import get_engine
        self.assertEqual(database.get_meta(get_engine(), 'binding_test_marker'), 'personal-data')
        self.assertIsNone(database.get_meta(get_engine(), 'official_account_dataset_key'))

    def test_legacy_server_allows_unchanged_settings_but_change_still_needs_release(self):
        original = self.account._status
        def old(*args):
            code, value = original(*args)
            value.pop('official_account')
            return code, value
        with mock.patch.object(self.account, '_status', side_effect=old):
            unchanged = replace(self.command, values=(('LOGIN', 'username', OFFICIAL_ID),), official_account_confirm='')
            official.save_settings(unchanged, self.persist)
            self.assertEqual(self.account.count('contributions-delete'), 0)
            with self.assertRaises(official.BindingError) as exc:
                official.save_settings(replace(self.command, official_account_confirm=''), self.persist)
            self.assertEqual(exc.exception.code, 'official_account_change_confirmation')
            with mock.patch.object(CommunityAccountClient, 'delete_contributions', return_value={'protocol': 1}), \
                 mock.patch('core.database.database.empty_report_data') as wipe:
                with self.assertRaises(official.BindingError) as exc:
                    official.save_settings(self.command, self.persist)
            self.assertEqual(exc.exception.code, 'official_account_protocol_required')
            wipe.assert_not_called()

    def test_unbound_server_does_not_skip_confirmation_for_changed_config(self):
        self.account.bindings.pop(USER_A['id'])
        with self.assertRaises(official.BindingError) as exc:
            official.save_settings(replace(self.command, official_account_confirm=''), self.persist)
        self.assertEqual(exc.exception.code, 'official_account_change_confirmation')
        self.assertEqual(self.account.count('contributions-delete'), 0)
        self.persist.assert_not_called()

    def test_settings_apply_cannot_bypass_confirmation(self):
        with self.assertRaises(official.BindingError):
            settings_service.apply(replace(self.command, official_account_confirm=''))
        self.assertEqual(self.account.count('contributions-delete'), 0)


class BackupBindingTests(unittest.TestCase):
    def test_server_and_mobile_check_only_kakao_owner(self):
        from services import account_data
        for kind, table in [('server', 'mysafety_sync_meta'), ('mobile', 'sync_meta')]:
            for value in (None, gate.dataset_key('other'), gate.dataset_key('current')):
                for owner in (None, '910001', '910002'):
                    with self.subTest(kind=kind, value=value, owner=owner), tempfile.TemporaryDirectory() as directory:
                        path = os.path.join(directory, 'backup.db')
                        with sqlite3.connect(path) as conn:
                            conn.execute(f'CREATE TABLE {table} (key TEXT, value TEXT)')
                            if value:
                                conn.execute(f'INSERT INTO {table} VALUES (?, ?)', ('official_account_dataset_key', value))
                            if owner:
                                conn.execute(f'INSERT INTO {table} VALUES (?, ?)', (account_data.KAKAO_MEMBER_META_KEY, owner))
                        if owner == '910001':
                            account_data.refuse_foreign_owner(path, kind, '910001')
                        else:
                            with self.assertRaises(account_data.ForeignDatabaseRefused):
                                account_data.refuse_foreign_owner(path, kind, '910001')

    def test_missing_binding_is_legacy_but_malformed_field_is_refused(self):
        self.assertIsNone(official.binding({}))
        for value in (None, {'official_account': None}, {'official_account': {}},
                      {'official_account': {'dataset_key': 'bad', 'bound_at': None}},
                      {'official_account': {'dataset_key': None}}):
            with self.subTest(value=value), self.assertRaises(official.BindingError):
                official.binding(value)
        self.assertIsNone(official.binding({'official_account': {'dataset_key': None, 'bound_at': None}}))

    def test_http_client_uses_generous_timeout(self):
        import json
        session = mock.Mock()
        session.post.return_value = mock.Mock(status_code=200, text=json.dumps({'protocol': 1}), headers={})
        CommunityAccountClient('http://127.0.0.1', 'fixture', session).status('fixture')
        self.assertEqual(session.post.call_args.kwargs['timeout'], 25)


class BindingCrawlRaceTests(unittest.TestCase):
    def test_binding_loss_during_prepare_cancels_before_process_spawn(self):
        from services.crawl_manager import CrawlManager, CrawlBlockedByRestore
        # A distinct manager keeps other test queues and live process references untouched.
        with mock.patch.object(CrawlManager, "_instance", None):
            manager = CrawlManager()
        with tempfile.TemporaryDirectory() as directory, \
             mock.patch("services.crawl_manager.block_if_fixture"), \
             mock.patch("services.crawl_run_state.create", return_value="fixture-binding-run"), \
             mock.patch("services.crawl_run_state.write"), \
             mock.patch("services.crawl_manager.subprocess.Popen") as spawn:
            generation = manager.restore_generation()
            with self.assertRaises(CrawlBlockedByRestore):
                manager.start_crawl(['fixture-crawler'], directory, os.path.join(directory, 'crawl.log'),
                                    prepare=manager.stop_for_account_binding, restore_generation=generation)
            spawn.assert_not_called()
            self.assertFalse(manager.is_crawling())
            # A start that passed the gate before the cancellation cannot reserve again.
            with self.assertRaises(CrawlBlockedByRestore):
                manager.start_crawl(['fixture-crawler'], directory, os.path.join(directory, 'crawl.log'),
                                    restore_generation=generation)
            spawn.assert_not_called()
