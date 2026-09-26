"""필수 진입 게이트(K && C) — PC/Docker 회귀 테스트 (contracts/community-ingest/gate.md, plan-final §6.2).

실제 카카오·Supabase 를 부르지 않는다. test_community_auth 의 가짜 Supabase(127.0.0.1 http.server)에
community-account 흉내(FakeAccount)를 붙여 실제 requests 로 통신한다.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
import uuid
from unittest import mock

from services import community_auth_service as cas
from services import community_gate
from services.community_store import CommunityStore
from test_community_auth import USER_A, USER_B, CommunityTestBase, wait_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY = community_gate.REQUIRED_POLICY_VERSION
HASH = community_gate.CONSENT_TEXT_SHA256
OFFICIAL_ID = "Fixture.Official "


class FakeAccount:
    """community-account v1 의 필요한 부분(account-api.md). 사용자는 가짜 GoTrue 세션의 access token 으로 식별."""

    def __init__(self, fake):
        self.fake = fake
        self.consents: dict[str, dict] = {}      # user_id -> {state, grant_id, policy_version, granted_at}
        self.connections: dict[str, dict] = {}   # connection_id -> {...}
        self.contributor: dict[str, str] = {}
        self.policy_hash = HASH
        self.fail: list[tuple[int, str]] = []    # 다음 호출들에 돌려줄 (status, code)
        self.calls: list[tuple[str, dict]] = []
        fake.account_handler = self

    def __call__(self, action, body, token):
        self.calls.append((action, body))
        if self.fail:
            status, code = self.fail.pop(0)
            return status, {"error": {"code": code, "message": code, "retryable": status >= 500}}
        sid, s = self.fake.session_for_access(token)
        if not s:
            return 401, {"error": {"code": "auth_required"}}
        uid = s["user"]["id"]
        handler = getattr(self, "_" + action.replace("-", "_"))
        return handler(uid, sid, body)

    def count(self, action):
        return sum(1 for a, _ in self.calls if a == action)

    def grant(self, user, state="active"):
        self.consents[user["id"]] = {"state": state, "grant_id": str(uuid.uuid4()), "policy_version": POLICY,
                                     "granted_at": "2026-09-26T00:00:00Z"}

    def _status(self, uid, sid, body):
        c = self.consents.get(uid) or {"state": "none", "grant_id": None, "policy_version": None, "granted_at": None}
        if c["state"] == "active" and self.policy_hash != HASH:
            c = dict(c, state="outdated")
        conn = self.connections.get(body.get("connection_id") or "")
        conn_view = None
        if conn and conn["user_id"] == uid:
            conn_view = {"status": conn["status"], "writer_epoch": conn["writer_epoch"],
                         "bound_to_current_session": conn["session"] == sid, "last_accepted_revision": conn["last"],
                         "source_app": "safetyreport", "source_mode": "server", "dataset_key": conn["dataset_key"]}
        contributor = self.contributor.get(uid, "active" if c["state"] != "none" else "none")
        return 200, {"protocol": 1,
                     "gate": {"kakao": True, "consent": c["state"] == "active", "can_enter": c["state"] == "active",
                              "reasons": [] if c["state"] == "active" else [f"consent_{c['state']}"]},
                     "policy": {"required_version": POLICY, "consent_text_sha256": self.policy_hash},
                     "consent": c, "contributor": {"status": contributor}, "connection": conn_view,
                     "projection": {"ready": False},
                     "account": {"fingerprint": community_gate.account_fingerprint(uid), "display_name": "로컬"},
                     "server_time": "2026-09-26T00:00:00.000Z"}

    def _consent(self, uid, sid, body):
        if body.get("policy_version") != POLICY or body.get("consent_text_sha256") != self.policy_hash \
                or body.get("accepted") is not True or body.get("via") != "safetyreport_server":
            return 409, {"error": {"code": "policy_mismatch", "required_version": POLICY}}
        created = (self.consents.get(uid) or {}).get("state") != "active"
        if created:
            self.grant({"id": uid})
        c = self.consents[uid]
        return 200, {"protocol": 1, "grant_id": c["grant_id"], "policy_version": POLICY, "granted_at": c["granted_at"],
                     "created": created}

    def _consent_revoke(self, uid, sid, body):
        c = self.consents.get(uid)
        if not c or c["grant_id"] != body.get("grant_id"):
            return 404, {"error": {"code": "not_found"}}
        already = c["state"] == "revoked"
        c["state"] = "revoked"
        return 200, {"protocol": 1, "grant_id": c["grant_id"], "revoked": True, "already_revoked": already,
                     "lineage_active": False}

    def _connections(self, uid, sid, body):
        active = [c for c in self.connections.values() if c["dataset_key"] == body["dataset_key"] and c["status"] == "active"]
        if active and not body.get("takeover"):
            a = active[0]
            return 409, {"error": {"code": "writer_conflict", "active_writer": {
                "device_label": a["device_label"], "platform": a["platform"], "source_app": "safetyreport",
                "created_at": "2026-09-26T00:00:00Z"}}}
        epoch = max([c["writer_epoch"] for c in self.connections.values() if c["dataset_key"] == body["dataset_key"]] or [0]) + 1
        for a in active:
            a["status"] = "superseded"
        cid = str(uuid.uuid4())
        self.connections[cid] = {"user_id": uid, "session": sid, "dataset_key": body["dataset_key"], "status": "active",
                                 "writer_epoch": epoch, "last": 0, "device_label": body["device_label"],
                                 "platform": body["platform"],
                                 "secret_sha": hashlib.sha256(body["connection_secret"].encode()).hexdigest()}
        return 200, {"protocol": 1, "connection_id": cid, "writer_epoch": epoch, "superseded_previous": bool(active)}

    def _connections_rebind(self, uid, sid, body):
        c = self.connections.get(body.get("connection_id") or "")
        if not c or c["user_id"] != uid or c["secret_sha"] != hashlib.sha256(body["connection_secret"].encode()).hexdigest():
            return 404, {"error": {"code": "not_found"}}
        if c["status"] != "active":
            return 409, {"error": {"code": f"connection_{c['status']}"}}
        c["session"] = sid
        return 200, {"protocol": 1, "connection_id": body["connection_id"], "writer_epoch": c["writer_epoch"],
                     "last_accepted_revision": c["last"]}

    def _connections_revoke(self, uid, sid, body):
        c = self.connections.get(body.get("connection_id") or "")
        if not c or c["user_id"] != uid:
            return 404, {"error": {"code": "not_found"}}
        c["status"] = "revoked"
        return 200, {"protocol": 1, "connection_id": body["connection_id"], "status": "revoked"}

    def _contributions_delete(self, uid, sid, body):
        n = 0
        for c in self.connections.values():
            if c["user_id"] == uid and c["status"] == "active":
                c["status"] = "revoked"
                n += 1
        return 200, {"protocol": 1, "deletion_id": str(uuid.uuid4()), "deleted_facts": 3, "revoked_connections": n,
                     "deleted_at": "2026-09-26T00:00:00Z"}


def inject_module(name, module):
    """sys.modules 와 services 패키지 속성을 함께 바꾸는 patch 두 개(실제 모듈이 이미 import 된 뒤에도 적용)."""
    import services

    return [mock.patch.dict(sys.modules, {name: module}),
            mock.patch.object(services, name.rsplit(".", 1)[1], module, create=True)]


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class GateTestBase(CommunityTestBase):
    def setUp(self):
        super().setUp()
        self.account = FakeAccount(self.fake)
        self.clock = FakeClock()
        self.data = tempfile.mkdtemp(prefix="community-gate-")
        self.addCleanup(shutil.rmtree, self.data, True)
        import settings.settings as app_settings

        patches = [
            mock.patch.object(cas, "_default", self.service),
            mock.patch.object(community_gate, "_gate", community_gate._Gate(clock=self.clock)),
            mock.patch.object(app_settings, "datapath", self.data),
            mock.patch.object(community_gate, "official_username", lambda: self.official),
        ]
        self.official = OFFICIAL_ID
        self.manifest_calls = 0
        fake_uploader = types.ModuleType("services.community_uploader")

        def refresh_server_completed():
            self.manifest_calls += 1
            return True

        fake_uploader.refresh_server_completed = refresh_server_completed
        patches += inject_module("services.community_uploader", fake_uploader)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.service.upload_allowed_provider = cas._gate_can_enter
        self.addCleanup(CommunityStore._forget, os.path.join(self.data, "community.db"))

    @property
    def store(self):
        return CommunityStore.open(self.data)

    def open_gate(self, user=USER_A):
        self.connect(user)
        self.account.grant(user)
        return community_gate.refresh_now()


class DecideTests(unittest.TestCase):
    def status(self, **over):
        base = {"gate": {"kakao": True}, "contributor": {"status": "active"},
                "consent": {"state": "active", "policy_version": POLICY},
                "policy": {"required_version": POLICY, "consent_text_sha256": HASH}}
        base.update(over)
        return base

    def test_order_first_failure_wins(self):
        d = community_gate.decide
        self.assertEqual(d("missing", "none", None, None, True)[0], "config_invalid")
        self.assertEqual(d("conflict", "valid", self.status(), 1, False)[0], "config_invalid")
        self.assertEqual(d("ok", "none", self.status(), 1, False)[0], "kakao_required")
        self.assertEqual(d("ok", "reauth_required", self.status(), 1, False)[0], "kakao_reauth_required")
        self.assertEqual(d("ok", "unreadable", self.status(), 1, False)[0], "session_unreadable")
        self.assertEqual(d("ok", "valid", None, None, False)[0], "verification_required")
        self.assertEqual(d("ok", "valid", self.status(), 1, True)[0], "verification_required")
        self.assertEqual(d("ok", "valid", self.status(), 601, False)[0], "verification_required")
        self.assertEqual(d("ok", "valid", self.status(gate={"kakao": False, "reasons": ["kakao_missing"]}), 1, False),
                         ("kakao_required", ["kakao_missing"]))
        self.assertEqual(d("ok", "valid", self.status(contributor={"status": "suspended"}), 1, False)[0], "suspended")
        self.assertEqual(d("ok", "valid", self.status(contributor={"status": "deletion_pending"}), 1, False)[0], "suspended")
        self.assertEqual(d("ok", "valid", self.status(consent={"state": "revoked"}), 1, False),
                         ("consent_required", ["consent_revoked"]))
        self.assertEqual(d("ok", "valid", self.status(consent={"state": "active", "policy_version": "old"}), 1, False),
                         ("consent_required", ["consent_outdated"]))
        self.assertEqual(d("ok", "valid", self.status(policy={"required_version": POLICY, "consent_text_sha256": "0" * 64}),
                           1, False), ("consent_required", ["consent_outdated"]))
        self.assertEqual(d("ok", "valid", self.status(contributor={"status": "none"}), 600, False), ("ok", []))

    def test_consent_hash_matches_contract_copy(self):
        path = os.path.join(ROOT, community_gate.CONSENT_TEXT_FILE)
        with open(path, "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(), HASH)
        with open(path.replace(".md", ".sha256"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().split()[0], HASH)
        self.assertIn(POLICY, community_gate.consent_text())

    def test_dataset_key_normalizes_official_id(self):
        expected = hashlib.sha256(b"safetyreport-dataset|v1|fixture.official").hexdigest()
        self.assertEqual(community_gate.dataset_key(" Fixture.Official "), expected)
        self.assertIsNone(community_gate.dataset_key("  "))


class GateServiceTests(GateTestBase):
    def test_not_connected_never_calls_network(self):
        result = community_gate.refresh_now()
        self.assertEqual(result["state"], "kakao_required")
        self.assertEqual(self.account.calls, [])
        self.assertFalse(community_gate.evaluate()["can_enter"])

    def test_login_is_not_consent(self):
        self.connect(USER_A)
        result = community_gate.refresh_now()
        self.assertEqual((result["state"], result["can_enter"]), ("consent_required", False))
        self.assertEqual(self.account.count("connections"), 0, "동의 전에는 업로드 연결을 만들지 않는다")
        self.assertNotEqual((self.store.context() or {}).get("state"), "active")

    def test_ok_registers_writer_and_activates_context(self):
        result = self.open_gate()
        self.assertTrue(result["can_enter"], result)
        ctx = self.store.context()
        self.assertEqual(ctx["state"], "active")
        dkey = community_gate.dataset_key(OFFICIAL_ID)
        self.assertEqual(ctx["dataset_key"], dkey)
        self.assertEqual(ctx["contributor_fingerprint"], community_gate.account_fingerprint(USER_A["id"]))
        self.assertEqual((ctx["source_app"], ctx["source_mode"]), ("safetyreport", "server"))
        self.assertEqual((ctx["policy_version"], ctx["consent_text_sha256"]), (POLICY, HASH))
        self.assertEqual(ctx["consent_grant_id"], self.account.consents[USER_A["id"]]["grant_id"])
        conn = self.account.connections[ctx["connection_id"]]
        self.assertEqual((conn["dataset_key"], conn["writer_epoch"], ctx["writer_epoch"]), (dkey, 1, 1))
        self.assertEqual(self.manifest_calls, 1, "새 writer scope 면 manifest 로 완료 목록을 먼저 받는다")
        writer = self.service.store.load_writer()
        with open(self.service.store.writer_path, "rb") as fh:
            blob = fh.read()
        self.assertNotIn(writer["connection_secret"].encode(), blob, "연결 비밀은 암호화 저장")
        self.assertNotIn(writer["connection_secret"], json.dumps(community_gate.status_view()))
        self.assertTrue(self.service.is_upload_allowed())
        # 같은 상태로 다시 확인해도 연결을 새로 만들지 않는다
        community_gate.refresh_now()
        self.assertEqual(self.account.count("connections"), 1)

    def test_cache_ttl_and_require_fresh(self):
        self.open_gate()
        n = self.account.count("status")
        self.clock.now += 59
        community_gate.require_fresh(60)
        self.assertEqual(self.account.count("status"), n, "60초 안이면 다시 묻지 않는다")
        self.clock.now += 2
        self.assertTrue(community_gate.require_fresh(60)["can_enter"])
        self.assertEqual(self.account.count("status"), n + 1, "새 작업은 60초 이내 재검증")
        self.clock.now += 599
        self.assertTrue(community_gate.evaluate()["can_enter"], "화면 이동은 10분 캐시")
        self.clock.now += 2
        self.assertEqual(community_gate.evaluate()["state"], "verification_required")

    def test_network_failure_keeps_cache_only_within_ttl(self):
        self.open_gate()
        self.clock.now += 300
        self.account.fail = [(503, "server_error")]
        self.assertTrue(community_gate.refresh_now()["can_enter"], "장애 때 유효 기간 안 성공 캐시는 유지")
        self.assertEqual(self.store.context()["state"], "active")
        self.clock.now += 301
        self.account.fail = [(503, "server_error")]
        result = community_gate.refresh_now()
        self.assertEqual((result["state"], result["can_enter"]), ("verification_required", False))
        self.assertFalse(self.service.is_upload_allowed())

    def test_remote_revoke_blocks_on_next_poll(self):
        self.open_gate()
        self.account.consents[USER_A["id"]]["state"] = "revoked"
        result = community_gate.refresh_now()
        self.assertEqual((result["state"], result["reasons"]), ("consent_required", ["consent_revoked"]))
        self.assertEqual(self.store.context()["state"], "inactive")
        self.assertEqual(self.store.context()["inactive_reason"], "consent_required")

    def test_auth_rejection_invalidates_immediately(self):
        self.open_gate()
        self.account.fail = [(401, "auth_required")]
        result = community_gate.refresh_now()
        self.assertEqual(result["state"], "verification_required")
        self.assertIn("auth_required", result["reasons"])

    def test_policy_outdated_and_suspended(self):
        self.open_gate()
        self.account.policy_hash = "1" * 64
        self.assertEqual(community_gate.refresh_now()["state"], "consent_required")
        self.account.policy_hash = HASH
        self.account.contributor[USER_A["id"]] = "suspended"
        self.assertEqual(community_gate.refresh_now()["state"], "suspended")
        self.assertEqual(self.store.context()["state"], "inactive")

    def test_logout_then_same_user_rebinds_same_connection(self):
        self.open_gate()
        cid = self.store.context()["connection_id"]
        community_gate.invalidate("logout")
        self.service.disconnect()
        self.assertEqual(community_gate.evaluate()["state"], "kakao_required")
        self.assertEqual(self.store.context()["state"], "inactive")
        self.assertIsNotNone(self.service.store.load_writer(), "로그아웃해도 writer 연결 정보는 남는다")
        self.connect(USER_A)
        community_gate.invalidate("login")
        self.assertTrue(community_gate.refresh_now()["can_enter"])
        self.assertEqual(self.account.count("connections-rebind"), 1)
        self.assertEqual(self.account.count("connections"), 1)
        self.assertEqual(self.store.context()["connection_id"], cid)

    def test_other_user_conflict_then_takeover(self):
        self.open_gate(USER_A)
        first = self.store.context()["connection_id"]
        self.service.disconnect()
        community_gate.invalidate("logout")
        self.connect(USER_B)
        self.account.grant(USER_B)
        community_gate.invalidate("login")
        result = community_gate.refresh_now()
        self.assertTrue(result["can_enter"], "writer 충돌은 진입을 막지 않는다(업로드만 멈춤)")
        view = community_gate.status_view()
        self.assertEqual(view["writer"]["code"], "writer_conflict")
        self.assertEqual(view["writer"]["active_writer"]["device_label"], "테스트 PC")
        self.assertEqual(self.store.context()["state"], "inactive")
        community_gate.request_takeover()
        ctx = self.store.context()
        self.assertEqual(ctx["state"], "active")
        self.assertNotEqual(ctx["connection_id"], first)
        self.assertEqual(ctx["writer_epoch"], 2)
        self.assertEqual(self.account.connections[first]["status"], "superseded")
        self.assertEqual(ctx["contributor_fingerprint"], community_gate.account_fingerprint(USER_B["id"]))

    def test_official_account_missing_blocks_upload_not_entry(self):
        self.official = None
        result = self.open_gate()
        self.assertTrue(result["can_enter"])
        self.assertEqual(community_gate.status_view()["writer"]["code"], "official_account_required")
        self.assertNotEqual((self.store.context() or {}).get("state"), "active")
        self.assertEqual(self.account.count("connections"), 0)

    def test_request_check_throttles_retries_when_offline(self):
        self.connect(USER_A)
        self.account.fail = [(503, "server_error")] * 5
        self.assertEqual(community_gate.check_for_request()["state"], "verification_required")
        n = self.account.count("status")
        self.clock.now += 5
        community_gate.check_for_request()
        self.assertEqual(self.account.count("status"), n, "15초 안에는 다시 시도하지 않는다")
        self.clock.now += 11
        community_gate.check_for_request()
        self.assertEqual(self.account.count("status"), n + 1)

    def test_verify_client_user_token(self):
        self.open_gate(USER_A)
        mine = self.fake.issue_session(USER_A)["access_token"]
        other = self.fake.issue_session(USER_B)["access_token"]
        self.assertTrue(community_gate.verify_client_user_token(mine))
        self.assertFalse(community_gate.verify_client_user_token(other))
        self.assertFalse(community_gate.verify_client_user_token("not-a-token"))
        self.assertFalse(community_gate.verify_client_user_token(None))
        self.assertFalse(community_gate.verify_client_user_token("x" * 9000))

    def test_change_listener_fires_on_loss(self):
        seen = []
        community_gate.on_change(lambda r: seen.append(r["state"]))
        self.open_gate()
        self.account.consents[USER_A["id"]]["state"] = "revoked"
        community_gate.refresh_now()
        self.assertEqual(seen[-1], "consent_required")
        self.assertIn("ok", seen)


def http_routes(routes, prefix=""):
    """FastAPI 0.14x 는 include_router 를 _IncludedRouter 로 감싼다 → 실제 (method, path) 를 펼친다."""
    from fastapi.routing import APIRoute

    for r in routes:
        if isinstance(r, APIRoute):
            for m in r.methods:
                yield m, prefix + r.path
        elif hasattr(r, "original_router"):
            yield from http_routes(r.original_router.routes, prefix + (getattr(r.include_context, "prefix", "") or ""))


class SafeNextTests(unittest.TestCase):
    def test_only_local_relative_paths(self):
        from web.routers.community_onboarding_route import safe_next

        self.assertEqual(safe_next("/stats?law=1"), "/stats?law=1")
        self.assertEqual(safe_next("/settings/"), "/settings/")
        for bad in (None, "", "stats", "//evil.example/x", "https://evil.example", "/\\evil", "/a\nb",
                    "/onboarding/community", "/onboarding/rebuild?x=1", "/login", "/logout", "/" + "a" * 600):
            self.assertEqual(safe_next(bad), "/", bad)


# ── 앱 전체(세션 → 관리자 인증 → 게이트 미들웨어) ─────────────────────────────────────

class GateAppTests(GateTestBase):
    @classmethod
    def setUpClass(cls):
        import warnings

        warnings.simplefilter("ignore", DeprecationWarning)
        import main
        from core.database import database
        from core.database.engine import get_engine
        from fastapi.testclient import TestClient

        cls.main = main
        cls.TestClient = TestClient
        engine = get_engine()
        database.upgrade_schema(engine, maintenance=False)
        if not database.has_admin_user(engine):
            database.create_admin_user(engine, "fixture-admin", "fixture-pass")
        cls.key = database.create_api_key(engine, "게이트 테스트 폰")

    @classmethod
    def tearDownClass(cls):
        from core.database import database
        from core.database.engine import get_engine

        database.delete_api_key(get_engine(), cls.key)

    def setUp(self):
        super().setUp()
        self.client = self.TestClient(self.main.app, base_url="http://testserver")
        self.addCleanup(self.client.close)

    def login(self):
        r = self.client.post("/login", data={"username": "fixture-admin", "password": "fixture-pass"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        page = self.client.get("/onboarding/community")
        self.assertEqual(page.status_code, 200)
        marker = 'data-csrf="'
        start = page.text.index(marker) + len(marker)
        return page.text[start:page.text.index('"', start)]

    def post(self, path, body=None, token=None, headers=None):
        h = {"Accept": "application/json", "Content-Type": "application/json"}
        if token:
            h["X-CSRF-Token"] = token
        h.update(headers or {})
        return self.client.post(path, content=json.dumps(body or {}), headers=h, follow_redirects=False)

    def test_middleware_order_session_auth_gate(self):
        names = []
        for m in self.main.app.user_middleware:
            dispatch = m.kwargs.get("dispatch")
            names.append(dispatch.__name__ if dispatch else m.cls.__name__)
        self.assertEqual(names[0], "_WebSocketSafeSessionMiddleware", names)
        self.assertLess(names.index("auth_middleware"), names.index("community_gate_middleware"),
                        "게이트는 관리자 인증 안쪽(뒤)에서 실행된다")

    def test_admin_auth_runs_before_gate(self):
        r = self.client.get("/settings/", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["location"].startswith("/login"))
        r = self.client.get("/onboarding/community", follow_redirects=False)
        self.assertEqual(r.status_code, 302, "온보딩도 관리자 로그인 뒤에만")

    def test_html_redirects_and_api_403_when_gate_closed(self):
        self.login()
        r = self.client.get("/stats?law=all", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/onboarding/community?next=/stats%3Flaw%3Dall")
        r = self.client.get("/settings/", headers={"Accept": "application/json"}, follow_redirects=False)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "COMMUNITY_ONBOARDING_REQUIRED"))
        r = self.client.post("/crawl/start", data={"crawl_mode": "full"}, follow_redirects=False)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "COMMUNITY_ONBOARDING_REQUIRED"))
        for path in ("/onboarding/community", "/settings/community/status", "/settings/community/policy",
                     "/settings/community/gate", "/health"):
            self.assertEqual(self.client.get(path, headers={"Accept": "application/json"}).status_code, 200, path)
        policy = self.client.get("/settings/community/policy").json()["data"]
        self.assertEqual((policy["policy_version"], policy["consent_text_sha256"]), (POLICY, HASH))

    def test_api_key_first_then_gate(self):
        r = self.client.get("/api/v1/summary", headers={"X-API-Key": "sk-wrong"})
        self.assertEqual(r.status_code, 401, "키가 틀리면 게이트 상태를 드러내지 않고 401")
        r = self.client.get("/api/v1/summary", headers={"X-API-Key": self.key})
        self.assertEqual((r.status_code, r.json()["code"]), (403, "COMMUNITY_ONBOARDING_REQUIRED"))
        r = self.client.post("/api/v1/crawl/start", headers={"X-API-Key": self.key}, json={})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.client.get("/api/v1/app/config", headers={"X-API-Key": self.key}).status_code, 200)
        r = self.client.get("/api/v1/community/gate", headers={"X-API-Key": self.key})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["state"], "kakao_required")
        self.assertEqual(r.headers.get("cache-control"), "no-store")

    def test_media_requires_auth_and_gate(self):
        self.assertEqual(self.client.get("/media/proxy?url=https://example.invalid/a.jpg").status_code, 401)
        r = self.client.get("/media/proxy?url=https://example.invalid/a.jpg", headers={"X-API-Key": self.key})
        self.assertEqual(r.status_code, 403)

    def test_every_route_outside_allowlist_is_gated(self):
        self.login()
        exempt = self.main._GATE_ALLOW
        checked = 0
        for method, route_path in sorted(set(http_routes(self.main.app.routes))):
            if method == "HEAD" or (method, route_path) in exempt:
                continue
            path = route_path.replace("{", "").replace("}", "")
            headers = {"Accept": "application/json"}
            if route_path.startswith("/api/v1/"):
                headers["X-API-Key"] = self.key
            r = self.client.request(method, path, headers=headers, follow_redirects=False)
            self.assertEqual(r.status_code, 403, f"{method} {route_path} → {r.status_code}")
            self.assertEqual(r.json().get("code"), "COMMUNITY_ONBOARDING_REQUIRED", f"{method} {route_path}")
            checked += 1
        self.assertGreater(checked, 60)

    def test_allowlist_entries_are_real_routes(self):
        routes = set(http_routes(self.main.app.routes))
        missing = sorted(e for e in self.main._GATE_ALLOW if e not in routes)
        try:
            import services.community_rebuild  # noqa: F401 - T3b 가 합쳐진 뒤에는 초기화 경로도 반드시 있어야 한다
            allowed_missing = set()
        except ImportError:
            allowed_missing = {e for e in missing if "/rebuild" in e[1]}
        self.assertEqual([e for e in missing if e not in allowed_missing], [])

    def test_websockets_require_auth_and_gate(self):
        from starlette.websockets import WebSocketDisconnect

        with self.assertRaises(WebSocketDisconnect) as ctx:
            with self.client.websocket_connect("/crawl/ws/logs") as ws:
                ws.receive_text()
        self.assertEqual(ctx.exception.code, 4001)
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with self.client.websocket_connect(f"/ws/events?api_key={self.key}") as ws:
                ws.receive_json()
        self.assertEqual(ctx.exception.code, 4403)
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with self.client.websocket_connect(f"/rating/ws/rating_logs?api_key={self.key}") as ws:
                ws.receive_text()
        self.assertEqual(ctx.exception.code, 4403)
        self.login()
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with self.client.websocket_connect("/crawl/ws/logs") as ws:  # 관리자 세션 쿠키는 있지만 게이트 미충족
                ws.receive_text()
        self.assertEqual(ctx.exception.code, 4403)

    def test_onboarding_consent_flow_and_revoke(self):
        token = self.login()
        rid = self.start_and_get_code(USER_A)
        r = self.post("/settings/community/confirm", {"request_id": rid}, token=token)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["gate"]["state"], "consent_required", "로그인만으로는 동의가 아니다")
        self.assertEqual(self.post("/settings/community/consent", {}, token=token).status_code, 400)
        self.assertEqual(self.post("/settings/community/consent", {"accepted": True, "policy_version": POLICY,
                                                                  "consent_text_sha256": "0" * 64}, token=token).status_code, 400)
        self.assertEqual(self.post("/settings/community/consent", {"accepted": True, "policy_version": POLICY,
                                                                  "consent_text_sha256": HASH}).status_code, 403, "CSRF")
        r = self.post("/settings/community/consent", {"accepted": True, "policy_version": POLICY,
                                                     "consent_text_sha256": HASH}, token=token)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["gate"]["state"], "ok")
        consent_calls = [b for a, b in self.account.calls if a == "consent"]
        self.assertEqual(len(consent_calls), 1)
        self.assertEqual((consent_calls[0]["via"], consent_calls[0]["accepted"]), ("safetyreport_server", True))
        self.assertEqual(self.client.get("/settings/", follow_redirects=False).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/summary", headers={"X-API-Key": self.key}).status_code, 200)
        self.assertEqual(self.store.context()["state"], "active")
        body = json.dumps(r.json())
        for secret in self.secret_values() + [self.service.store.load_writer()["connection_secret"]]:
            self.assertNotIn(secret, body)
        r = self.post("/settings/community/consent-revoke", {"confirm": True}, token=token)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["gate"]["state"], "consent_required")
        self.assertEqual(self.store.context()["state"], "inactive")
        r = self.client.get("/settings/", follow_redirects=False)
        self.assertEqual(r.status_code, 302, "철회하면 즉시 필수 설정 화면으로")

    def test_logout_closes_gate_immediately(self):
        token = self.login()
        self.open_gate()
        self.assertEqual(self.client.get("/settings/", follow_redirects=False).status_code, 200)
        r = self.post("/settings/community/disconnect", {}, token=token)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["gate"]["state"], "kakao_required")
        self.assertEqual(self.client.get("/settings/", follow_redirects=False).status_code, 302)
        self.assertEqual(self.store.context()["state"], "inactive")

    def test_config_invalid_recovery_allowed(self):
        token = self.login()
        self.cfg = cas.CommunityConfig(site_url=self.cfg.site_url)
        self.assertEqual(community_gate.evaluate()["state"], "config_invalid")
        page = self.client.get("/onboarding/community")
        self.assertEqual(page.status_code, 200)
        self.assertIn("cmAdvanced", page.text)
        with mock.patch.object(cas, "update_settings", return_value=self.cfg):
            r = self.post("/settings/community/settings", {"supabase_url": "https://abc.supabase.co"}, token=token)
        self.assertEqual(r.status_code, 200, r.text)

    def test_new_work_requires_fresh_gate_and_rebuild(self):
        self.login()
        self.open_gate()
        rebuild = types.ModuleType("services.community_rebuild")
        rebuild.required = lambda: True
        rebuild.blocking_state = lambda: None
        p_mod, p_attr = inject_module("services.community_rebuild", rebuild)
        with p_mod, p_attr, mock.patch("services.crawl_control.start_crawl") as start:
            n = self.account.count("status")
            self.clock.now += 61
            r = self.client.post("/crawl/start", data={"crawl_mode": "full"}, follow_redirects=False)
            self.assertEqual((r.status_code, r.json()["code"]), (409, "COMMUNITY_REBUILD_REQUIRED"))
            self.assertEqual(self.account.count("status"), n + 1, "크롤 시작 전 60초 이내 재검증")
            r = self.client.post("/api/v1/crawl/enqueue", headers={"X-API-Key": self.key}, json={"report_number": "1"})
            self.assertEqual((r.status_code, r.json()["detail"]), (409, "COMMUNITY_REBUILD_REQUIRED"))
            rebuild.required = lambda: False
            r = self.client.post("/crawl/start", data={"crawl_mode": "full"}, follow_redirects=False)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(start.call_count, 1)
            self.account.consents[USER_A["id"]]["state"] = "revoked"
            self.clock.now += 61
            r = self.client.post("/crawl/start", data={"crawl_mode": "full"}, follow_redirects=False)
            self.assertEqual(r.status_code, 403, "철회 뒤 60초 안에 새 작업이 막힌다")
            self.assertEqual(start.call_count, 1)

    def test_client_sensitive_controls_require_phone_user_token(self):
        # Client 민감 제어(수동 업로드·초기화 시작)는 폰 사용자 토큰이 서버 연결 사용자와 같을 때만(plan §6.3).
        self.cfg = cas.CommunityConfig(**{**self.cfg.__dict__, "api_key_managers": frozenset({cas.hash_api_key(self.key)})})
        self.open_gate(USER_A)
        mine = self.fake.issue_session(USER_A)["access_token"]
        other = self.fake.issue_session(USER_B)["access_token"]
        h = {"X-API-Key": self.key}
        with mock.patch("services.community_uploader.request_upload", create=True,
                        return_value={"result": "ok", "counts": {}}) as run:
            r = self.client.post("/api/v1/community/upload/run", headers=h, json={})
            self.assertEqual((r.status_code, r.json()["code"]), (403, "user_token_required"))
            r = self.client.post("/api/v1/community/upload/run", headers={**h, "X-Community-User-Token": other}, json={})
            self.assertEqual((r.status_code, r.json()["code"]), (403, "account_mismatch"))
            self.assertEqual(run.call_count, 0)
            r = self.client.post("/api/v1/community/upload/run", headers={**h, "X-Community-User-Token": mine}, json={})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(run.call_count, 1)
        r = self.client.post("/api/v1/community/rebuild/start", headers=h, json={})
        self.assertEqual((r.status_code, r.json()["code"]), (403, "user_token_required"))

    def test_gate_loss_closes_event_websockets(self):
        with mock.patch("services.ws_manager.ws_manager.close_all_from_thread") as close_all:
            self.main._on_community_gate_change({"state": "consent_required", "can_enter": False})
            close_all.assert_called_once_with(4403, "COMMUNITY_ONBOARDING_REQUIRED")
            close_all.reset_mock()
            # Sol M-01: 캐시 만료·중앙 장애로 확인이 필요한 상태도 게이트 상실이다 → 닫는다.
            self.main._on_community_gate_change({"state": "verification_required", "can_enter": False})
            close_all.assert_called_once_with(4403, "COMMUNITY_ONBOARDING_REQUIRED")

    def test_broadcast_checks_the_gate_before_sending(self):
        import asyncio

        from services.ws_manager import WsManager

        class FakeWs:
            def __init__(self):
                self.sent, self.closed = [], None

            async def send_text(self, text):
                self.sent.append(text)

            async def close(self, code, reason=""):
                self.closed = code

        mgr = WsManager()
        ws = FakeWs()
        mgr._connections["t-open"] = ws
        self.addCleanup(mgr._connections.pop, "t-open", None)
        self.addCleanup(mgr._connection_meta.pop, "t-open", None)
        with mock.patch.object(community_gate, "evaluate", return_value={"state": "ok", "can_enter": True}):
            asyncio.run(mgr.broadcast("crawl_done", {"n": 1}))
        self.assertEqual(len(ws.sent), 1)
        with mock.patch.object(community_gate, "evaluate", return_value={"state": "verification_required", "can_enter": False}):
            asyncio.run(mgr.broadcast("crawl_done", {"n": 2}))
        self.assertEqual(len(ws.sent), 1, "게이트가 닫히면 보내지 않는다")
        self.assertEqual(ws.closed, 4403)
        self.assertNotIn("t-open", mgr._connections)


if __name__ == "__main__":
    unittest.main()
