"""커뮤니티 계정(safeauth) 기기 연결 — PC/Docker 쪽 회귀 테스트.

실제 카카오·Supabase 를 부르지 않는다. 가짜 Supabase(중계 + GoTrue 흉내)를 이 프로세스 안의 http.server 로 띄우고
실제 requests 클라이언트로 통신한다(127.0.0.1 → fixture 모드에서도 허용되는 loopback).
"""
from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from services import community_auth_client as cac
from services import community_auth_service as cas
from services.community_auth_store import CommunitySessionStore, StoreUnreadable

SITE_URL = "http://127.0.0.1:8480/safeauth/"
USER_A = {"id": "0f1e2d3c-4b5a-4968-8776-a5b4c3d2e1f0", "email": None,
          "user_metadata": {"name": "로컬A", "nickname": "로컬A"}}
USER_B = {"id": "9a8b7c6d-5e4f-4a3b-9c2d-1e0f9a8b7c6d", "email": "b-user@example.invalid",
          "user_metadata": {"nickname": "로컬B"}}


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def fake_jwt(claims: dict) -> str:
    return ".".join([_b64(b'{"alg":"HS256","typ":"JWT"}'), _b64(json.dumps(claims).encode()), _b64(os.urandom(16))])


class FakeSupabase:
    """중계(community-auth-relay)와 GoTrue 의 필요한 부분만 흉내 낸다. 모든 호출을 기록한다."""

    def __init__(self):
        self.lock = threading.Lock()
        self.calls: list[dict] = []
        self.requests: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}       # session_id -> {user, refresh, access, revoked}
        self.refresh_index: dict[str, str] = {}   # refresh token -> session_id
        self.codes: dict[str, dict] = {}          # auth_code -> {challenge, user, used}
        self.poll_script: dict[str, list] = {}    # request_id -> list of responses (dict or 'drop')
        self.exchange_fail: str | None = None
        self.refresh_mode = "ok"
        self.refresh_delay = 0.0
        self.complete_fail: list[int] = []        # 순서대로 돌려줄 HTTP 상태(빈 목록이면 성공)
        self.expires_in = 3600
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # 기록 도우미
    def calls_to(self, suffix: str) -> list[dict]:
        with self.lock:
            return [c for c in self.calls if c["path"].endswith(suffix)]

    def count(self, suffix: str) -> int:
        return len(self.calls_to(suffix))

    def all_text(self) -> str:
        with self.lock:
            return json.dumps(self.calls, ensure_ascii=False)

    def issue_session(self, user: dict) -> dict:
        sid = str(uuid.uuid4())
        refresh = _b64(os.urandom(12))
        access = fake_jwt({"sub": user["id"], "session_id": sid, "role": "authenticated", "iat": int(time.time())})
        with self.lock:
            self.sessions[sid] = {"user": user, "refresh": refresh, "access": access, "revoked": False}
            self.refresh_index[refresh] = sid
        return {"access_token": access, "refresh_token": refresh, "expires_in": self.expires_in,
                "expires_at": int(time.time()) + self.expires_in, "token_type": "bearer", "user": user}

    def session_for_access(self, access: str):
        with self.lock:
            for sid, s in self.sessions.items():
                if s["access"] == access and not s["revoked"]:
                    return sid, s
        return None, None

    def give_code(self, request_id: str, user: dict) -> str:
        code = str(uuid.uuid4())
        with self.lock:
            self.codes[code] = {"challenge": self.requests[request_id]["code_challenge"], "user": user, "used": False}
            self.requests[request_id]["code"] = code
        return code

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body=None, headers=None):
                data = b"" if body is None else json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _record(self, body):
                parts = urlsplit(self.path)
                with fake.lock:
                    fake.calls.append({"method": self.command, "path": parts.path, "query": parts.query,
                                       "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})

            def do_GET(self):
                self._record(None)
                if urlsplit(self.path).path == "/auth/v1/user":
                    token = self.headers.get("Authorization", "")[7:]
                    sid, s = fake.session_for_access(token)
                    return self._send(200, s["user"]) if s else self._send(401, {"error_code": "bad_jwt"})
                self._send(404, {})

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = json.loads(raw) if raw else {}
                self._record(body)
                parts = urlsplit(self.path)
                path, query = parts.path, parse_qs(parts.query)
                if path.startswith("/functions/v1/community-auth-relay/"):
                    return self._relay(path.rsplit("/", 1)[1], body)
                if path == "/auth/v1/token" and query.get("grant_type") == ["pkce"]:
                    return self._pkce(body)
                if path == "/auth/v1/token" and query.get("grant_type") == ["refresh_token"]:
                    return self._refresh(body)
                if path == "/auth/v1/logout":
                    token = self.headers.get("Authorization", "")[7:]
                    sid, s = fake.session_for_access(token)
                    if not s:
                        return self._send(401, {"error_code": "session_not_found"})
                    if query.get("scope") == ["local"]:
                        with fake.lock:
                            s["revoked"] = True
                    return self._send(204)
                self._send(404, {})

            def _relay(self, action, body):
                if body.get("protocol") != 1:
                    return self._send(400, {"error": {"code": "unsupported_protocol"}})
                if action == "requests":
                    rid = str(uuid.uuid4())
                    with fake.lock:
                        fake.requests[rid] = dict(body, phase="created", delivery=None)
                    return self._send(201, {
                        "protocol": 1, "request_id": rid, "bootstrap_url": f"{SITE_URL}#r={rid}&t={_b64(os.urandom(32))}",
                        "display_code": "ABCD-2345", "expires_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() + 600)),
                        "poll_interval_seconds": 5, "code_ttl_seconds": 120})
                req = fake.requests.get(body.get("request_id"))
                if not req:
                    return self._send(404, {"error": {"code": "not_found"}})
                if action == "poll":
                    if body["device_secret"] != req["device_secret"]:
                        return self._send(404, {"error": {"code": "not_found"}})
                    script = fake.poll_script.get(body["request_id"]) or []
                    if script:
                        item = script.pop(0)
                        if item == "drop":  # 응답 유실 흉내: 서버는 처리했지만 클라이언트는 5xx 를 본다
                            if req.get("code"):
                                req["delivery"] = req["delivery"] or body["delivery_key"]
                            return self._send(503, {"error": {"code": "server_error"}})
                        if item == "429":
                            return self._send(429, {"error": {"code": "rate_limited", "retryAfterSeconds": 0}},
                                              {"Retry-After": "0"})
                        return self._send(200, dict(item, protocol=1))
                    if req.get("code"):
                        if req["delivery"] and req["delivery"] != body["delivery_key"]:
                            return self._send(409, {"error": {"code": "delivery_conflict"}})
                        req["delivery"] = body["delivery_key"]
                        return self._send(200, {"protocol": 1, "status": "code", "auth_code": req["code"],
                                                "code_expires_at": "2099-01-01T00:00:00Z"})
                    return self._send(200, {"protocol": 1, "status": "pending", "phase": req["phase"],
                                            "expires_at": "2099-01-01T00:00:00Z", "poll_after_seconds": 5})
                if action == "complete":
                    if fake.complete_fail:
                        status = fake.complete_fail.pop(0)
                        return self._send(status, {"error": {"code": "server_error" if status >= 500 else "auth_invalid"}})
                    token = self.headers.get("Authorization", "")[7:]
                    sid, s = fake.session_for_access(token)
                    if not s or body["device_secret"] != req["device_secret"]:
                        return self._send(401, {"error": {"code": "auth_invalid"}})
                    req["phase"] = "device_confirmed"
                    return self._send(200, {"protocol": 1, "phase": "device_confirmed"})
                if action == "cancel":
                    if body.get("actor") != "device" or body.get("secret") != req["device_secret"]:
                        return self._send(404, {"error": {"code": "not_found"}})
                    req["phase"] = "cancelled"
                    return self._send(200, {"protocol": 1, "phase": "cancelled"})
                return self._send(404, {"error": {"code": "not_found"}})

            def _pkce(self, body):
                if fake.exchange_fail:
                    return self._send(400, {"code": 400, "error_code": fake.exchange_fail, "msg": "x"})
                entry = fake.codes.get(body.get("auth_code"))
                if not entry or entry["used"]:
                    return self._send(404, {"code": 404, "error_code": "flow_state_not_found"})
                if cac.pkce_challenge(body.get("code_verifier", "")) != entry["challenge"]:
                    return self._send(400, {"code": 400, "error_code": "bad_code_verifier"})
                entry["used"] = True
                return self._send(200, fake.issue_session(entry["user"]))

            def _refresh(self, body):
                if fake.refresh_delay:
                    time.sleep(fake.refresh_delay)
                if fake.refresh_mode == "5xx":
                    return self._send(503, {"code": 503, "msg": "unavailable"})
                sid = fake.refresh_index.get(body.get("refresh_token"))
                if fake.refresh_mode == "invalid" or not sid:
                    return self._send(400, {"code": 400, "error_code": "refresh_token_not_found"})
                s = fake.sessions[sid]
                if s["revoked"]:
                    return self._send(400, {"code": 400, "error_code": "session_not_found"})
                if s["refresh"] != body["refresh_token"]:
                    return self._send(400, {"code": 400, "error_code": "refresh_token_already_used"})
                new_refresh = _b64(os.urandom(12))
                new_access = fake_jwt({"sub": s["user"]["id"], "session_id": sid, "role": "authenticated",
                                       "iat": int(time.time()), "n": _b64(os.urandom(4))})
                with fake.lock:
                    s["refresh"], s["access"] = new_refresh, new_access
                    fake.refresh_index[new_refresh] = sid
                return self._send(200, {"access_token": new_access, "refresh_token": new_refresh,
                                        "expires_in": fake.expires_in, "expires_at": int(time.time()) + fake.expires_in,
                                        "user": s["user"]})

        return Handler


def wait_for(predicate, timeout=5.0, interval=0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


FORBIDDEN_KEYS = ("access_token", "refresh_token", "code_verifier", "device_secret", "delivery_key", "user_id")


class CommunityTestBase(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSupabase()
        self.addCleanup(self.fake.close)
        self.tmp = tempfile.mkdtemp(prefix="community-auth-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cfg = cas.CommunityConfig(enabled=True, supabase_url=self.fake.url, publishable_key="sb_publishable_testkey1234567890",
                                       site_url=SITE_URL, device_label="테스트 PC")
        self.service = self.make_service(self.tmp)

    def make_service(self, datapath, **kw):
        service = cas.CommunityAuthService(datapath, lambda: self.cfg, dockerenv_path=os.path.join(datapath, "nope"),
                                           poll_interval_override=0.02, retry_base_seconds=0.01, **kw)
        self.addCleanup(service.shutdown, 1.0)
        return service

    def state(self):
        return self.service.store.load()

    def start_and_get_code(self, user=USER_A, service=None):
        service = service or self.service
        dto = service.start()
        rid = dto["pending"]["request_id"]
        self.fake.give_code(rid, user)
        self.assertTrue(wait_for(lambda: service.status(can_manage=True)["state"] == "confirm_required"),
                        service.status(can_manage=True))
        return rid

    def connect(self, user=USER_A, service=None):
        service = service or self.service
        rid = self.start_and_get_code(user, service)
        return service.confirm(rid)

    def assert_no_secrets(self, payload, extra=()):
        text = json.dumps(payload, ensure_ascii=False)
        for key in FORBIDDEN_KEYS:
            self.assertNotIn(key, text)
        for secret in self.secret_values() + list(extra):
            self.assertNotIn(secret, text)

    def secret_values(self) -> list[str]:
        values = [USER_A["id"], USER_B["id"], USER_B["email"]]
        try:
            st = self.service.store.load()
        except StoreUnreadable:
            return values
        for rec in (st.get("current"), (st.get("pending") or {}).get("candidate")):
            if rec:
                values += [rec["access_token"], rec["refresh_token"]]
        p = st.get("pending") or {}
        values += [p[k] for k in ("code_verifier", "device_secret", "delivery_key") if p.get(k)]
        with self.fake.lock:
            for s in self.fake.sessions.values():
                values += [s["access"], s["refresh"]]
            values += list(self.fake.codes)
        return values


# ── 저장소 ────────────────────────────────────────────────────────────────────

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="community-store-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.store = CommunitySessionStore(self.tmp)

    def test_roundtrip_encrypted_and_modes(self):
        self.store.save({"current": {"access_token": "AT-plain-marker", "refresh_token": "RT-plain-marker"}})
        with open(self.store.session_path, "rb") as fh:
            blob = fh.read()
        self.assertNotIn(b"AT-plain-marker", blob)
        self.assertEqual(self.store.load()["current"]["refresh_token"], "RT-plain-marker")
        if os.name == "posix":
            for path in (self.store.session_path, self.store.key_path):
                self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600, path)

    def test_key_is_separate_from_config_key(self):
        from core.utils import security

        security.encrypt_config_value("x", self.tmp)  # .config_key 를 만든다
        self.store.save({"current": {"access_token": "a", "refresh_token": "b"}})
        with open(os.path.join(self.tmp, "auth", ".config_key"), "rb") as a, open(self.store.key_path, "rb") as b:
            self.assertNotEqual(a.read().strip(), b.read().strip())
        self.assertTrue(self.store.key_path.endswith(".community_key"))

    def test_atomic_replace_keeps_old_file_on_failure(self):
        self.store.save({"current": {"access_token": "old", "refresh_token": "old"}})
        with mock.patch("services.community_auth_store.os.replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                self.store.save({"current": {"access_token": "new", "refresh_token": "new"}})
        self.assertEqual(self.store.load()["current"]["access_token"], "old")
        leftovers = [n for n in os.listdir(self.store.auth_dir) if n.startswith(".tmp-")]
        self.assertEqual(leftovers, [])

    def test_corrupt_file_is_unreadable_and_not_overwritten(self):
        self.store.save({"current": {"access_token": "a", "refresh_token": "b"}})
        with open(self.store.session_path, "wb") as fh:
            fh.write(b"garbage")
        with self.assertRaises(StoreUnreadable):
            self.store.load()
        with self.assertRaises(StoreUnreadable):
            self.store.save({"current": None, "pending": {"x": 1}})
        with open(self.store.session_path, "rb") as fh:
            self.assertEqual(fh.read(), b"garbage")

    def test_missing_key_is_unreadable_and_key_not_regenerated(self):
        self.store.save({"current": {"access_token": "a", "refresh_token": "b"}})
        os.remove(self.store.key_path)
        with self.assertRaises(StoreUnreadable):
            self.store.load()
        self.assertFalse(os.path.exists(self.store.key_path))

    def test_installation_id_persists(self):
        first = self.store.installation_id()
        self.assertRegex(first, r"^[A-Za-z0-9_-]{22,64}$")
        self.assertEqual(CommunitySessionStore(self.tmp).installation_id(), first)


class HelperTests(unittest.TestCase):
    def test_pkce_rfc7636_appendix_b(self):
        octets = bytes([116, 24, 223, 180, 151, 153, 224, 37, 79, 250, 96, 125, 216, 173, 187, 186,
                        22, 212, 37, 77, 105, 214, 191, 240, 91, 88, 5, 88, 83, 132, 141, 121])
        verifier = _b64(octets)
        self.assertEqual(verifier, "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")
        self.assertEqual(cac.pkce_challenge(verifier), "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")

    def test_random_secrets_shape(self):
        from services.community_auth_store import random_b64url

        values = {random_b64url() for _ in range(20)}
        self.assertEqual(len(values), 20)
        for v in values:
            self.assertRegex(v, r"^[A-Za-z0-9_-]{43}$")
            self.assertRegex(cac.pkce_challenge(v), r"^[A-Za-z0-9_-]{43}$")

    def test_device_label_rules_match_protocol(self):
        self.assertEqual(cac.normalize_device_label("  우리집   NAS  "), "우리집 NAS")
        for bad in ("", " ", "a" * 41, "<b>", 'x"y', "a`b", "back\\slash", "javascript:alert(1)",
                    "http://x", "a‮b", "a\u0007b", None, 3):
            self.assertIsNone(cac.normalize_device_label(bad), bad)
        self.assertEqual(cac.normalize_device_label("가" * 40), "가" * 40)

    def test_bootstrap_url_validation(self):
        rid = str(uuid.uuid4())
        ticket = _b64(os.urandom(32))
        self.assertEqual(cac.parse_bootstrap_url(f"{SITE_URL}#r={rid}&t={ticket}", SITE_URL), (rid, ticket))
        for bad in (f"https://evil.example/safeauth/#r={rid}&t={ticket}", f"{SITE_URL}?r={rid}&t={ticket}",
                    f"{SITE_URL}#r={rid}&t={ticket}&x=1", f"{SITE_URL}#r=nope&t={ticket}", "javascript:alert(1)"):
            self.assertIsNone(cac.parse_bootstrap_url(bad, SITE_URL), bad)

    def test_config_validation(self):
        anon = fake_jwt({"role": "anon", "iss": "supabase"})
        service = fake_jwt({"role": "service_role", "iss": "supabase"})
        self.assertEqual(cas.validate_publishable_key(anon), anon)
        self.assertEqual(cas.validate_publishable_key("sb_publishable_abcdefghijklmnop"), "sb_publishable_abcdefghijklmnop")
        for bad in ("sb_secret_abcdefghijklmnop", service, "random-string", ""):
            self.assertIsNone(cas.validate_publishable_key(bad), bad)
        self.assertEqual(cas.normalize_supabase_url("https://abc.supabase.co/"), "https://abc.supabase.co")
        self.assertEqual(cas.normalize_supabase_url("http://127.0.0.1:54400"), "http://127.0.0.1:54400")
        for bad in ("http://abc.supabase.co", "http://localhost:54321", "https://abc.supabase.co/rest",
                    "https://u:p@abc.supabase.co", "ftp://abc", "https://abc.supabase.co?x=1"):
            self.assertIsNone(cas.normalize_supabase_url(bad), bad)
        cfg = cas.build_config({"enabled": "true", "supabase_url": "https://abc.supabase.co",
                                "publishable_key": "sb_secret_abcdefghijklmnop"}, env={})
        self.assertFalse(cfg.configured)
        self.assertIn("publishable_key", cfg.problems)
        cfg = cas.build_config({"enabled": "false"}, env={cas.ENV_ENABLED: "true",
                                                         cas.ENV_SUPABASE_URL: "https://abc.supabase.co",
                                                         cas.ENV_PUBLISHABLE_KEY: "sb_publishable_abcdefghijklmnop"})
        self.assertTrue(cfg.enabled and cfg.configured)
        self.assertEqual(cfg.env_locked, frozenset({"enabled", "supabase_url", "publishable_key"}))
        self.assertEqual(cfg.site_url, cas.DEFAULT_SITE_URL)


# ── 서비스 흐름 (가짜 Supabase) ─────────────────────────────────────────────────

class ServiceFlowTests(CommunityTestBase):
    def test_start_poll_exchange_once_then_confirm(self):
        dto = self.service.start()
        self.assertEqual(dto["state"], "pending")
        self.assertEqual(dto["pending"]["display_code"], "ABCD-2345")
        self.assertTrue(dto["pending"]["bootstrap_url"].startswith(SITE_URL + "#r="))
        create = self.fake.calls_to("/requests")[0]
        self.assertEqual(create["body"]["client_kind"], "pc")
        self.assertEqual(create["body"]["device_label"], "테스트 PC")
        self.assertEqual(create["body"]["code_challenge_method"], "s256")
        self.assertNotIn("code_verifier", create["body"])
        self.assertEqual(create["headers"].get("apikey"), self.cfg.publishable_key)
        self.assertNotIn("origin", create["headers"])
        stored = self.state()["pending"]
        self.assertEqual(create["body"]["code_challenge"], cac.pkce_challenge(stored["code_verifier"]))
        rid = dto["pending"]["request_id"]
        self.fake.requests[rid]["phase"] = "claimed"
        self.assertTrue(wait_for(lambda: self.service.status(can_manage=True)["pending"]["phase"] == "claimed"))
        self.fake.give_code(rid, USER_A)
        self.assertTrue(wait_for(lambda: self.service.status(can_manage=True)["state"] == "confirm_required"))
        dto = self.service.status(can_manage=True)
        self.assertEqual(dto["candidate"]["display_name"], "로컬A")
        self.assertFalse(dto["candidate"]["is_different_account"])
        self.assertEqual(self.fake.count("/auth/v1/token"), 1)
        polls_after_code = self.fake.count("/poll")
        time.sleep(0.15)
        self.assertEqual(self.fake.count("/poll"), polls_after_code, "code 를 받은 뒤 poll 을 멈춘다")
        self.assert_no_secrets(dto)

        dto = self.service.confirm(rid)
        self.assertEqual(dto["state"], "connected")
        self.assertEqual(dto["account"]["display_name"], "로컬A")
        self.assertIsNone(dto["pending"])
        complete = self.fake.calls_to("/complete")
        self.assertEqual(len(complete), 1)
        current = self.state()["current"]
        self.assertEqual(complete[0]["headers"]["authorization"], f"Bearer {current['access_token']}")
        self.assertEqual(set(complete[0]["body"]), {"protocol", "request_id", "device_secret"})
        self.assertEqual(self.fake.requests[rid]["phase"], "device_confirmed")
        self.assertEqual(self.service.active_workers(), 0)
        self.assert_no_secrets(dto)

    def test_lost_poll_response_is_retried_with_same_delivery_key(self):
        dto = self.service.start()
        rid = dto["pending"]["request_id"]
        self.fake.give_code(rid, USER_A)
        self.fake.poll_script[rid] = ["drop", "429"]
        self.assertTrue(wait_for(lambda: self.service.status(can_manage=True)["state"] == "confirm_required", 8))
        keys = {c["body"]["delivery_key"] for c in self.fake.calls_to("/poll")}
        self.assertEqual(len(keys), 1)
        self.assertGreaterEqual(self.fake.count("/poll"), 3)
        self.assertEqual(self.fake.count("/auth/v1/token"), 1)

    def test_exchange_failure_does_not_reexchange(self):
        self.fake.exchange_fail = "bad_code_verifier"
        dto = self.service.start()
        rid = dto["pending"]["request_id"]
        self.fake.give_code(rid, USER_A)
        self.assertTrue(wait_for(lambda: self.service.status(can_manage=True)["last_error"] is not None))
        dto = self.service.status(can_manage=True)
        self.assertEqual(dto["state"], "disconnected")
        self.assertEqual(dto["last_error"]["code"], "exchange_failed")
        self.assertIsNone(self.state()["pending"])
        polls = self.fake.count("/poll")
        time.sleep(0.15)
        self.assertEqual(self.fake.count("/auth/v1/token"), 1)
        self.assertEqual(self.fake.count("/poll"), polls)
        self.assertEqual(self.fake.count("/cancel"), 1)

    def test_cancel_candidate_logs_it_out_and_keeps_current(self):
        self.connect(USER_A)
        before = self.state()["current"]
        rid = self.start_and_get_code(USER_B)
        dto = self.service.status(can_manage=True)
        self.assertTrue(dto["candidate"]["is_different_account"])
        self.assertEqual(dto["account"]["display_name"], "로컬A")
        candidate = self.state()["pending"]["candidate"]
        dto = self.service.cancel(rid)
        self.assertEqual(dto["state"], "connected")
        self.assertEqual(self.state()["current"], before)
        logout = self.fake.calls_to("/auth/v1/logout")
        self.assertEqual(len(logout), 1)
        self.assertEqual(logout[0]["query"], "scope=local")
        self.assertEqual(logout[0]["headers"]["authorization"], f"Bearer {candidate['access_token']}")
        self.assertEqual(self.fake.calls_to("/cancel")[-1]["body"]["actor"], "device")

    def test_confirm_different_account_replaces_and_logs_out_old_session_locally(self):
        self.connect(USER_A)
        old = self.state()["current"]
        rid = self.start_and_get_code(USER_B)
        dto = self.service.confirm(rid)
        self.assertEqual(dto["account"]["display_name"], "로컬B")
        self.assertEqual(self.state()["current"]["user_id"], USER_B["id"])
        logout = self.fake.calls_to("/auth/v1/logout")
        self.assertEqual(len(logout), 1)
        self.assertEqual(logout[0]["query"], "scope=local")
        self.assertEqual(logout[0]["headers"]["authorization"], f"Bearer {old['access_token']}")

    def test_confirm_checks_request_and_state(self):
        with self.assertRaises(cas.CommunityAuthError) as ctx:
            self.service.confirm(str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "no_pending")
        dto = self.service.start()
        with self.assertRaises(cas.CommunityAuthError) as ctx:
            self.service.confirm(dto["pending"]["request_id"])
        self.assertEqual(ctx.exception.code, "invalid_state")
        with self.assertRaises(cas.CommunityAuthError) as ctx:
            self.service.confirm(str(uuid.uuid4()))
        self.assertEqual(ctx.exception.code, "request_mismatch")

    def test_complete_retries_transient_then_succeeds(self):
        self.fake.complete_fail = [503, 502]
        dto = self.connect(USER_A)
        self.assertEqual(dto["state"], "connected")
        self.assertIsNone(dto["last_error"])
        self.assertEqual(self.fake.count("/complete"), 3)
        self.assertEqual(self.fake.count("/auth/v1/token"), 1, "complete 재시도가 코드를 다시 교환하지 않는다")

    def test_complete_failure_keeps_local_connection_and_reports(self):
        self.fake.complete_fail = [401]
        dto = self.connect(USER_A)
        self.assertEqual(dto["state"], "connected")
        self.assertEqual(dto["last_error"]["code"], "complete_failed")

    def test_disconnect_logs_out_with_scope_local_and_deletes(self):
        self.connect(USER_A)
        current = self.state()["current"]
        result = self.service.disconnect()
        self.assertTrue(result["server_logout"])
        logout = self.fake.calls_to("/auth/v1/logout")[-1]
        self.assertEqual(logout["query"], "scope=local")
        self.assertEqual(logout["headers"]["authorization"], f"Bearer {current['access_token']}")
        self.assertIsNone(self.state()["current"])
        self.assertFalse(os.path.exists(self.service.store.session_path))
        self.assertEqual(self.service.status(can_manage=True)["state"], "disconnected")

    def test_disconnect_with_expired_access_refreshes_first(self):
        self.connect(USER_A)
        st = self.state()
        st["current"]["expires_at"] = time.time() - 10
        self.service.store.save(st)
        result = self.service.disconnect()
        self.assertTrue(result["server_logout"])
        self.assertEqual(self.fake.count("/auth/v1/token"), 2)  # pkce + refresh
        self.assertEqual(self.fake.calls_to("/auth/v1/logout")[-1]["query"], "scope=local")

    def test_disconnect_when_server_unreachable_still_deletes_locally(self):
        self.connect(USER_A)
        self.fake.close()
        result = self.service.disconnect()
        self.assertFalse(result["server_logout"])
        self.assertIsNone(self.state()["current"])

    def test_refresh_rotation_saved_atomically(self):
        self.connect(USER_A)
        self.assertEqual(self.service.get_access_token(), self.state()["current"]["access_token"])
        self.assertEqual(self.fake.count("/auth/v1/token"), 1, "만료 전에는 refresh 하지 않는다")
        st = self.state()
        old = dict(st["current"])
        st["current"]["expires_at"] = time.time() + 30  # 60초 안 → refresh
        self.service.store.save(st)
        token = self.service.get_access_token()
        new = self.state()["current"]
        self.assertEqual(token, new["access_token"])
        self.assertNotEqual(new["access_token"], old["access_token"])
        self.assertNotEqual(new["refresh_token"], old["refresh_token"])
        sid = new["session_id"]
        self.assertEqual(self.fake.sessions[sid]["refresh"], new["refresh_token"])
        self.assertGreater(new["expires_at"], time.time() + 3000)

    def test_concurrent_refresh_uses_network_once(self):
        self.connect(USER_A)
        st = self.state()
        st["current"]["expires_at"] = time.time() - 1
        self.service.store.save(st)
        self.fake.refresh_delay = 0.3
        other = self.make_service(self.tmp)  # 같은 데이터 폴더의 두 번째 인스턴스(다른 락 객체) → 파일 락으로 직렬화
        results, errors = [], []

        def worker(svc):
            try:
                results.append(svc.get_access_token())
            except Exception as exc:  # pragma: no cover - 실패 시 보고용
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(self.service if i % 2 else other,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(errors, [])
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(self.fake.count("/auth/v1/token") - 1, 1, "refresh 는 한 번만")

    def test_invalid_refresh_becomes_reauth_required(self):
        self.connect(USER_A)
        st = self.state()
        st["current"]["expires_at"] = time.time() - 1
        self.service.store.save(st)
        self.fake.refresh_mode = "invalid"
        with self.assertRaises(cas.CommunityAuthError) as ctx:
            self.service.get_access_token()
        self.assertEqual(ctx.exception.code, "reauth_required")
        st = self.state()
        self.assertIsNone(st["current"])
        self.assertNotIn("refresh_token", json.dumps(st["reauth"]))
        dto = self.service.status(can_manage=True)
        self.assertEqual(dto["state"], "reauth_required")
        self.assertEqual(dto["account"]["session_state"], "reauth_required")
        self.assertEqual(dto["account"]["display_name"], "로컬A")
        self.assertFalse(self.service.is_upload_allowed())

    def test_transient_refresh_error_keeps_tokens(self):
        self.connect(USER_A)
        st = self.state()
        st["current"]["expires_at"] = time.time() - 1
        self.service.store.save(st)
        before = self.state()["current"]
        self.fake.refresh_mode = "5xx"
        with self.assertRaises(cas.CommunityAuthError) as ctx:
            self.service.get_access_token()
        self.assertEqual(ctx.exception.code, "auth_unavailable")
        self.assertEqual(self.state()["current"], before)
        self.assertEqual(self.service.status(can_manage=True)["state"], "connected")

    def test_new_start_cancels_previous_pending_at_relay(self):
        first = self.service.start()["pending"]["request_id"]
        second = self.service.start()["pending"]["request_id"]
        self.assertNotEqual(first, second)
        self.assertEqual(self.fake.requests[first]["phase"], "cancelled")
        self.assertTrue(wait_for(lambda: self.service.active_workers() == 1))

    def test_pending_expiry_clears_state(self):
        dto = self.service.start()
        st = self.state()
        st["pending"]["expires_ts"] = time.time() - 1
        self.service.store.save(st)
        dto = self.service.status(can_manage=True)
        self.assertEqual(dto["state"], "disconnected")
        self.assertEqual(dto["last_error"]["code"], "expired")

    def test_resume_does_not_reexchange_interrupted_exchange(self):
        self.service.store.save({"pending": {
            "request_id": str(uuid.uuid4()), "code_verifier": "v" * 43, "device_secret": "d" * 43,
            "delivery_key": "k" * 43, "phase": "exchanging", "code_consumed": True,
            "expires_ts": time.time() + 300, "display_code": "ABCD-2345"}})
        self.service.resume()
        self.assertEqual(self.service.status(can_manage=True)["last_error"]["code"], "interrupted")
        self.assertEqual(self.fake.count("/auth/v1/token"), 0)
        self.assertEqual(self.service.active_workers(), 0)

    def test_resume_restarts_poll_for_live_pending(self):
        dto = self.service.start()
        rid = dto["pending"]["request_id"]
        self.service.shutdown(1.0)
        restarted = self.make_service(self.tmp)
        restarted.resume()
        self.assertEqual(restarted.active_workers(), 1)
        self.fake.give_code(rid, USER_A)
        self.assertTrue(wait_for(lambda: restarted.status(can_manage=True)["state"] == "confirm_required"))

    def test_shutdown_stops_workers(self):
        self.service.start()
        self.assertEqual(self.service.active_workers(), 1)
        self.service.shutdown(2.0)
        self.assertEqual(self.service.active_workers(), 0)

    def test_status_hides_bootstrap_url_without_manage(self):
        self.service.start()
        dto = self.service.status(can_manage=False)
        self.assertNotIn("bootstrap_url", dto["pending"])
        self.assertNotIn(SITE_URL, json.dumps(dto))

    def test_upload_gate(self):
        self.assertFalse(self.service.is_upload_allowed())
        self.connect(USER_A)
        self.assertFalse(self.service.is_upload_allowed(), "upload_enabled 기본값은 꺼짐")
        self.cfg = cas.CommunityConfig(**{**self.cfg.__dict__, "upload_enabled": True})
        self.assertTrue(self.service.is_upload_allowed())

    def test_unreadable_store_surfaces_state_and_disconnect_quarantines(self):
        self.connect(USER_A)
        os.remove(self.service.store.key_path)
        dto = self.service.status(can_manage=True)
        self.assertEqual(dto["state"], "store_unreadable")
        with self.assertRaises(cas.CommunityAuthError) as ctx:
            self.service.start()
        self.assertEqual(ctx.exception.code, "store_unreadable")
        result = self.service.disconnect()
        self.assertTrue(result["reset_unreadable"])
        self.assertTrue(any(n.startswith("community_session.enc.unreadable-") for n in os.listdir(self.service.store.auth_dir)))
        self.assertEqual(self.service.status(can_manage=True)["state"], "disconnected")

    def test_disabled_and_unconfigured(self):
        self.cfg = cas.CommunityConfig(enabled=False, supabase_url=self.fake.url,
                                       publishable_key="sb_publishable_testkey1234567890", site_url=SITE_URL)
        self.assertEqual(self.service.status(can_manage=True)["state"], "disabled")
        with self.assertRaises(cas.CommunityAuthError) as ctx:
            self.service.start()
        self.assertEqual((ctx.exception.code, ctx.exception.status), ("community_disabled", 503))
        self.cfg = cas.CommunityConfig(enabled=True, site_url=SITE_URL)
        self.assertEqual(self.service.status(can_manage=True)["state"], "unconfigured")
        with self.assertRaises(cas.CommunityAuthError) as ctx:
            self.service.start()
        self.assertEqual(ctx.exception.code, "community_unconfigured")
        self.assertEqual(self.fake.calls, [])

    def test_fixture_mode_blocks_non_loopback(self):
        self.cfg = cas.CommunityConfig(enabled=True, supabase_url="https://abc.supabase.co",
                                       publishable_key="sb_publishable_testkey1234567890")
        with mock.patch.dict(os.environ, {"SAFETYREPORT_FIXTURE_MODE": "1"}), \
             mock.patch("requests.Session.post") as post:
            with self.assertRaises(cas.CommunityAuthError) as ctx:
                self.service.start()
        self.assertEqual(ctx.exception.code, "fixture_blocked")
        post.assert_not_called()

    def test_fixture_mode_allows_loopback(self):
        with mock.patch.dict(os.environ, {"SAFETYREPORT_FIXTURE_MODE": "1"}):
            self.assertEqual(self.service.start()["state"], "pending")

    def test_invalid_relay_bootstrap_url_is_rejected(self):
        self.cfg = cas.CommunityConfig(**{**self.cfg.__dict__, "site_url": "https://worklazy.net/safeauth/"})
        with self.assertRaises(cas.CommunityAuthError) as ctx:
            self.service.start()
        self.assertEqual(ctx.exception.code, "relay_rejected")
        self.assertIsNone(self.state()["pending"])

    def test_logs_do_not_contain_secrets(self):
        records = []

        class Grab(logging.Handler):
            def emit(self, record):
                records.append(self.format(record))

        handler = Grab(level=logging.DEBUG)
        for name in ("safetyreport.core", "safetyreport.core.community_auth", "urllib3", ""):
            logging.getLogger(name).addHandler(handler)
            self.addCleanup(logging.getLogger(name).removeHandler, handler)
        log = logging.getLogger("safetyreport.core.community_auth")
        old_level = log.level
        log.setLevel(logging.DEBUG)
        self.addCleanup(log.setLevel, old_level)
        self.fake.complete_fail = [503]
        self.connect(USER_A)
        rid = self.start_and_get_code(USER_B)
        self.service.cancel(rid)
        st = self.state()
        st["current"]["expires_at"] = time.time() - 1
        self.service.store.save(st)
        self.service.get_access_token()
        secrets_seen = self.secret_values()
        self.service.disconnect()
        joined = "\n".join(records)
        self.assertTrue(records, "로그가 실제로 캡처되어야 한다")
        for secret in secrets_seen:
            self.assertNotIn(secret, joined)
        self.assertNotIn(SITE_URL + "#", joined)


class TwoDevicesTests(CommunityTestBase):
    def test_two_installations_do_not_cross_sessions(self):
        other_dir = tempfile.mkdtemp(prefix="community-auth-b-")
        self.addCleanup(shutil.rmtree, other_dir, True)
        other = self.make_service(other_dir)
        a = self.service.start()["pending"]["request_id"]
        b = other.start()["pending"]["request_id"]
        self.fake.give_code(b, USER_B)
        self.fake.give_code(a, USER_A)
        self.assertTrue(wait_for(lambda: self.service.status(can_manage=True)["state"] == "confirm_required"))
        self.assertTrue(wait_for(lambda: other.status(can_manage=True)["state"] == "confirm_required"))
        self.service.confirm(a)
        other.confirm(b)
        self.assertEqual(self.service.store.load()["current"]["user_id"], USER_A["id"])
        self.assertEqual(other.store.load()["current"]["user_id"], USER_B["id"])
        self.assertNotEqual(self.service.store.installation_id(), other.store.installation_id())


# ── 로컬 API (FastAPI 앱 전체: 세션·인증 미들웨어 포함) ─────────────────────────────

class LocalApiTests(CommunityTestBase):
    @classmethod
    def setUpClass(cls):
        import warnings

        warnings.simplefilter("ignore", DeprecationWarning)
        import main  # noqa: F401 - 앱과 미들웨어 구성
        from core.database import database
        from core.database.engine import get_engine
        from fastapi.testclient import TestClient

        cls.main = main
        cls.TestClient = TestClient
        engine = get_engine()
        database.upgrade_schema(engine, maintenance=False)
        if not database.has_admin_user(engine):
            database.create_admin_user(engine, "fixture-admin", "fixture-pass")
        cls.manager_key = database.create_api_key(engine, "관리 허용 폰")
        cls.plain_key = database.create_api_key(engine, "일반 폰")

    @classmethod
    def tearDownClass(cls):
        from core.database import database
        from core.database.engine import get_engine

        for key in (cls.manager_key, cls.plain_key):
            database.delete_api_key(get_engine(), key)

    def setUp(self):
        super().setUp()
        self.cfg = cas.CommunityConfig(**{**self.cfg.__dict__,
                                          "api_key_managers": frozenset({cas.hash_api_key(self.manager_key)})})
        patcher = mock.patch.object(cas, "_default", self.service)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = self.TestClient(self.main.app, base_url="http://testserver")
        self.addCleanup(self.client.close)

    def login(self):
        r = self.client.post("/login", data={"username": "fixture-admin", "password": "fixture-pass"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        page = self.client.get("/settings/")
        self.assertEqual(page.status_code, 200)
        marker = 'data-csrf="'
        start = page.text.index(marker) + len(marker)
        return page, page.text[start:page.text.index('"', start)]

    def post(self, path, body=None, token=None, headers=None):
        h = {"Accept": "application/json", "Content-Type": "application/json"}
        if token:
            h["X-CSRF-Token"] = token
        h.update(headers or {})
        return self.client.post(path, content=json.dumps(body or {}), headers=h)

    def test_unauthenticated_web_is_rejected(self):
        r = self.client.get("/settings/community/status", headers={"Accept": "application/json"})
        self.assertEqual(r.status_code, 401)
        r = self.post("/settings/community/start", token="x")
        self.assertEqual(r.status_code, 401)
        r = self.client.post("/settings/community/start", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.fake.count("/requests"), 0)

    def test_web_csrf_origin_and_content_type(self):
        _, token = self.login()
        self.assertEqual(self.post("/settings/community/start").status_code, 403)
        self.assertEqual(self.post("/settings/community/start", token="wrong-token-value").status_code, 403)
        r = self.post("/settings/community/start", token=token, headers={"Origin": "http://evil.example"})
        self.assertEqual(r.status_code, 403)
        r = self.post("/settings/community/start", token=token, headers={"Origin": "null"})
        self.assertEqual(r.status_code, 403)
        r = self.post("/settings/community/start", token=token, headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/settings/community/start", data={"a": "b"},
                             headers={"Accept": "application/json", "X-CSRF-Token": token})
        self.assertEqual(r.status_code, 403)
        for path in ("confirm", "cancel", "disconnect", "settings"):
            self.assertEqual(self.post(f"/settings/community/{path}").status_code, 403, path)
        self.assertEqual(self.fake.count("/requests"), 0)
        r = self.post("/settings/community/start", token=token, headers={"Origin": "http://testserver"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["state"], "pending")

    def test_web_full_flow_and_no_secrets(self):
        page, token = self.login()
        r = self.post("/settings/community/start", {"device_label": "우리집 NAS"}, token=token)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.fake.calls_to("/requests")[0]["body"]["device_label"], "우리집 NAS")
        rid = r.json()["data"]["pending"]["request_id"]
        self.assertEqual(self.post("/settings/community/start", {"device_label": "<x>"}, token=token).status_code, 400)
        rid = self.post("/settings/community/start", {}, token=token).json()["data"]["pending"]["request_id"]
        self.fake.give_code(rid, USER_B)
        self.assertTrue(wait_for(lambda: self.client.get(
            "/settings/community/status", headers={"Accept": "application/json"}).json()["data"]["state"] == "confirm_required"))
        status = self.client.get("/settings/community/status", headers={"Accept": "application/json"})
        self.assertEqual(status.headers.get("cache-control"), "no-store")
        self.assert_no_secrets(status.json()["data"])
        self.assertNotIn(self.manager_key, status.text)
        r = self.post("/settings/community/confirm", {"request_id": rid}, token=token)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["state"], "connected")
        self.assert_no_secrets(r.json())
        settings_html = self.client.get("/settings/").text
        for secret in self.secret_values():
            self.assertNotIn(secret, settings_html)
        self.assertNotIn(self.manager_key, settings_html)
        r = self.post("/settings/community/disconnect", {}, token=token)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["result"]["server_logout"])
        self.assertEqual(self.fake.calls_to("/auth/v1/logout")[-1]["query"], "scope=local")

    def test_web_errors_use_codes(self):
        _, token = self.login()
        r = self.post("/settings/community/confirm", {"request_id": str(uuid.uuid4())}, token=token)
        self.assertEqual((r.status_code, r.json()["code"]), (409, "no_pending"))
        r = self.post("/settings/community/cancel", {}, token=token)
        self.assertEqual((r.status_code, r.json()["code"]), (409, "no_pending"))
        r = self.post("/settings/community/start", {"unknown": 1}, token=token)
        self.assertEqual(r.status_code, 400)

    def test_web_settings_save(self):
        _, token = self.login()
        import settings.settings as app_settings

        self.addCleanup(self._clear_community_section)
        plain_hash = cas.hash_api_key(self.plain_key)
        r = self.post("/settings/community/settings", {"api_key_managers": [plain_hash]}, token=token)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(app_settings._instance.config.get("COMMUNITY", "api_key_managers"), plain_hash)
        keys = {k["name"]: k for k in r.json()["config"]["api_keys"]}
        self.assertTrue(keys["일반 폰"]["allowed"])
        self.assertNotIn(self.plain_key, r.text)
        r = self.post("/settings/community/settings", {"api_key_managers": ["0" * 64]}, token=token)
        self.assertEqual(r.status_code, 400)
        r = self.post("/settings/community/settings", {"publishable_key": "sb_secret_abcdefghijklmnop"}, token=token)
        self.assertEqual(r.status_code, 400)
        r = self.post("/settings/community/settings", {"supabase_url": "http://abc.supabase.co"}, token=token)
        self.assertEqual(r.status_code, 400)
        r = self.post("/settings/community/settings", {"enabled": True, "supabase_url": "https://abc.supabase.co",
                                                        "publishable_key": "sb_publishable_abcdefghijklmnop",
                                                        "device_label": "거실 PC"}, token=token)
        self.assertEqual(r.status_code, 200, r.text)
        cfg = cas.load_config_from_settings()
        self.assertTrue(cfg.enabled and cfg.configured)
        self.assertEqual(cfg.device_label, "거실 PC")

    def _clear_community_section(self):
        import settings.settings as app_settings

        app_settings._instance.config.remove_section("COMMUNITY")
        app_settings._instance.save()

    def api(self, method, path, key, body=None):
        headers = {"X-API-Key": key, "Accept": "application/json"}
        if method == "GET":
            return self.client.get(f"/api/v1/community-auth/{path}", headers=headers)
        headers["Content-Type"] = "application/json"
        return self.client.post(f"/api/v1/community-auth/{path}", content=json.dumps(body or {}), headers=headers)

    def test_mobile_permission(self):
        self.assertEqual(self.api("GET", "status", "sk-invalid").status_code, 401)
        self.service.start()
        r = self.api("GET", "status", self.plain_key)
        self.assertEqual(r.status_code, 200)
        data = r.json()["data"]
        self.assertFalse(data["can_manage"])
        self.assertNotIn("bootstrap_url", data["pending"])
        for path, body in (("start", {}), ("confirm", {"request_id": data["pending"]["request_id"]}),
                           ("cancel", {}), ("disconnect", {})):
            r = self.api("POST", path, self.plain_key, body)
            self.assertEqual(r.status_code, 403, path)
            self.assertEqual(r.json()["code"], "permission_required")
            self.assertEqual(r.json()["detail"], cas.MESSAGES["permission_required"])
        self.assertEqual(self.fake.count("/requests"), 1)
        self.assertEqual(self.fake.count("/cancel"), 0)

    def test_mobile_manager_flow(self):
        r = self.api("GET", "status", self.manager_key)
        self.assertTrue(r.json()["data"]["can_manage"])
        r = self.api("POST", "start", self.manager_key, {"device_label": "폰에서 시작"})
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()["data"]
        self.assertTrue(data["pending"]["bootstrap_url"].startswith(SITE_URL))
        self.assertEqual(self.fake.calls_to("/requests")[-1]["body"]["client_kind"], "mobile_client_server")
        self.fake.give_code(data["pending"]["request_id"], USER_A)
        self.assertTrue(wait_for(lambda: self.api("GET", "status", self.manager_key).json()["data"]["state"] == "confirm_required"))
        r = self.api("POST", "confirm", self.manager_key, {"request_id": data["pending"]["request_id"]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["data"]["state"], "connected")
        self.assert_no_secrets(r.json())
        r = self.api("POST", "confirm", self.manager_key, {"request_id": data["pending"]["request_id"]})
        self.assertEqual((r.status_code, r.json()["code"]), (409, "no_pending"))
        r = self.api("POST", "disconnect", self.manager_key)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["data"]["state"], "disconnected")

    def test_mobile_errors(self):
        self.cfg = cas.CommunityConfig(**{**self.cfg.__dict__, "enabled": False})
        r = self.api("POST", "start", self.manager_key)
        self.assertEqual((r.status_code, r.json()["code"]), (503, "community_disabled"))
        self.cfg = cas.CommunityConfig(**{**self.cfg.__dict__, "enabled": True})
        self.fake.close()
        r = self.api("POST", "start", self.manager_key)
        self.assertEqual((r.status_code, r.json()["code"]), (502, "relay_unavailable"))

    def test_app_config_capability_and_no_secrets(self):
        self.connect(USER_A)
        r = self.client.get("/api/v1/app/config", headers={"X-API-Key": self.plain_key})
        self.assertEqual(r.status_code, 200)
        self.assertIn("community_account", r.json()["data"]["capabilities"])
        self.assertIn("rating_cause", r.json()["data"]["capabilities"])
        for secret in self.secret_values():
            self.assertNotIn(secret, r.text)

    def test_backup_export_has_no_secrets(self):
        from services import db_backup

        self.connect(USER_A)
        secrets_ = self.secret_values()
        path = db_backup.export_clean_db()
        self.addCleanup(os.remove, path)
        with open(path, "rb") as fh:
            blob = fh.read()
        for secret in secrets_:
            self.assertNotIn(secret.encode(), blob)
        self.assertNotIn(b"community_session", blob)

    def test_file_api_cannot_reach_auth_dir(self):
        from services import file_service

        self.assertNotIn("auth", file_service.ALLOWED_API_ROOTS)
        r = self.client.get("/api/v1/files", params={"path": "auth"}, headers={"X-API-Key": self.plain_key})
        self.assertNotEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
