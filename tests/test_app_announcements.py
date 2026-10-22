"""앱 공지(9절): 링크 허용 벡터, 응답 검사, 6시간 캐시, 보지 않기 7일·관리자 분리, 닫기, 평문, 실패 시 빈 목록, 부분 템플릿."""
import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from jinja2 import Environment, FileSystemLoader, select_autoescape

from services import app_announcements as aa

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "app-announcements"
NOW = datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc).timestamp()


def item(**kw):
    base = {"id": "2026-10-client-to-standalone", "title": "Client 모드가 곧 없어집니다",
            "body": "첫 줄\n둘째 줄", "primary_action": {"label": "자세히", "url": "https://github.com/Fentanest/safetyreport/releases/latest"},
            "snooze_days": 7, "starts_at": "2026-10-23T00:00:00.000Z", "ends_at": None, "priority": 10}
    base.update(kw)
    return base


def response(*items):
    return {"protocol": 1, "announcements": list(items), "server_time": "2026-10-25T00:00:00.000Z"}


class AnnouncementTest(unittest.TestCase):
    def setUp(self):
        aa._reset_for_tests()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = [NOW]
        self.requests = []
        self.reply = (200, json.dumps(response(item())).encode())
        for p in (mock.patch.object(aa, "_now", lambda: self.clock[0]),
                  mock.patch.object(aa, "_snooze_path", lambda: os.path.join(self.tmp.name, "announcements", "snooze.json")),
                  mock.patch.object(aa, "_config", return_value=("https://x.supabase.example", "pub")),
                  mock.patch.object(aa, "_http_get", side_effect=self._get),
                  mock.patch("services.community_gate._platform", return_value="linux"),
                  mock.patch("services.community_ingest_client._client_version", return_value="3.6.2")):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(aa._reset_for_tests)

    def _get(self, url, params, headers):
        self.requests.append((url, params, headers))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    def advance(self, seconds):
        self.clock[0] += seconds

    # 링크 ----------------------------------------------------------------------
    def test_link_vectors(self):
        cases = json.loads((CONTRACT / "vectors/links.json").read_text(encoding="utf-8"))["cases"]
        self.assertGreater(len(cases), 10)
        for case in cases:
            with self.subTest(case["url"]):
                self.assertEqual(aa.link_allowed(case["url"]), case["allowed"])

    def test_outside_link_hides_button_but_keeps_notice(self):
        self.reply = (200, json.dumps(response(item(primary_action={"label": "열기", "url": "https://evil.example/"}))).encode())
        got = aa.pending_for_admin("admin")
        self.assertEqual(len(got), 1)
        self.assertIsNone(got[0]["primary_action"])

    def test_app_action_button_hidden_on_pc(self):
        self.reply = (200, json.dumps(response(item(primary_action={"label": "지금 바꾸기", "app_action": "open_mode_switch"}))).encode())
        self.assertIsNone(aa.pending_for_admin("admin")[0]["primary_action"])
        self.reply = (200, json.dumps(response(item(id="other-id-1", primary_action={"label": "x", "app_action": "launch_missiles"}))).encode())
        aa._reset_for_tests()
        self.assertEqual(aa.pending_for_admin("admin"), [])  # 모르는 값은 그 공지를 버린다

    # 요청·검사 -----------------------------------------------------------------
    def test_request_shape(self):
        aa.pending_for_admin("admin")
        url, params, headers = self.requests[0]
        self.assertEqual(url, "https://x.supabase.example/functions/v1/app-announcements")
        self.assertEqual(params, [("app", "pc"), ("mode", "server"), ("platform", "linux"), ("version", "3.6.2")])
        self.assertEqual(headers, {"apikey": "pub"})

    def test_contract_example_parses(self):
        example = json.loads((CONTRACT / "examples/response.json").read_text(encoding="utf-8"))
        self.assertTrue(aa.parse_response(example))

    def test_invalid_items_dropped_individually(self):
        bad = [item(id="BAD"), item(id="ok-title-0", title="가" * 41), item(id="ok-body-00", body="가" * 601),
               item(id="ctrl-body-0", body="a\tb"), item(id="extra-key-0", extra=1), item(id="days-neg-00", snooze_days=-1),
               item(id="date-bad-00", starts_at="yesterday"), item(id="days-bool-0", snooze_days=True)]
        good = item(id="good-one-00")
        self.reply = (200, json.dumps(response(*bad[:2], good)).encode())
        self.assertEqual([a["id"] for a in aa.pending_for_admin("a")], ["good-one-00"])
        for b in bad:
            self.assertIsNone(aa.validate_item(b), b["id"])

    def test_more_than_three_keeps_first_three(self):
        items = [item(id=f"notice-{n}-xx") for n in range(5)]
        self.reply = (200, json.dumps(response(*items)).encode())
        self.assertEqual(len(aa.pending_for_admin("a")), 3)

    def test_period_filter(self):
        future = item(id="future-one-0", starts_at="2026-10-26T00:00:00Z")
        ended = item(id="ended-one-00", ends_at="2026-10-24T00:00:00Z")
        live = item(id="live-one-000", ends_at="2026-10-26T00:00:00Z")
        self.reply = (200, json.dumps(response(future, ended, live)).encode())
        self.assertEqual([a["id"] for a in aa.pending_for_admin("a")], ["live-one-000"])

    # 캐시 ----------------------------------------------------------------------
    def test_cache_six_hours_for_all_admins(self):
        aa.pending_for_admin("a")
        aa.pending_for_admin("b")
        self.advance(6 * 3600 - 1)
        aa.pending_for_admin("a")
        self.assertEqual(len(self.requests), 1)
        self.advance(2)
        aa.pending_for_admin("a")
        self.assertEqual(len(self.requests), 2)

    def test_failure_gives_empty_list_and_waits_for_next_time(self):
        for reply in ((500, b"{}"), (404, b"{}"), (200, b"not json"), (200, b'{"protocol":2,"announcements":[],"server_time":"2026-10-25T00:00:00Z"}'),
                      (200, b"x" * (aa.MAX_BYTES + 1)), OSError("down")):
            aa._reset_for_tests()
            self.reply = reply
            self.assertEqual(aa.pending_for_admin("a"), [])
        aa._reset_for_tests()
        self.reply = (500, b"{}")
        self.assertEqual(aa.pending_for_admin("a"), [])
        before = len(self.requests)
        self.reply = (200, json.dumps(response(item())).encode())
        self.assertEqual(aa.pending_for_admin("a"), [])  # 다음 때(6시간)까지 반복하지 않는다
        self.assertEqual(len(self.requests), before)

    def test_unconfigured_is_empty_without_network(self):
        with mock.patch.object(aa, "_config", return_value=None):
            self.assertEqual(aa.pending_for_admin("a"), [])
        self.assertEqual(self.requests, [])

    def test_on_login_forces_refresh_and_clears_dismissals(self):
        aa.pending_for_admin("a")
        aa.dismiss("a", "2026-10-client-to-standalone")
        self.assertEqual(aa.pending_for_admin("a"), [])
        self.advance(120)
        with mock.patch("threading.Thread") as thread:
            aa.on_login("a")
            target = thread.call_args.kwargs["target"]
        target()
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(len(aa.pending_for_admin("a")), 1)

    # 보지 않기·닫기 ------------------------------------------------------------
    def test_snooze_seven_days_then_returns(self):
        aa.pending_for_admin("alice")
        aa.snooze("alice", "2026-10-client-to-standalone")
        self.assertEqual(aa.pending_for_admin("alice"), [])
        data = json.loads(Path(aa._snooze_path()).read_text(encoding="utf-8"))
        self.assertEqual(data, {"alice": {"2026-10-client-to-standalone": "2026-11-01T00:00:00Z"}})
        self.advance(7 * 86400 - 5)
        self.reply = (200, json.dumps(response(item())).encode())
        self.assertEqual(aa.pending_for_admin("alice"), [])
        self.advance(10)
        self.assertEqual(len(aa.pending_for_admin("alice")), 1)
        self.assertEqual(json.loads(Path(aa._snooze_path()).read_text(encoding="utf-8")), data)  # 읽기는 파일을 바꾸지 않는다

    def test_snooze_days_from_notice_and_labels(self):
        self.reply = (200, json.dumps(response(item(id="three-days-00", snooze_days=3), item(id="no-snooze-00", snooze_days=0),
                                                item(id="week-notice-0", snooze_days=7))).encode())
        got = {a["id"]: a for a in aa.pending_for_admin("a")}
        self.assertEqual(got["three-days-00"]["snooze_label"], "3일 동안 보지 않기")
        self.assertEqual(got["week-notice-0"]["snooze_label"], "일주일 동안 보지 않기")
        self.assertIsNone(got["no-snooze-00"]["snooze_label"])
        aa.snooze("a", "three-days-00")
        aa.snooze("a", "no-snooze-00")  # 0일 공지는 숨기지 않는다
        data = json.loads(Path(aa._snooze_path()).read_text(encoding="utf-8"))
        self.assertEqual(data["a"], {"three-days-00": "2026-10-28T00:00:00Z"})

    def test_snooze_is_per_admin(self):
        aa.pending_for_admin("alice")
        aa.snooze("alice", "2026-10-client-to-standalone")
        self.assertEqual(aa.pending_for_admin("alice"), [])
        self.assertEqual(len(aa.pending_for_admin("bob")), 1)

    def test_expired_entries_are_dropped_on_read_and_garbage_ignored(self):
        path = Path(aa._snooze_path())
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"a": {"old-notice-00": "2020-01-01T00:00:00Z", "bad id": "2099-01-01T00:00:00Z"}}), encoding="utf-8")
        aa.pending_for_admin("a")
        aa.snooze("a", "new-notice-00")
        self.assertEqual(list(json.loads(path.read_text(encoding="utf-8"))["a"]), ["new-notice-00"])
        path.write_text("{broken", encoding="utf-8")
        self.assertEqual(len(aa.pending_for_admin("a")), 1)
        aa.snooze("a", "new-notice-00")  # 깨진 파일 위에도 저장된다

    def test_snooze_rejects_bad_id_and_never_raises(self):
        aa.snooze("a", "../../etc")
        aa.snooze("a", None)
        self.assertFalse(os.path.exists(aa._snooze_path()))
        with mock.patch.object(aa, "_write_snooze", side_effect=OSError("disk full")):
            aa.snooze("a", "valid-notice-0")

    def test_dismiss_is_memory_only_and_per_admin(self):
        aa.pending_for_admin("alice")
        aa.dismiss("alice", "2026-10-client-to-standalone")
        self.assertEqual(aa.pending_for_admin("alice"), [])
        self.assertEqual(len(aa.pending_for_admin("bob")), 1)
        self.assertFalse(os.path.exists(aa._snooze_path()))
        aa._dismissed.clear()  # 새 로그인/재시작
        self.assertEqual(len(aa.pending_for_admin("alice")), 1)

    # 평문·템플릿 ---------------------------------------------------------------
    def test_html_in_body_stays_text_in_popup(self):
        html_body = "<script>alert(1)</script>\n<b>굵게</b>"
        self.reply = (200, json.dumps(response(item(body=html_body))).encode())
        got = aa.pending_for_admin("a")
        self.assertEqual(got[0]["body"], html_body)  # 변환하지 않는다
        env = Environment(loader=FileSystemLoader(str(ROOT / "web/templates")), autoescape=select_autoescape(["html"]))
        out = env.get_template("partials/announcement_popup.html").render(announcements=got)
        self.assertNotIn("<script>alert(1)</script>", out)
        self.assertIn("\\u003cscript\\u003e", out)  # tojson 이스케이프
        self.assertIn("textContent", out)
        self.assertNotIn("innerHTML", out)
        self.assertEqual(env.get_template("partials/announcement_popup.html").render(announcements=[]).strip(), "")


if __name__ == "__main__":
    unittest.main()
