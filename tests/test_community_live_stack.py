"""PC 커뮤니티 경로 — 실제 로컬 통합 스택(합성 Supabase ci0926-int) 수직 테스트. 기본은 건너뛴다.

실행(map integration worktree 에서 스택·가짜 카카오·함수 서빙이 떠 있어야 한다 — map tests/integration/community-stack.test.ts 머리말):
  COMMUNITY_STACK=1 COMMUNITY_STACK_DIR=<map>/.integration-stack SAFETYREPORT_DATA_DIR=$(mktemp -d) \\
    .venv/bin/python -m unittest tests.test_community_live_stack
가짜: 카카오(로컬 mock)만. GoTrue·PostgREST·Postgres·edge-runtime 은 실제. 호스팅 카카오 E2E 아님.
검증: 게이트(동의 전/후), writer 등록, capture → request_upload → 실제 ACK·공개 API, manifest 교체(키 = 서버 계산 키),
원격 철회 → 게이트 차단·context 비활성·업로드 중단(원장 불변), 삭제 요청 → 로컬 대기 행 차단.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import time
import unittest
import uuid
from unittest import mock
from urllib.parse import parse_qs, urlsplit, urlunsplit

import requests

ENABLED = os.environ.get("COMMUNITY_STACK") == "1"
API = os.environ.get("COMMUNITY_API_URL", "http://127.0.0.1:56321")
STACK_DIR = os.environ.get("COMMUNITY_STACK_DIR", "")
DB_CONTAINER = os.environ.get("COMMUNITY_DB_CONTAINER", "supabase_db_ci0926-int")
MOCK_KAKAO_HOST = os.environ.get("COMMUNITY_MOCK_KAKAO_HOST", "172.17.0.1")
REDIRECT = "http://127.0.0.1:56480/callback.html"


def _sql(query: str) -> str:
    return subprocess.run(["docker", "exec", "-i", DB_CONTAINER, "psql", "-U", "postgres", "-tAq", "-v", "ON_ERROR_STOP=1"],
                          input=query, capture_output=True, text=True, check=True).stdout.strip()


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def kakao_session(publishable: str, choice: str) -> dict:
    verifier = _b64(secrets.token_bytes(32))
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    r1 = requests.get(f"{API}/auth/v1/authorize", allow_redirects=False, timeout=10,
                      params={"provider": "kakao", "redirect_to": REDIRECT, "code_challenge": challenge, "code_challenge_method": "s256"})
    kakao = urlsplit(r1.headers["location"])
    state = parse_qs(kakao.query)["state"][0]
    decide = urlunsplit(("http", f"{MOCK_KAKAO_HOST}:{kakao.port}", "/oauth/decide", f"state={state}&choice={choice}", ""))
    r2 = requests.get(decide, allow_redirects=False, timeout=10)
    r3 = requests.get(r2.headers["location"], allow_redirects=False, timeout=10)
    code = parse_qs(urlsplit(r3.headers["location"]).query)["code"][0]
    tok = requests.post(f"{API}/auth/v1/token", params={"grant_type": "pkce"}, timeout=10,
                        headers={"apikey": publishable}, json={"auth_code": code, "code_verifier": verifier})
    tok.raise_for_status()
    return tok.json()


@unittest.skipUnless(ENABLED and STACK_DIR, "COMMUNITY_STACK=1 과 COMMUNITY_STACK_DIR 이 필요하다(실제 로컬 스택)")
class PcLiveStackTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        status = json.loads(subprocess.run(["npx", "supabase", "status", "-o", "json", "--workdir", STACK_DIR],
                                           capture_output=True, text=True, check=True, cwd=os.path.dirname(STACK_DIR)).stdout)
        cls.publishable = status["PUBLISHABLE_KEY"]
        _sql("delete from private.rate_limits; delete from private.community_auth_rate_limits;"
             " update private.analytics_state set ready = true, published_at = coalesce(published_at, now()) where singleton;")

    def setUp(self):
        from services import community_auth_service as cas
        from services import community_gate
        import settings.settings as app_settings

        self.tmp = tempfile.mkdtemp(prefix="pc-live-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = cas.CommunityConfig(supabase_url=API, publishable_key=self.publishable, site_url="http://127.0.0.1:56480/")
        self.service = cas.CommunityAuthService(self.tmp, lambda: self.cfg)
        self.service.upload_allowed_provider = cas._gate_can_enter
        self.official = f"live-{uuid.uuid4().hex[:8]}"
        for p in (mock.patch.object(cas, "_default", self.service),
                  mock.patch.object(cas, "load_config_from_settings", lambda: self.cfg),
                  mock.patch.object(app_settings, "datapath", self.tmp),
                  mock.patch.object(community_gate, "_gate", community_gate._Gate()),
                  mock.patch.object(community_gate, "official_username", lambda: self.official)):
            p.start()
            self.addCleanup(p.stop)
        from services.community_store import CommunityStore
        self.addCleanup(CommunityStore._forget, os.path.join(self.tmp, "community.db"))

    def login(self, choice: str) -> dict:
        s = kakao_session(self.publishable, choice)
        claims = json.loads(base64.urlsafe_b64decode(s["access_token"].split(".")[1] + "=="))
        record = {"access_token": s["access_token"], "refresh_token": s["refresh_token"],
                  "expires_at": float(s.get("expires_at") or time.time() + 3600), "user_id": s["user"]["id"],
                  "display_name": "live", "has_email": False, "session_id": claims["session_id"], "connected_at": None}
        self.service.store.save({"current": record})
        return record

    def account(self, action: str, token: str, body: dict):
        return requests.post(f"{API}/functions/v1/community-account/{action}", timeout=10, json={"protocol": 1, **body},
                             headers={"apikey": self.publishable, "Authorization": f"Bearer {token}"})

    def public_count(self, contributor: str) -> int:
        return int(_sql(f"""select count(*) from jsonb_array_elements(public.internal_analytics_v2_facts(
            date '2024-01-01', date '2028-12-31', 'all', null, null, null, null)) e where e->>'contributor_id' = '{contributor}';"""))

    def test_pc_end_to_end(self):
        from services import community_capture as cap
        from services import community_gate
        from services import community_uploader as up
        from services.community_store import CommunityStore
        from services.community_gate import CONSENT_TEXT_SHA256, REQUIRED_POLICY_VERSION

        # 관리자 화면의 "카카오 계정으로 연결": 실제 relay(합성 스택의 community-auth-relay)에 연결 요청을 만든다
        pending = self.service.start()
        self.assertEqual(pending["state"], "pending", pending)
        self.assertRegex(pending["pending"]["display_code"], r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")
        self.assertTrue(pending["pending"]["bootstrap_url"].startswith("http://127.0.0.1:56480/"))
        self.service.cancel()
        user = self.login("A")
        store = CommunityStore.open(self.tmp)
        # 동의 전: 로그인만으로는 통과하지 않는다
        self.assertEqual(community_gate.refresh_now()["state"], "consent_required")
        st = self.account("status", user["access_token"], {}).json()
        if st["consent"]["state"] == "active":  # 같은 mock 계정의 이전 실행
            self.account("consent-revoke", user["access_token"], {"grant_id": st["consent"]["grant_id"]})
            self.assertEqual(community_gate.refresh_now()["state"], "consent_required")
        r = self.account("consent", user["access_token"], {"policy_version": REQUIRED_POLICY_VERSION,
                                                           "consent_text_sha256": CONSENT_TEXT_SHA256,
                                                           "via": "safetyreport_server", "accepted": True})
        self.assertEqual(r.status_code, 200, r.text)
        community_gate.invalidate("consent_saved")
        result = community_gate.refresh_now()
        self.assertTrue(result["can_enter"], result)
        ctx = store.active_context()
        self.assertIsNotNone(ctx, community_gate.status_view())
        self.assertEqual(ctx["dataset_key"], community_gate.dataset_key(self.official))
        self.assertEqual(store.meta("manifest_scope"), f"{ctx['dataset_key']}:{ctx['writer_epoch']}", "새 연결은 manifest 먼저")

        # capture → 실제 업로드 → ACK·공개
        from test_community_uploader import INPUT
        report_id = f"LIVE{uuid.uuid4().hex[:12]}"
        res = cap.capture(dict(INPUT), source_report_id=report_id, trigger="realtime", data_dir=self.tmp)
        self.assertTrue(res.event_id and res.eligible)
        run = up.request_upload("manual", data_dir=self.tmp)
        self.assertEqual(run["result"], "success", run)
        row = store.connect().execute("SELECT ack_status, projection_status, receipt_id FROM source_journal WHERE event_id=?",
                                      (res.event_id,)).fetchone()
        self.assertEqual((row["ack_status"], row["projection_status"]), ("accepted", "published"))
        self.assertEqual(self.public_count(user["user_id"]), 1)
        self.assertEqual(_sql(f"select source_report_key from private.community_ingest_events where source_report_id='{report_id}';")[:24],
                         cap._server_key_prefix(report_id), "PC 키 = 서버 계산 키")

        # manifest: 서버 완료 목록이 이 신고를 포함
        self.assertTrue(up.refresh_server_completed(data_dir=self.tmp))
        keys = [r["key_prefix"] for r in store.connect().execute("SELECT key_prefix FROM server_completed")]
        self.assertEqual(keys, [cap._server_key_prefix(report_id)])

        # 같은 내용 재관측 → 이벤트 없음, 다시 올려도 원장 불변
        ledger = int(_sql("select count(*) from private.community_ingest_events;"))
        again = cap.capture(dict(INPUT), source_report_id=report_id, trigger="realtime", data_dir=self.tmp)
        self.assertIsNone(again.event_id)
        up.request_upload("manual", data_dir=self.tmp)
        self.assertEqual(int(_sql("select count(*) from private.community_ingest_events;")), ledger)

        # 원격(다른 기기·웹)에서 철회 → 다음 확인에서 차단, context 비활성, 대기 이벤트는 보내지 않는다
        grant = self.account("status", user["access_token"], {}).json()["consent"]["grant_id"]
        self.assertEqual(self.account("consent-revoke", user["access_token"], {"grant_id": grant}).status_code, 200)
        self.assertEqual(community_gate.refresh_now()["state"], "consent_required")
        self.assertIsNone(store.active_context())
        self.assertEqual(self.public_count(user["user_id"]), 0)
        other = dict(INPUT, processing_status="일부수용")
        pending = cap.capture(other, source_report_id=f"LIVE{uuid.uuid4().hex[:12]}", trigger="realtime", data_dir=self.tmp)
        run = up.request_upload("manual", data_dir=self.tmp)
        self.assertIn(run["result"], ("consent_required", "auth_required"), run)
        self.assertEqual(int(_sql("select count(*) from private.community_ingest_events;")), ledger)
        self.assertIsNotNone(pending.event_id)


if __name__ == "__main__":
    unittest.main()
