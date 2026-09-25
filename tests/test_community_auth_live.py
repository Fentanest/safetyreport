"""커뮤니티 계정 연결 — 로컬 Supabase 검증 스택 대상 end-to-end (선택 실행).

실제 GoTrue + 실제 중계(community-auth-relay) + 가짜 카카오(OAuth 흉내)로 이루어진 loopback 스택에서,
이 서버의 서비스 코드(start → poll → 한 번 교환 → confirm → complete → refresh → disconnect)를 그대로 돌린다.
브라우저(중앙 페이지) 역할은 이 테스트가 HTTP 로 흉내 낸다. 실제 카카오·호스팅 Supabase 검증이 아니다.

실행(스택이 떠 있을 때만):
  SAFEAUTH_STACK_URL=http://127.0.0.1:54400 \
  SAFEAUTH_STACK_ENV=<safetyreport-community-map>/.safeauth-stack/stack.env \
  SAFETYREPORT_DATA_DIR=$(mktemp -d) .venv/bin/python -m unittest tests.test_community_auth_live
공개(anon) 키는 SAFEAUTH_ANON_KEY 또는 SAFEAUTH_STACK_ENV 파일의 SAFEAUTH_ANON_KEY 를 실행 중에만 읽는다(저장소·출력에 남기지 않음).
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
import threading
import time
import unittest
from urllib.parse import parse_qs, urlsplit

import requests

from services import community_auth_client as cac
from services import community_auth_service as cas
from services.community_auth_store import random_b64url

STACK = os.environ.get("SAFEAUTH_STACK_URL", "").rstrip("/")
SITE_URL = os.environ.get("SAFEAUTH_SITE_URL", "http://127.0.0.1:8480/safeauth/")
KAKAO_URL = os.environ.get("SAFEAUTH_KAKAO_URL", "http://127.0.0.1:54410").rstrip("/")


def _anon_key() -> str | None:
    if os.environ.get("SAFEAUTH_ANON_KEY"):
        return os.environ["SAFEAUTH_ANON_KEY"].strip()
    path = os.environ.get("SAFEAUTH_STACK_ENV")
    if not path or not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^SAFEAUTH_ANON_KEY=(.*)$", line.strip())
            if m:
                return m.group(1)
    return None


def _wait(predicate, timeout, interval=0.25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class Browser:
    """중앙 페이지(worklazy.net/safeauth) 역할. 원래 기기의 비밀값은 모른다."""

    def __init__(self, anon: str):
        self.anon = anon
        self.origin = f"{urlsplit(SITE_URL).scheme}://{urlsplit(SITE_URL).netloc}"
        self.secret = random_b64url()
        self.http = requests.Session()
        self.http.trust_env = False

    def relay(self, action: str, body: dict):
        r = self.http.post(f"{STACK}/functions/v1/community-auth-relay/{action}", json={"protocol": 1, **body},
                           headers={"Origin": self.origin, "apikey": self.anon}, timeout=10, allow_redirects=False)
        return r.status_code, r.json()

    def run_to_publish(self, bootstrap_url: str, choice: str) -> dict:
        parsed = cac.parse_bootstrap_url(bootstrap_url, SITE_URL)
        assert parsed, "bootstrap_url 형식"
        self.request_id, ticket = parsed
        status, claimed = self.relay("claim", {"request_id": self.request_id, "ticket": ticket, "browser_secret": self.secret})
        assert status == 200, (status, claimed.get("error", {}).get("code"))
        status, prepared = self.relay("prepare", {"request_id": self.request_id, "browser_secret": self.secret,
                                                  "confirmed_started_by_me": True})
        assert status == 200, (status, prepared.get("error", {}).get("code"))
        step1 = self.http.get(prepared["authorize_url"], allow_redirects=False, timeout=10)
        kakao = urlsplit(step1.headers["Location"])
        assert f"{kakao.scheme}://{kakao.netloc}" == KAKAO_URL, "mock Kakao 로 가야 한다"
        state = parse_qs(kakao.query)["state"][0]
        step2 = self.http.get(f"{KAKAO_URL}/oauth/decide", params={"state": state, "choice": choice},
                              allow_redirects=False, timeout=10)
        step3 = self.http.get(step2.headers["Location"], allow_redirects=False, timeout=10)
        landed = urlsplit(step3.headers["Location"])
        assert landed.path.endswith("/safeauth/callback.html"), landed.path
        codes = parse_qs(landed.query).get("code")
        assert codes and len(codes) == 1, "callback 에 code 1개"
        status, published = self.relay("publish", {"request_id": self.request_id, "browser_secret": self.secret,
                                                    "outcome": "code", "code": codes[0]})
        assert status == 200, (status, published.get("error", {}).get("code"))
        return claimed

    def phase(self) -> str:
        status, body = self.relay("browser-status", {"request_id": self.request_id, "browser_secret": self.secret})
        assert status == 200, (status, body.get("error", {}).get("code"))
        return body["phase"]


@unittest.skipUnless(STACK, "SAFEAUTH_STACK_URL 이 없어 로컬 Supabase 스택 테스트를 건너뜀")
class LiveStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.anon = _anon_key()
        if not cls.anon:
            raise unittest.SkipTest("SAFEAUTH_ANON_KEY / SAFEAUTH_STACK_ENV 가 없어 건너뜀")
        try:
            ok = requests.get(f"{STACK}/auth/v1/health", headers={"apikey": cls.anon}, timeout=5).status_code == 200
        except requests.RequestException:
            ok = False
        if not ok:
            raise unittest.SkipTest(f"스택에 연결할 수 없음: {STACK}")
        cls.http = requests.Session()
        cls.http.trust_env = False

    def make_service(self, label: str) -> cas.CommunityAuthService:
        datapath = tempfile.mkdtemp(prefix="community-live-")
        self.addCleanup(shutil.rmtree, datapath, True)
        cfg = cas.CommunityConfig(enabled=True, supabase_url=STACK, publishable_key=self.anon,
                                  site_url=SITE_URL, device_label=label)
        self.assertTrue(cfg.configured, cfg.problems)
        # 중계의 요청별 poll 한도(30/분) 안에서 빠르게: 2.5초 간격
        service = cas.CommunityAuthService(datapath, lambda: cfg, dockerenv_path=os.path.join(datapath, "no"),
                                           poll_interval_override=2.5, retry_base_seconds=0.5)
        self.addCleanup(service.shutdown, 3.0)
        return service

    def auth_user(self, access: str):
        return self.http.get(f"{STACK}/auth/v1/user", headers={"apikey": self.anon, "Authorization": f"Bearer {access}"},
                             timeout=10)

    def refresh_status(self, refresh: str) -> int:
        return self.http.post(f"{STACK}/auth/v1/token", params={"grant_type": "refresh_token"},
                              json={"refresh_token": refresh}, headers={"apikey": self.anon}, timeout=10).status_code

    def link(self, service, choice: str) -> tuple[Browser, dict]:
        dto = service.start()
        self.assertEqual(dto["state"], "pending")
        browser = Browser(self.anon)
        claimed = browser.run_to_publish(dto["pending"]["bootstrap_url"], choice)
        self.assertEqual(claimed["display_code"], dto["pending"]["display_code"], "A05 비교코드 일치")
        self.assertTrue(_wait(lambda: service.status(can_manage=True)["state"] == "confirm_required", 30),
                        service.status(can_manage=True))
        return browser, service.status(can_manage=True)

    def test_full_device_link_refresh_and_disconnect(self):
        service = self.make_service("라이브 PC")
        browser, dto = self.link(service, "A")
        self.assertEqual(browser.phase(), "code_delivered", "확정 전에는 중앙에 성공을 보이지 않는다")
        self.assertTrue(dto["candidate"]["display_name"].startswith("로컬테스트"), dto["candidate"])
        dto = service.confirm(dto["candidate"]["request_id"])
        self.assertEqual(dto["state"], "connected")
        self.assertIsNone(dto["last_error"])
        self.assertEqual(browser.phase(), "device_confirmed")

        stored = service.store.load()["current"]
        token = service.get_access_token()
        self.assertEqual(token, stored["access_token"])
        user = self.auth_user(token)
        self.assertEqual(user.status_code, 200)
        self.assertEqual(user.json()["id"], stored["user_id"])

        st = service.store.load()
        st["current"]["expires_at"] = time.time() - 5
        service.store.save(st)
        rotated_access = service.get_access_token()
        rotated = service.store.load()["current"]
        self.assertNotEqual(rotated["refresh_token"], stored["refresh_token"], "refresh 토큰 회전 저장")
        self.assertEqual(rotated["access_token"], rotated_access)
        self.assertEqual(self.auth_user(rotated_access).status_code, 200)

        result = service.disconnect()
        self.assertTrue(result["server_logout"])
        self.assertIsNone(service.store.load()["current"])
        self.assertEqual(self.refresh_status(rotated["refresh_token"]), 400, "scope=local 로그아웃 뒤 refresh 불가")
        self.user_after_logout = self.auth_user(rotated_access).status_code
        self.assertNotEqual(self.user_after_logout, 200, "로그아웃한 세션의 access token 은 거부")

    def test_two_devices_concurrently_do_not_cross(self):
        a = self.make_service("라이브 PC A")
        b = self.make_service("라이브 Docker B")
        results = {}

        def run(name, service, choice):
            try:
                results[name] = self.link(service, choice)
            except BaseException as exc:  # 스레드 실패를 본 스레드로 전달
                results[name] = exc

        threads = [threading.Thread(target=run, args=("a", a, "A")), threading.Thread(target=run, args=("b", b, "B"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        for name in ("a", "b"):
            self.assertNotIsInstance(results.get(name), BaseException, results.get(name))
        a.confirm(results["a"][1]["candidate"]["request_id"])
        b.confirm(results["b"][1]["candidate"]["request_id"])
        self.assertEqual(results["a"][0].phase(), "device_confirmed")
        self.assertEqual(results["b"][0].phase(), "device_confirmed")
        cur_a, cur_b = a.store.load()["current"], b.store.load()["current"]
        self.assertNotEqual(cur_a["user_id"], cur_b["user_id"])
        self.assertEqual(self.auth_user(a.get_access_token()).json()["id"], cur_a["user_id"])
        self.assertEqual(self.auth_user(b.get_access_token()).json()["id"], cur_b["user_id"])
        self.assertTrue(a.disconnect()["server_logout"])
        # A 의 로그아웃(scope=local)이 B 의 세션을 끝내지 않는다
        self.assertEqual(self.auth_user(b.get_access_token()).status_code, 200)
        self.assertTrue(b.disconnect()["server_logout"])

    def test_reject_candidate_keeps_existing_and_revokes_candidate(self):
        service = self.make_service("라이브 교체 테스트")
        _, dto = self.link(service, "A")
        service.confirm(dto["candidate"]["request_id"])
        existing = service.store.load()["current"]
        browser, dto = self.link(service, "B")
        self.assertTrue(dto["candidate"]["is_different_account"], "A06 다른 계정 경고")
        candidate = service.store.load()["pending"]["candidate"]
        service.cancel(dto["candidate"]["request_id"])
        self.assertEqual(browser.phase(), "cancelled")
        self.assertEqual(service.store.load()["current"], existing, "취소가 기존 연결을 덮어쓰지 않는다")
        self.assertEqual(self.refresh_status(candidate["refresh_token"]), 400, "버린 후보 세션은 logout 됨")
        self.assertEqual(self.auth_user(service.get_access_token()).status_code, 200)
        self.assertTrue(service.disconnect()["server_logout"])


if __name__ == "__main__":
    unittest.main()
