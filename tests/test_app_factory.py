"""EO R-04: 앱 조립(create_app)과 import 부작용.

- `import main` 만으로는 앱·코어 로거·데이터 하위 폴더·시그널 처리기를 만들지 않는다(새 프로세스에서 확인).
- `main.app`·`main:app`(uvicorn 문자열)은 처음 접근할 때 한 번 만든 같은 앱이다.
- PyInstaller 하위 모드(--mode) 분기는 라우터를 불러오기 전에 있다.
한계: 설정·DB 경로는 프로세스 전역이라 한 프로세스에 데이터 루트가 다른 앱 둘은 만들 수 없다(docs/reviews/2026-10-05-eo-refactor.md).
"""

import ast
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]

_PROBE = """
import json, os, signal, sys
sys.path.insert(0, {root!r})
import main
import settings.settings as st
from core.utils import logger
before = {{"app": main._app is not None, "logger": logger.LoggerFactory.logbot is not None,
          "sigint": signal.getsignal(signal.SIGINT) is main._signal_handler, "dirs": sorted(os.listdir(st.datapath))}}
from uvicorn.importer import import_from_string
app = import_from_string("main:app")
after = {{"same": app is main.app is main.get_app(), "logger": logger.LoggerFactory.logbot is not None,
         "sigint": signal.getsignal(signal.SIGINT) is main._signal_handler, "dirs": sorted(os.listdir(st.datapath)),
         "middleware": [(m.kwargs.get("dispatch").__name__ if m.kwargs.get("dispatch") else m.cls.__name__)
                        for m in app.user_middleware]}}
print(json.dumps({{"before": before, "after": after}}))
"""


class AppFactoryTest(unittest.TestCase):
    def test_import_has_no_side_effects_and_the_app_is_built_once_on_access(self):
        import json

        data_dir = tempfile.mkdtemp(prefix="sr-r04-")
        env = dict(os.environ, SAFETYREPORT_DATA_DIR=data_dir)
        result = subprocess.run([sys.executable, "-c", _PROBE.format(root=str(ROOT))], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(report["before"], {"app": False, "logger": False, "sigint": False, "dirs": []})
        after = report["after"]
        self.assertTrue(after["same"])
        self.assertTrue(after["logger"] and after["sigint"])
        self.assertEqual(after["dirs"], ["auth", "logs", "results"])
        self.assertEqual(after["middleware"][0], "_WebSocketSafeSessionMiddleware")
        self.assertEqual(after["middleware"][1:], ["auth_middleware", "request_timings", "community_gate_middleware",
                                                   "inject_version_middleware"])

    def test_subprocess_modes_are_dispatched_before_router_imports(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        first_router = next(n.lineno for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "web.routers")
        dispatch = next(n.lineno for n in tree.body
                        if isinstance(n, ast.If) and "--mode" in ast.get_source_segment(source, n))
        self.assertLess(dispatch, first_router)

    def test_unknown_module_attribute_still_raises(self):
        import main

        with self.assertRaises(AttributeError):
            main.not_an_attribute  # noqa: B018


if __name__ == "__main__":
    unittest.main()
