"""커뮤니티 공개 설정·동의문 사본이 실행파일·Docker 산출물에 들어가는지(T8). 실제 빌드는 CI/로컬 수동 확인."""
from __future__ import annotations

import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_build_exe():
    spec = importlib.util.spec_from_file_location("build_exe", ROOT / "scripts" / "build" / "build_exe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CommunityPackagingTest(unittest.TestCase):
    def setUp(self):
        self.build = _load_build_exe()
        self.cwd = os.getcwd()
        self.tmp = tempfile.mkdtemp(prefix="pkg-")
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self.cwd)

    def test_public_config_only_accepts_public_https_values(self):
        ok = {"COMMUNITY_SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co",
              "COMMUNITY_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_abcdefghijklmnop"}
        path = self.build.write_community_public(ok)
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        self.assertEqual(data, {"supabase_url": ok["COMMUNITY_SUPABASE_URL"], "publishable_key": ok["COMMUNITY_SUPABASE_PUBLISHABLE_KEY"],
                                "site_url": "https://safeauth.worklazy.net/"})
        self.assertIsNone(self.build.write_community_public({}))
        for bad in ({**ok, "COMMUNITY_SUPABASE_PUBLISHABLE_KEY": "sb_secret_abcdefghijklmnop"},
                    {**ok, "COMMUNITY_SUPABASE_URL": "https://<PROJECT_REF>.supabase.co"},
                    {**ok, "COMMUNITY_SUPABASE_URL": "http://127.0.0.1:56321"},
                    {**ok, "COMMUNITY_SUPABASE_PUBLISHABLE_KEY": "YOUR_PUBLISHABLE_KEY"},
                    {**ok, "COMMUNITY_SUPABASE_URL": ""}):
            with self.assertRaises(SystemExit, msg=str(bad)):
                self.build.write_community_public(bad)

    def test_legacy_key_name_is_an_alias_and_conflicts_stop_the_build(self):
        url = {"COMMUNITY_SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co"}
        path = self.build.write_community_public({**url, "COMMUNITY_PUBLISHABLE_KEY": "sb_publishable_abcdefghijklmnop"})
        self.assertEqual(json.loads(Path(path).read_text(encoding="utf-8"))["publishable_key"], "sb_publishable_abcdefghijklmnop")
        same = {**url, "COMMUNITY_PUBLISHABLE_KEY": "sb_publishable_abcdefghijklmnop", "COMMUNITY_SUPABASE_PUBLISHABLE_KEY": "sb_publishable_abcdefghijklmnop"}
        self.assertIsNotNone(self.build.write_community_public(same))
        with self.assertRaises(SystemExit):
            self.build.write_community_public({**same, "COMMUNITY_PUBLISHABLE_KEY": "sb_publishable_zzzzzzzzzzzzzzzz"})

    def test_release_builds_refuse_missing_public_config(self):
        with self.assertRaises(SystemExit):
            self.build.write_community_public({"COMMUNITY_CONFIG_REQUIRED": "1"})
        with self.assertRaises(SystemExit):
            self.build.write_community_public({"COMMUNITY_CONFIG_REQUIRED": "1", "COMMUNITY_SUPABASE_URL": "https://abcdefghijklmnopqrst.supabase.co"})
        self.assertIsNone(self.build.write_community_public({}))  # local/dev builds only warn

    def test_docker_image_bakes_the_public_config_with_the_same_validation(self):
        docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("python scripts/build/write_community_public.py --out /app/community_public.json", docker)
        build = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")
        self.assertIn("COMMUNITY_SUPABASE_PUBLISHABLE_KEY=${{ vars.COMMUNITY_SUPABASE_PUBLISHABLE_KEY }}", build)
        self.assertIn("COMMUNITY_CONFIG_REQUIRED=1", build)
        ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertNotIn("scripts/build/", ignore)

    def test_consent_copy_is_bundled_and_not_docker_ignored(self):
        source = (ROOT / "scripts" / "build" / "build_exe.py").read_text(encoding="utf-8")
        self.assertIn("--add-data=contracts/community-ingest/consent{sep}contracts/community-ingest/consent", source)
        ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("!contracts/community-ingest/consent/*.md", ignore)
        self.assertGreater(ignore.index("!contracts/community-ingest/consent/*.md"), ignore.index("*.md"),
                           "예외는 *.md 뒤에 있어야 적용된다")
        self.assertTrue((ROOT / "contracts" / "community-ingest" / "consent" / "share-consent-2026-09-26.1.md").is_file())

    def test_workflows_pass_only_public_variables(self):
        for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
            text = wf.read_text(encoding="utf-8")
            if "scripts/build/build_exe.py" not in text:
                continue
            self.assertIn("COMMUNITY_SUPABASE_URL: ${{ vars.COMMUNITY_SUPABASE_URL }}", text, wf.name)
            # 정본 이름(Android·지도·저장소 Variables 와 같음). 옛 이름으로 읽으면 값이 비어 배포 빌드가 멈춘다.
            self.assertIn("COMMUNITY_SUPABASE_PUBLISHABLE_KEY: ${{ vars.COMMUNITY_SUPABASE_PUBLISHABLE_KEY }}", text, wf.name)
            self.assertNotIn("vars.COMMUNITY_PUBLISHABLE_KEY", text, wf.name)
            self.assertIn("COMMUNITY_CONFIG_REQUIRED: '1'", text, wf.name)
            self.assertNotRegex(text, r"COMMUNITY_[A-Z_]+: \$\{\{ secrets\.", wf.name)


if __name__ == "__main__":
    unittest.main()
