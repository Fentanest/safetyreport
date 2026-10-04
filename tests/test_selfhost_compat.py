import json
import unittest
from pathlib import Path
from unittest import mock

from services import selfhost_compat as compat

HEADERS = {'X-SafetyReport-Client':'mobile','X-SafetyReport-Version':'2.0.0+31','X-SafetyReport-Protocol':'3'}


class CompatibilityVectors(unittest.TestCase):
    def test_vectors(self):
        cases = json.loads((Path(__file__).resolve().parents[1] / 'contracts/selfhost-compat/vectors.json').read_text())['cases']
        for v in cases:
            with self.subTest(v['name']):
                rejection = compat.rejection(v['client'], v['version'], v['protocol'], v['server'])
                self.assertEqual(rejection['code'] if rejection else None, v['code'])


class CompatibilityApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import main
        main.get_app()  # EO R-04: import 만으로는 앱을 만들지 않는다
        from fastapi.testclient import TestClient
        from core.database import database
        from core.database.engine import get_engine
        cls.main, cls.engine = main, get_engine()
        database.upgrade_schema(cls.engine, maintenance=False)
        cls.key = database.create_api_key(cls.engine, 'fixture-protocol3')
        cls.TestClient = TestClient

    @classmethod
    def tearDownClass(cls):
        from core.database import database
        database.delete_api_key(cls.engine, cls.key)

    def setUp(self):
        self.client = self.TestClient(self.main.app)
        self.addCleanup(self.client.close)

    def test_all_external_routes_reject_old_key_only_requests_before_handlers(self):
        from test_community_gate import http_routes
        for method, path in http_routes(self.main.app.routes):
            if not path.startswith('/api/v1/') or path == '/api/v1/server/version' or method == 'OPTIONS':
                continue
            # Guard runs before route parameter validation or any write.
            path = __import__('re').sub(r'\{[^}]+\}', 'fixture', path)
            with self.subTest(method=method, path=path):
                r = self.client.request(method,path,headers={'X-API-Key':self.key})
                self.assertEqual((r.status_code,r.json()['code']),(409,'CLIENT_UPGRADE_REQUIRED'))
                self.assertEqual(r.headers['cache-control'],'no-store')

    def test_query_key_downloads_and_fake_admin_headers_do_not_bypass(self):
        for path in ('/api/v1/settings/db','/api/v1/files/download?target=logs&name=fixture.log','/media/proxy?url=https://example.invalid/a'):
            sep = '&' if '?' in path else '?'
            r = self.client.get(path + sep + 'api_key=' + self.key, headers={'User-Agent':'Mozilla/5.0','X-Admin':'true'})
            self.assertEqual(r.status_code,409)

    def test_http_upgrade_header_is_not_a_websocket_or_admin_session(self):
        with mock.patch('services.community_gate.check_for_request', return_value={'can_enter':True}):
            for path in ('/data/all','/backup/download','/settings/','/stats','/crawl/'):
                with self.subTest(path=path):
                    r = self.client.get(path, headers={'Upgrade':'websocket', 'Connection':'Upgrade', **HEADERS}, follow_redirects=False)
                    self.assertEqual(r.status_code,302)
                    self.assertTrue(r.headers['location'].startswith('/login'))
            r = self.client.post('/backup/upload',headers={'Upgrade':'websocket','Accept':'application/json'},follow_redirects=False)
            self.assertEqual(r.status_code,401)

    def test_probe_is_minimal_and_auth_still_required(self):
        self.assertEqual(self.client.get('/api/v1/server/version').status_code,401)
        r = self.client.get('/api/v1/server/version', headers={'X-API-Key':self.key})
        self.assertEqual(r.status_code,200)
        self.assertEqual({k:r.json()[k] for k in compat.metadata()}, compat.metadata())
        self.assertIn('latest_version',r.json())
        self.assertNotIn('reports',r.json())
        self.assertEqual(self.client.get('/health').status_code,200)

    def test_protocol3_product2_allowed_but_invalid_auth_is_not(self):
        with mock.patch('services.community_gate.check_for_request', return_value={'can_enter':True}):
            r = self.client.get('/api/v1/summary',headers={**HEADERS,'X-API-Key':self.key})
            self.assertEqual(r.status_code,200)
            self.assertEqual(self.client.get('/api/v1/summary',headers={**HEADERS,'X-API-Key':'fixture-invalid'}).status_code,401)
        for value, code in [('2','CLIENT_UPGRADE_REQUIRED'),('4','CLIENT_PROTOCOL_UNSUPPORTED'),('garbage','CLIENT_PROTOCOL_UNSUPPORTED')]:
            r = self.client.get('/api/v1/summary',headers={**HEADERS,'X-API-Key':self.key,'X-SafetyReport-Protocol':value})
            self.assertEqual((r.status_code,r.json()['code']),(409,code))

    def test_every_ws_and_reconnect_refuses_before_data(self):
        from starlette.websockets import WebSocketDisconnect
        for path in ('/ws/events','/crawl/ws/logs','/rating/ws/rating_logs'):
            for attempt in range(2):
                with self.subTest(path=path,attempt=attempt):
                    with self.client.websocket_connect(path+'?api_key='+self.key) as ws:
                        with self.assertRaises(WebSocketDisconnect) as err: ws.receive_json()
                        self.assertEqual(err.exception.code,4406)
            with mock.patch('core.utils.ws_auth.gate_ok',new=mock.AsyncMock(return_value=True)):
                if path == '/ws/events':
                    with self.client.websocket_connect(path+'?api_key='+self.key+'&client_type=mobile&client_version=2.0.0%2B31&client_protocol=3') as ws:
                        self.assertEqual(ws.receive_json()['type'],'connected')
