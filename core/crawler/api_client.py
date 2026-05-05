from __future__ import annotations

from time import sleep

import settings.settings as settings

from core.crawler import direct_login


def ensure_browser_context(driver):
    if driver is None:
        raise RuntimeError("브라우저 API fallback에는 Selenium driver가 필요합니다.")
    if "safetyreport.go.kr" not in (driver.current_url or ""):
        driver.get(settings.myreporturl)
        sleep(2)


def get_authorized_json(session, url: str, *, params: dict | None = None, timeout: int = 20):
    try:
        response = direct_login.request_with_retry(
            session,
            "GET",
            url,
            params=params,
            timeout=timeout,
        )
    except Exception as exc:
        return {"error": f"network: {exc}"}, session

    if response.status_code == 401:
        direct_login.get_valid_token(force_refresh=True)
        session, _ = direct_login.make_authorized_session()
        try:
            response = direct_login.request_with_retry(
                session,
                "GET",
                url,
                params=params,
                timeout=timeout,
            )
        except Exception as exc:
            return {"error": f"network after relogin: {exc}"}, session

    if response.status_code != 200:
        return {"error": f"HTTP {response.status_code}"}, session

    try:
        return response.json(), session
    except Exception as exc:
        return {"error": f"JSON parse: {exc}"}, session
