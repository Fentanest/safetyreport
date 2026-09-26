"""개발·QA 전용: 로컬 통합 스택(127.0.0.1 Supabase + 가짜 카카오)에서 카카오 세션을 받아 fixture 데이터 폴더에 넣는다.

화면 검수에서 '카카오 인증됨·동의 전', '동의 완료' 상태를 재현하려는 도구다. 중앙 연결 페이지(safeauth)를 띄우지 않아도 된다.
- loopback(127.0.0.1) Supabase 만 허용. 운영 data/ 를 거부한다(fixture_server 와 같은 규칙).
- 토큰은 fixture 폴더의 암호화 저장소에만 쓰고 출력하지 않는다.

  .venv/bin/python scripts/dev/community_qa_session.py --data-dir .agent-runs/fixture/qa \\
      --supabase-url http://127.0.0.1:56321 --publishable-key <local publishable> [--choice A] [--consent] [--official-id qa-official]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parents[2]


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def kakao_session(api: str, publishable: str, choice: str, mock_host: str) -> dict:
    import requests

    verifier = _b64(secrets.token_bytes(32))
    challenge = _b64(hashlib.sha256(verifier.encode()).digest())
    r1 = requests.get(f"{api}/auth/v1/authorize", allow_redirects=False, timeout=10,
                      params={"provider": "kakao", "redirect_to": "http://127.0.0.1:56480/callback.html",
                              "code_challenge": challenge, "code_challenge_method": "s256"})
    kakao = urlsplit(r1.headers["location"])
    state = parse_qs(kakao.query)["state"][0]
    r2 = requests.get(urlunsplit(("http", f"{mock_host}:{kakao.port}", "/oauth/decide", f"state={state}&choice={choice}", "")),
                      allow_redirects=False, timeout=10)
    r3 = requests.get(r2.headers["location"], allow_redirects=False, timeout=10)
    code = parse_qs(urlsplit(r3.headers["location"]).query)["code"][0]
    tok = requests.post(f"{api}/auth/v1/token", params={"grant_type": "pkce"}, timeout=10, headers={"apikey": publishable},
                        json={"auth_code": code, "code_verifier": verifier})
    tok.raise_for_status()
    return tok.json()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-dir", required=True)
    p.add_argument("--supabase-url", default="http://127.0.0.1:56321")
    p.add_argument("--publishable-key", required=True)
    p.add_argument("--choice", default="A", choices=["A", "B", "C", "D"])
    p.add_argument("--mock-kakao-host", default="172.17.0.1")
    p.add_argument("--consent", action="store_true", help="중앙에 동의도 저장한다(동의 완료 상태)")
    p.add_argument("--revoke", action="store_true", help="이 계정의 활성 동의를 철회한다(동의 필요 상태)")
    p.add_argument("--official-id", help="config.ini [LOGIN] username (writer dataset_key 용)")
    a = p.parse_args()

    data = Path(a.data_dir).resolve()
    if urlsplit(a.supabase_url).hostname != "127.0.0.1":
        sys.exit("loopback(127.0.0.1) 로컬 스택만 허용합니다")
    if (REPO_ROOT / "data").resolve() == data or data.name == "data" and (data.parent / "main.py").exists():
        sys.exit("운영 data/ 는 쓰지 않습니다")
    data.mkdir(parents=True, exist_ok=True)
    os.environ["SAFETYREPORT_DATA_DIR"] = str(data)
    sys.path.insert(0, str(REPO_ROOT))

    import configparser

    import requests

    from services.community_auth_store import CommunitySessionStore
    from services.community_gate import CONSENT_TEXT_SHA256, REQUIRED_POLICY_VERSION

    cfg_path = data / "config.ini"
    cfg = configparser.ConfigParser()
    cfg.read(cfg_path, encoding="utf-8")
    if not cfg.has_section("COMMUNITY"):
        cfg.add_section("COMMUNITY")
    cfg.set("COMMUNITY", "supabase_url", a.supabase_url)
    cfg.set("COMMUNITY", "publishable_key", a.publishable_key)
    cfg.set("COMMUNITY", "site_url", "http://127.0.0.1:56480/")
    if a.official_id:
        if not cfg.has_section("LOGIN"):
            cfg.add_section("LOGIN")
        cfg.set("LOGIN", "username", a.official_id)
    with open(cfg_path, "w", encoding="utf-8") as fh:
        cfg.write(fh)

    s = kakao_session(a.supabase_url, a.publishable_key, a.choice, a.mock_kakao_host)
    claims = json.loads(base64.urlsafe_b64decode(s["access_token"].split(".")[1] + "=="))
    CommunitySessionStore(str(data)).save({"current": {
        "access_token": s["access_token"], "refresh_token": s["refresh_token"],
        "expires_at": float(s.get("expires_at") or time.time() + 3600), "user_id": s["user"]["id"],
        "display_name": f"QA 카카오 {a.choice}", "has_email": False, "session_id": claims.get("session_id"),
        "connected_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())}})

    def account(action: str, body: dict) -> dict:
        r = requests.post(f"{a.supabase_url}/functions/v1/community-account/{action}", timeout=10, json={"protocol": 1, **body},
                          headers={"apikey": a.publishable_key, "Authorization": f"Bearer {s['access_token']}"})
        return {"status": r.status_code, **(r.json() if r.content else {})}

    st = account("status", {})
    if a.revoke and (st.get("consent") or {}).get("state") == "active":
        account("consent-revoke", {"grant_id": st["consent"]["grant_id"]})
    if a.consent:
        r = account("consent", {"policy_version": REQUIRED_POLICY_VERSION, "consent_text_sha256": CONSENT_TEXT_SHA256,
                                "via": "safetyreport_server", "accepted": True})
        if r["status"] != 200:
            sys.exit(f"consent failed: {r.get('error', {}).get('code')}")
    st = account("status", {})
    print(json.dumps({"data_dir": str(data), "kakao": (st.get("gate") or {}).get("kakao"),
                      "consent": (st.get("consent") or {}).get("state")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
