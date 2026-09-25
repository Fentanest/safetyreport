"""비회원(수동) 로그인·레거시 크롤링 제거 회귀 (G16 W3·W4, 2026-09-25).

크롤링은 회원 로그인 + API 방식(+브라우저 비상 경로)만 있다. 구앱이 옛 값을 보내도 무시하고, 구앱이 읽는 응답 필드는 남긴다.
"""
import asyncio
import inspect
import unittest
from unittest import mock

from fastapi import HTTPException


class NonMemberRemovedTests(unittest.TestCase):
    def test_command_has_no_nonmember_flag_and_start_takes_no_login_mode(self):
        from services import crawl_control

        self.assertNotIn("login_mode", inspect.signature(crawl_control._build_command).parameters)
        self.assertNotIn("login_mode", inspect.signature(crawl_control.start_crawl).parameters)
        self.assertFalse(hasattr(crawl_control, "resume_crawl"))
        self.assertNotIn("--nonmember", crawl_control._build_command())

    def test_web_resume_route_is_gone(self):
        from web.routers import crawl

        self.assertNotIn("/crawl/resume", [r.path for r in crawl.router.routes])

    def test_mobile_resume_answers_410_for_old_apps(self):
        from web.routers import api_route

        with self.assertRaises(HTTPException) as ctx:
            api_route.mobile_resume_crawl(_="key")
        self.assertEqual(ctx.exception.status_code, 410)

    def test_mobile_start_ignores_old_login_mode(self):
        from web.routers import api_route

        request = mock.Mock()

        async def body():
            return {"login_mode": "nonmember", "crawl_mode": "full", "queue_list": ""}

        request.json = body
        with mock.patch.object(api_route.crawl_manager, "is_crawling", return_value=False), \
             mock.patch.object(api_route.crawl_control, "start_crawl") as start:
            asyncio.run(api_route.mobile_start_crawl(request, _="key"))
        self.assertNotIn("login_mode", start.call_args.kwargs)

    def test_start_script_has_no_nonmember_path(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "start.py").read_text(encoding="utf-8")
        self.assertNotIn("nonmember", source)
        self.assertNotIn("resume.sig", source)


if __name__ == "__main__":
    unittest.main()
