import sys, os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT), str(ROOT / 'tests')]
from scripts.dev import fixture_server
DATA = ROOT / '.agent-runs/official-binding-rc/browser-data'
fixture_server.activate_environment(fixture_server.prepare_data_dir(str(DATA), reset=True))
fixture_server.seed(DATA)
import main
main.get_app()
from test_community_gate import FakeAccount, GateTestBase, OFFICIAL_ID
from services import community_gate, community_auth_service as cas
import settings.settings as settings
# Same real loopback fake API as integration tests, without any mocked gate/auth.
test = GateTestBase()
test.setUp()
(DATA / 'fake-data-path.txt').write_text(test.data)
test.connect()
test.account.grant(__import__('test_community_auth').USER_A)
# GateTestBase normally substitutes official_username; restore real config reader for UI tests.
from unittest import mock
# Keep its isolated cloud/session patches; use real username reader on this server.
def username():
    settings._instance.load()
    return settings._instance.username
community_gate.official_username = username
settings._instance.update_config('LOGIN', 'username', OFFICIAL_ID)
settings._instance.save()
original_call = FakeAccount.__call__
def call(self, action, body, token):
    mode_file = DATA / 'mode'
    mode = mode_file.read_text().strip() if mode_file.exists() else 'ok'
    if mode == 'offline':
        return 503, {'error': {'code': 'server_error'}}
    if mode == 'taken' and action == 'connections':
        return 409, {'error': {'code': 'official_account_taken'}}
    if mode == 'unbound' and action == 'status':
        self.bindings.clear()
        for connection in self.connections.values():
            connection['status'] = 'revoked'
        mode_file.write_text('ok')
    code, result = original_call(self, action, body, token)
    if mode == 'old' and action == 'status':
        result.pop('official_account', None)
    return code, result
FakeAccount.__call__ = call
assert community_gate.refresh_now()['can_enter']
import uvicorn
try:
    uvicorn.run(main.app, host='127.0.0.1', port=18806, log_level='warning')
finally:
    test.doCleanups()
