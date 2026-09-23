#!/usr/bin/env python3
"""웹UI 계약 인벤토리(기계 추출).

라우트 데코레이터, 템플릿 렌더 위치, 템플릿별 DOM id / JS 참조 id / fetch·form URL / JS 함수를
정적으로 뽑아 JSON 으로 출력한다. 리뉴얼 전후 비교(누락된 id, 사라진 엔드포인트 호출)에 쓴다.
서버를 띄우거나 앱 모듈을 import 하지 않는다.

    python scripts/dev/web_contract_inventory.py > web-contract-inventory.json
    python scripts/dev/web_contract_inventory.py --check-ids "#foo" "#bar"   # 존재 여부만 확인
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ROUTERS = ROOT / "web" / "routers"
TEMPLATES = ROOT / "web" / "templates"

_ROUTE_RE = re.compile(r"@(?:router|app)\.(get|post|put|patch|delete|websocket)\(\s*[\"']([^\"']*)[\"']")
_FUNC_RE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)")
_PREFIX_RE = re.compile(r"APIRouter\([^)]*prefix\s*=\s*[\"']([^\"']+)[\"']")
_RENDER_RE = re.compile(r"TemplateResponse\(\s*(?:request\s*,\s*)?[\"']([\w./-]+\.html)[\"']")
_ID_RE = re.compile(r"\bid=[\"']([^\"'{}\s]+)[\"']")
_NAME_RE = re.compile(r"\bname=[\"']([^\"'{}\s]+)[\"']")
_JS_ID_RES = [
    re.compile(r"getElementById\(\s*[\"'`]([\w-]+)[\"'`]"),
    re.compile(r"(?:\$|jQuery|querySelector(?:All)?)\(\s*[\"'`]#([\w-]+)"),
]
_FETCH_RE = re.compile(r"(?:fetch|\$\.(?:get|post|ajax|getJSON))\(\s*[\"'`]([^\"'`$]+)|\burl\s*:\s*[\"'`]([^\"'`$]+)")
_ACTION_RE = re.compile(r"\baction=[\"']([^\"'{}]+)[\"']")
_HREF_RE = re.compile(r"\bhref=[\"'](/[^\"'{}#?]*)")
_JS_FUNC_RE = re.compile(r"(?:function\s+(\w+)\s*\(|(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s*)?(?:function|\([^)]*\)\s*=>))")
_EXTENDS_RE = re.compile(r"{%\s*extends\s+[\"']([\w./-]+)[\"']")
_INCLUDE_RE = re.compile(r"{%\s*include\s+[\"']([\w./-]+)[\"']")


def _line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


def scan_routes() -> list[dict]:
    routes = []
    for path in sorted(list(ROUTERS.glob("*.py")) + [ROOT / "main.py"]):
        text = path.read_text(encoding="utf-8")
        prefix_match = _PREFIX_RE.search(text)
        prefix = prefix_match.group(1) if prefix_match else ""
        lines = text.splitlines()
        for match in _ROUTE_RE.finditer(text):
            line = _line_of(text, match.start())
            func = ""
            for follow in lines[line:line + 8]:
                found = _FUNC_RE.match(follow)
                if found:
                    func = found.group(1)
                    break
            routes.append({
                "method": match.group(1).upper(),
                "path": prefix + match.group(2),
                "handler": func,
                "where": f"{path.relative_to(ROOT)}:{line}",
            })
        for match in _RENDER_RE.finditer(text):
            routes.append({
                "method": "RENDER",
                "path": match.group(1),
                "handler": "",
                "where": f"{path.relative_to(ROOT)}:{_line_of(text, match.start())}",
            })
    return routes


def scan_templates() -> dict[str, dict]:
    result = {}
    for path in sorted(TEMPLATES.glob("*.html")):
        text = path.read_text(encoding="utf-8")
        ids = sorted(set(_ID_RE.findall(text)))
        js_ids = set()
        for regex in _JS_ID_RES:
            js_ids.update(regex.findall(text))
        funcs = sorted({a or b for a, b in _JS_FUNC_RE.findall(text)})
        result[path.name] = {
            "extends": _EXTENDS_RE.findall(text),
            "includes": _INCLUDE_RE.findall(text),
            "element_ids": ids,
            "js_referenced_ids": sorted(js_ids),
            "js_ids_missing_in_same_file": sorted(js_ids - set(ids)),
            "form_names": sorted(set(_NAME_RE.findall(text))),
            "fetch_urls": sorted({a or b for a, b in _FETCH_RE.findall(text)}),
            "form_actions": sorted(set(_ACTION_RE.findall(text))),
            "internal_hrefs": sorted(set(_HREF_RE.findall(text))),
            "js_functions": funcs,
        }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check-ids", nargs="*", help="#id 또는 id 목록이 어떤 템플릿에 있는지 확인")
    args = parser.parse_args()

    templates = scan_templates()
    if args.check_ids is not None:
        missing = 0
        for raw in args.check_ids:
            element_id = raw.lstrip("#")
            owners = [name for name, info in templates.items() if element_id in info["element_ids"]]
            print(f"{'OK ' if owners else 'MISSING'} #{element_id} {', '.join(owners)}")
            missing += 0 if owners else 1
        return 1 if missing else 0

    json.dump({"routes": scan_routes(), "templates": templates}, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
