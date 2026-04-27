"""안전신문고 직접 로그인 (Selenium 없이).

login.md 역공학 결과:
1. GET  /api/v1/common/rsa/getPublicKey  → JSESSIONID 쿠키 + RSAModulus/RSAExponent
2. POST /oauth/token (form-urlencoded)  → access_token (1시간 유효)
3. 이후 API 호출은 Authorization: BEARER + JSESSIONID 쿠키

세션 영속화: data/auth_token.json — start.py 서브프로세스와 FastAPI가 공유.
55분마다 백그라운드에서 자동 재로그인 (만료 5분 마진).

Python `requests`는 TLS ClientHello가 브라우저와 달라 connection reset 발생 →
`curl_cffi`로 Chrome impersonation 사용.
"""
from __future__ import annotations
import json
import os
import threading
import time
from datetime import datetime, timedelta
from typing import Optional

from cryptography.hazmat.primitives.asymmetric import rsa, padding
from cryptography.hazmat.primitives import hashes  # noqa: F401  (PKCS1 import 정렬용)
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

import settings.settings as settings
from core.utils import logger

_BASE = "https://www.safetyreport.go.kr"
_KEY_URL = f"{_BASE}/api/v1/common/rsa/getPublicKey"
_TOKEN_URL = f"{_BASE}/oauth/token"
_TOKEN_FILE = lambda: os.path.join(settings.datapath, "auth_token.json")  # noqa: E731

_COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.safetyreport.go.kr/",
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Origin": "https://www.safetyreport.go.kr",
}


def _rsa_encrypt_hex(modulus_hex: str, exponent_hex: str, plaintext: str) -> str:
    """PKCS#1 v1.5 RSA 암호화 → hex 문자열 (브라우저 JSEncrypt 호환)."""
    modulus = int(modulus_hex, 16)
    exponent = int(exponent_hex, 16)
    public_key = RSAPublicNumbers(exponent, modulus).public_key(default_backend())
    encrypted = public_key.encrypt(plaintext.encode("utf-8"), padding.PKCS1v15())
    return encrypted.hex()


def _make_session():
    from curl_cffi import requests as cffi_requests
    s = cffi_requests.Session(impersonate="chrome120")
    s.headers.update(_COMMON_HEADERS)
    return s


def _login_once(username: str, password: str) -> dict:
    """단일 로그인 시도. 성공 시 {access_token, expires_at, jsessionid, wmonid}."""
    session = _make_session()

    # Step 1: RSA 공개키 (재시도 3회)
    last_err = None
    for attempt in range(3):
        try:
            res = session.get(_KEY_URL, timeout=15)
            res.raise_for_status()
            break
        except Exception as e:
            last_err = e
            time.sleep(attempt + 1)
    else:
        raise RuntimeError(f"RSA 키 조회 실패: {last_err}")

    key_data = res.json()
    modulus_hex = key_data["RSAModulus"]
    exponent_hex = key_data["RSAExponent"]

    # JSESSIONID는 session.cookies에 자동 저장됨

    # Step 2: 비밀번호 RSA 암호화
    encrypted_pw = _rsa_encrypt_hex(modulus_hex, exponent_hex, password)

    # Step 3: 토큰 발급 (재시도 3회)
    # 주의: curl_cffi가 dict를 form-urlencoded로 직렬화하는 방식이 안전신문고 서버와
    # 호환되지 않아 401을 반환함. 모바일 Dart 코드처럼 직접 문자열로 만들어 보내야 함.
    from urllib.parse import quote
    body = (
        "client_id=web"
        "&grant_type=password"
        "&loginType=1"
        f"&username={quote(username, safe='')}"
        f"&password={encrypted_pw}"
    )
    last_err = None
    for attempt in range(3):
        try:
            res = session.post(
                _TOKEN_URL,
                data=body,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Content-Length": str(len(body)),
                },
                timeout=15,
            )
            break
        except Exception as e:
            last_err = e
            time.sleep(attempt + 1)
    else:
        raise RuntimeError(f"토큰 요청 실패: {last_err}")

    if res.status_code in (400, 401):
        try:
            err = res.json()
            detail = err.get("error_description") or "아이디/비밀번호 오류"
        except Exception:
            detail = "아이디/비밀번호 오류"
        raise RuntimeError(f"로그인 거부: {detail}")
    if res.status_code != 200:
        raise RuntimeError(f"로그인 실패 HTTP {res.status_code}: {res.text[:200]}")

    token_data = res.json()
    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("응답에 access_token 없음")
    expires_in = int(token_data.get("expires_in", 3599))
    expires_at = (datetime.now() + timedelta(seconds=expires_in)).timestamp()

    cookies = {c.name: c.value for c in session.cookies.jar}

    return {
        "access_token": access_token,
        "expires_at": expires_at,
        "jsessionid": cookies.get("JSESSIONID", ""),
        "wmonid": cookies.get("WMONID", ""),
        "issued_at": datetime.now().timestamp(),
    }


