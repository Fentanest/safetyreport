import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from core.database import models
from core.utils import logger
from scripts.dev import fixture_server
from web.routers import api_route


class ApiInputBoundaryTests(unittest.TestCase):
    def setUp(self):
        logger.LoggerFactory.create_logger(mode='crawl')
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.engine = create_engine(f"sqlite:///{Path(self.directory.name) / 'data.db'}")
        self.addCleanup(self.engine.dispose)
        key = fixture_server.seed_engine(self.engine)['api_key']
        patch = mock.patch.object(api_route, 'engine', self.engine)
        patch.start()
        self.addCleanup(patch.stop)
        app = FastAPI()
        app.include_router(api_route.router)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.headers = {'X-API-Key': key, 'X-SafetyReport-Client': 'mobile',
                        'X-SafetyReport-Version': '2.0.0+31', 'X-SafetyReport-Protocol': '3'}

    def test_literal_bulk_route_changes_the_requested_group(self):
        with self.engine.connect() as conn:
            gid = conn.execute(select(models.duplicate_group_table.c.group_id)).scalar_one()
        response = self.client.post('/api/v1/duplicates/groups/bulk-status', headers=self.headers,
                                    json={'group_ids': [gid], 'duplicate_status': 'review_required'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'success', 'updated': 1})
        with self.engine.connect() as conn:
            self.assertEqual(conn.execute(select(models.duplicate_group_table.c.status)).scalar_one(), 'review_required')

    def test_non_object_and_malformed_json_are_rejected_before_service(self):
        paths = ['/watchlist', '/duplicates/groups/bulk-status', '/rating/start']
        with mock.patch.object(api_route.duplicate_group_service, 'bulk_update_duplicate_status') as update:
            for path in paths:
                for body in ('null', '[]', '"text"', '12', '{'):
                    with self.subTest(path=path, body=body):
                        response = self.client.post('/api/v1' + path, headers={**self.headers, 'Content-Type': 'application/json'}, content=body)
                        self.assertEqual(response.status_code, 400)
                        self.assertIn('detail', response.json())
            update.assert_not_called()

    def test_body_validation_does_not_bypass_api_key(self):
        response = self.client.post('/api/v1/duplicates/groups/bulk-status', json={})
        self.assertEqual(response.status_code, 401)

    def test_invalid_rating_score_has_a_client_error(self):
        for score in (None, 'bad', {}, True, 2.5):
            with self.subTest(score=score):
                response = self.client.post('/api/v1/rating/start', headers=self.headers,
                                            json={'report_numbers': ['SPP-TEST'], 'score': score})
                self.assertEqual(response.status_code, 400)

    def test_oversized_or_incorrectly_typed_body_never_reaches_mutation(self):
        with mock.patch.object(api_route.duplicate_group_service, 'bulk_update_duplicate_status') as update:
            for body in ({'group_ids': 'group'}, {'group_ids': [1]}, {'group_ids': ['']}, {'note': {}}):
                response = self.client.post('/api/v1/duplicates/groups/bulk-status', headers=self.headers, json=body)
                self.assertEqual(response.status_code, 400)
            response = self.client.post('/api/v1/duplicates/groups/bulk-status',
                headers={**self.headers, 'Content-Type': 'application/json'}, content=b' ' * (1024 * 1024 + 1))
            self.assertEqual(response.status_code, 413)
            update.assert_not_called()
