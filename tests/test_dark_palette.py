"""다크 B안 팔레트 — 웹 tokens.css 가 모바일과 같은 값(contracts/dark-palette.json, 두 레포 바이트 동일)."""
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PALETTE = json.loads((ROOT / "contracts" / "dark-palette.json").read_text(encoding="utf-8"))
TOKENS = (ROOT / "web" / "static" / "ui" / "tokens.css").read_text(encoding="utf-8")


def _dark_block() -> dict:
    block = TOKENS[TOKENS.index('[data-bs-theme="dark"] {'):]
    block = block[: block.index("}")]
    return {k: v.strip().lower() for k, v in re.findall(r"(--sr-[a-z0-9-]+):\s*([^;]+);", block)}


def _luminance(hex_color: str) -> float:
    rgb = [int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class DarkPaletteTests(unittest.TestCase):
    def test_tokens_match_shared_palette(self):
        dark = _dark_block()
        mapping = {
            "bg": "--sr-bg", "surface": "--sr-surface", "surface_2": "--sr-surface-2", "surface_3": "--sr-surface-3",
            "border": "--sr-border", "text": "--sr-text", "text_muted": "--sr-text-muted",
            "primary_fill": "--sr-primary-fill", "primary_text": "--sr-primary",
        }
        for key, token in mapping.items():
            self.assertEqual(dark[token].split()[0], PALETTE[key], token)

    def test_text_and_fill_contrast(self):
        p = PALETTE
        for bg in (p["bg"], p["surface"], p["surface_2"], p["surface_3"]):
            self.assertGreaterEqual(contrast(p["text"], bg), 4.5)
            self.assertGreaterEqual(contrast(p["text_muted"], bg), 4.5)
            self.assertGreaterEqual(contrast(p["primary_text"], bg), 4.5)
        self.assertGreaterEqual(contrast("#ffffff", p["primary_fill"]), 4.5)

    def test_neutrals_are_not_blue(self):
        # "블루 모드" 가 아니게: 바탕·표면·테두리의 채도는 낮아야 한다(예전 #060e22 는 70%)
        import colorsys

        for key in ("bg", "surface", "surface_2", "surface_3", "border"):
            h = PALETTE[key]
            r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
            _, _, s = colorsys.rgb_to_hls(r, g, b)
            self.assertLess(s, 0.1, key)


if __name__ == "__main__":
    unittest.main()
