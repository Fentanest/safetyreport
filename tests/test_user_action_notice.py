"""사용자 조치 안내(8절) — 순수 함수와 부분 템플릿 렌더."""
import unittest
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from services import user_action_notice as un

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = ("writer", "backfill", "outbox", "게이트", "동기화 대기열", "run", "hold", "contract", "v1", "v2", "원활한",
             "더 나은 서비스를 위해", "최적의", "새롭게", "편리하게", "~를 통해", "다양한", "효율적", "보다 나은", "!", "~", "보류")


def evaluate(**kw):
    base = dict(backfill_reason=None, official_login_manual=False, stale_holds=0, update_required=False)
    base.update(kw)
    return un.evaluate(**base)


class NoticeTest(unittest.TestCase):
    def test_nothing(self):
        self.assertEqual(evaluate(), [])

    def test_official_login_needs_manual_flag(self):
        self.assertEqual(evaluate(backfill_reason="official_login_required"), [])
        items = evaluate(backfill_reason="official_login_required", official_login_manual=True)
        self.assertEqual([i["code"] for i in items], ["official_login_required"])
        self.assertEqual(items[0]["text"], "안전신문고 로그인이 필요합니다. 설정에서 다시 로그인해 주세요.")
        self.assertEqual(items[0]["action_label"], "로그인")

    def test_retry_reason_is_not_a_notice(self):
        self.assertEqual(evaluate(backfill_reason="official_login_retry", official_login_manual=True), [])
        self.assertEqual(evaluate(backfill_reason="maintenance"), [])

    def test_optimize_blocked(self):
        items = evaluate(backfill_reason="optimize_blocked_background")
        self.assertEqual(items[0]["text"], "프로그램을 켜 두면 신고 정리를 마치고 공유를 이어 갑니다.")
        self.assertIsNone(items[0]["action_label"])

    def test_stale_holds_count_and_button(self):
        items = evaluate(stale_holds=3)
        self.assertEqual(items[0]["text"], "30일 넘게 올리지 못한 신고가 3건 있습니다. 다시 시도를 눌러 주세요.")
        self.assertEqual(items[0]["action_label"], "다시 시도")
        self.assertEqual(evaluate(stale_holds=0), [])
        self.assertEqual(evaluate(stale_holds="x"), [])

    def test_update_required_first_and_all_shapes(self):
        items = evaluate(update_required=True, stale_holds=1, backfill_reason="official_login_required", official_login_manual=True)
        self.assertEqual([i["code"] for i in items], ["update_required", "official_login_required", "stale_holds"])
        for item in items:
            self.assertEqual(set(item), {"code", "text", "action_label", "action_url"})
            for word in FORBIDDEN:
                self.assertNotIn(word, item["text"])

    def test_partial_renders_escaped_with_retry_button(self):
        env = Environment(loader=FileSystemLoader(str(ROOT / "web/templates")), autoescape=select_autoescape(["html"]))
        tpl = env.get_template("partials/notice_banner.html")
        self.assertEqual(tpl.render(notices=[]).strip(), "")
        html = tpl.render(notices=[{"code": "stale_holds", "text": "<b>x</b>", "action_label": "다시 시도", "action_url": None},
                                   {"code": "update_required", "text": "t", "action_label": "업데이트", "action_url": un.UPDATE_URL}])
        self.assertNotIn("<b>x</b>", html)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)
        self.assertIn("data-notice-retry", html)
        self.assertIn('target="_blank"', html)


if __name__ == "__main__":
    unittest.main()
