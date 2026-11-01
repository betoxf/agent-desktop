"""How ucu-proxy.py picks cua_repl from the Codex plugin folders, in a temporary HOME."""
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests/fake_cua_repl.py"
TEMPLATE = {"command": "node", "enabled": False}  # what Codex writes first during an update
ENV = {"CUA_REPL_NODE_REPL_PATH": sys.executable, "CUA_REPL_ENABLED_SURFACES": "computer"}
ENTRY = {"command": sys.executable, "args": [str(FAKE)], "env": ENV}


def load_proxy(home):
    previous = os.environ.get("HOME")
    os.environ["HOME"] = home  # its paths are computed at import
    try:
        spec = importlib.util.spec_from_file_location("ucu_proxy_under_test", ROOT / "scripts/ucu-proxy.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        os.environ["HOME"] = previous
    return module


class PluginTest(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.proxy = load_proxy(home.name)

    def plugin(self, version, entry):
        folder = self.proxy.PLUGIN_DIR / version
        folder.mkdir(parents=True)
        (folder / ".mcp.json").write_text(json.dumps({"mcpServers": {"cua_repl": entry}}))

    def test_uses_the_newest_version_by_number(self):
        for version in ("26.999.1", "26.1021.31345", "26.1007.30912"):
            self.plugin(version, ENTRY)
        self.assertEqual(self.proxy.plugin_command(), ("26.1021.31345", [sys.executable, str(FAKE)], ENV))

    def test_skips_the_disabled_template_during_an_update(self):
        self.plugin("26.1016.62342", ENTRY)
        self.plugin("26.1021.31345", TEMPLATE)
        self.assertEqual(self.proxy.plugin_command()[0], "26.1016.62342")

    def test_says_why_no_version_is_usable(self):
        with self.assertRaisesRegex(RuntimeError, "no usable Codex computer-use plugin"):
            self.proxy.plugin_command()
        self.plugin("26.1021.31345", TEMPLATE)
        (self.proxy.PLUGIN_DIR / "26.1022.1").mkdir()
        (self.proxy.PLUGIN_DIR / "26.1022.1/.mcp.json").write_text("{")  # still being written
        with self.assertRaisesRegex(RuntimeError, r"26\.1022\.1: unreadable .*26\.1021\.31345: entry is disabled"):
            self.proxy.plugin_command()

    def test_reports_missing_paths(self):
        issues = self.proxy.entry_problems({"command": "/nonexistent/node", "args": ["/nonexistent/cua.mjs"], "env": {}})
        self.assertEqual(len(issues), 4, issues)
        self.assertEqual(self.proxy.entry_problems(ENTRY), [])


if __name__ == "__main__":
    unittest.main()