def login_and_cache() -> dict:
    """로그인 후 토큰 정보를 디스크에 저장."""
    if not settings.username or not settings.password:
        raise RuntimeError("settings.username / settings.password 가 비어 있습니다.")
    info = _login_once(settings.username, settings.password)
    save_token(info)
    if logger.LoggerFactory.logbot:
        logger.LoggerFactory.logbot.info(
            f"[direct_login] 로그인 성공. 토큰 만료 시각: "
            f"{datetime.fromtimestamp(info['expires_at']).strftime('%H:%M:%S')}"
        )
    return info


def save_token(info: dict) -> None:
    path = _TOKEN_FILE()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False)


def load_token() -> Optional[dict]:
    path = _TOKEN_FILE()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def is_token_valid(info: Optional[dict] = None, margin_seconds: int = 300) -> bool:
    """토큰이 존재하고, 만료 5분 마진 내에 들어가지 않았는지."""
    if info is None:
        info = load_token()
    if not info or not info.get("access_token"):
        return False
    return datetime.now().timestamp() < info["expires_at"] - margin_seconds


def get_valid_token(force_refresh: bool = False) -> dict:
    """유효한 토큰을 반환. 만료됐거나 없으면 재로그인."""
    info = load_token() if not force_refresh else None
    if force_refresh or not is_token_valid(info):
        info = login_and_cache()
    return info


def make_authorized_session():
    """재로그인된 토큰을 가진 curl_cffi 세션 반환. API 호출용."""
    info = get_valid_token()
    s = _make_session()
    s.headers.update({"Authorization": f"BEARER {info['access_token']}"})
    if info.get("jsessionid"):
        s.cookies.set("JSESSIONID", info["jsessionid"], domain="www.safetyreport.go.kr")
    if info.get("wmonid"):
        s.cookies.set("WMONID", info["wmonid"], domain="www.safetyreport.go.kr")
    return s, info


def request_with_retry(session, method, url, *,
                       max_attempts=None, base_backoff=1.0, **kwargs):
    """curl_cffi 일시 네트워크 오류(errno=104, timeout 등) silent 재시도 헬퍼.

    catch 대상: SSLError(connection reset 포함), ConnectionError, Timeout,
    DNSError, IncompleteRead, ChunkedEncodingError, RequestException.
    4xx/5xx 응답은 재시도 무의미 → 그대로 반환 (호출자가 status_code 처리).

    max_attempts: None이면 settings.max_retry_attemps 사용 (앱 설정 페이지 값).
    backoff: attempt * base_backoff 초 sleep. 최종 실패 시 마지막 예외 그대로 raise.
    """
    from curl_cffi.requests import exceptions as cce
    if max_attempts is None:
        try:
            max_attempts = max(1, int(settings.max_retry_attemps))
        except Exception:
            max_attempts = 3
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            return session.request(method, url, **kwargs)
        except (cce.SSLError, cce.ConnectionError, cce.Timeout,
                cce.DNSError, cce.IncompleteRead, cce.ChunkedEncodingError,
                cce.RequestException) as e:
            last_err = e
            if attempt < max_attempts:
                if logger.LoggerFactory.logbot:
                    logger.LoggerFactory.logbot.warning(
                        f"[direct_login] {method} {url} 일시 오류 "
                        f"({attempt}/{max_attempts}): {e}. {attempt * base_backoff}초 후 재시도"
                    )
                time.sleep(attempt * base_backoff)
            else:
                if logger.LoggerFactory.logbot:
                    logger.LoggerFactory.logbot.error(
                        f"[direct_login] {method} {url} {max_attempts}회 재시도 실패: {e}"
                    )
    raise last_err


# ── 백그라운드 keep-alive ─────────────────────────────────────────

_keepalive_thread: Optional[threading.Thread] = None
_keepalive_stop = threading.Event()


def _keepalive_loop(interval_seconds: int = 55 * 60):
    """55분마다 토큰 갱신."""
    log = logger.LoggerFactory.logbot
    while not _keepalive_stop.is_set():
        try:
            login_and_cache()
        except Exception as e:
            if log:
                log.error(f"[direct_login] keep-alive 갱신 실패: {e}. 5분 후 재시도.")
            # 실패 시 5분 후 재시도
            if _keepalive_stop.wait(300):
                break
            continue
        if _keepalive_stop.wait(interval_seconds):
            break


def start_keepalive(interval_seconds: int = 55 * 60) -> bool:
    """백그라운드 갱신 스레드 시작. 이미 실행 중이면 False."""
    global _keepalive_thread
    if _keepalive_thread and _keepalive_thread.is_alive():
        return False
    if not settings.username or not settings.password:
        if logger.LoggerFactory.logbot:
            logger.LoggerFactory.logbot.warning(
                "[direct_login] 자격증명이 비어 있어 keep-alive 시작 안 함."
            )
        return False
    _keepalive_stop.clear()
    _keepalive_thread = threading.Thread(
        target=_keepalive_loop, args=(interval_seconds,), daemon=True, name="direct_login_keepalive"
    )
    _keepalive_thread.start()
    if logger.LoggerFactory.logbot:
        logger.LoggerFactory.logbot.info(
            f"[direct_login] keep-alive 스레드 시작 ({interval_seconds // 60}분 주기)"
        )
    return True


def stop_keepalive():
    _keepalive_stop.set()
