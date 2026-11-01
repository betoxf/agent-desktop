"""`ucu install`, `doctor`, `uninstall` and the deny commands in a temporary HOME.

claude and swiftc are fakes in $HOME/.local/bin, which ucu searches first, so nothing outside the
temporary HOME is touched. The server ucu registers is the real proxy, which finds
tests/fake_cua_repl.py through a plugin folder in the temporary HOME.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FAKE = ROOT / "tests/fake_cua_repl.py"
PROXY = str(ROOT / "scripts/ucu-proxy.py")
OTHER_HOOK = {"hooks": [{"type": "command", "command": "afplay done.aiff"}]}
OPENCODE = {"$schema": "https://opencode.ai/config.json", "model": "local/qwen",
            "mcp": {"github": {"type": "remote", "url": "https://example.com/mcp"}}}
POLICY = "Library/Application Support/ucu/policy.json"

FAKE_CLAUDE = """
config = Path.home() / ".claude.json"
data = json.loads(config.read_text())
servers = data.setdefault("mcpServers", {})
action, name = sys.argv[2], sys.argv[3]
found = servers.pop(name, None) if action == "remove" else True
if action == "add-json":
    servers[name] = json.loads(sys.argv[4])
config.write_text(json.dumps(data))
sys.exit(0 if found else 1)
"""
FAKE_SWIFTC = """
out = Path(sys.argv[sys.argv.index("-o") + 1])
out.write_text("#!/bin/sh\\nexit 0\\n")
out.chmod(0o755)
"""


class CliTest(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = Path(home.name)
        bin_dir = self.home / ".local/bin"
        bin_dir.mkdir(parents=True)
        for name, body in (("claude", FAKE_CLAUDE), ("swiftc", FAKE_SWIFTC)):
            path = bin_dir / name
            path.write_text(f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n" + body)
            path.chmod(0o755)
        self.env = {**os.environ, "HOME": str(self.home), "PATH": f"{bin_dir}:/usr/bin:/bin"}
        self.assertEqual(shutil.which("claude", path=self.env["PATH"]), str(bin_dir / "claude"))

        (self.home / ".claude.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "numStartups": 3}))
        (self.home / ".claude").mkdir()
        (self.home / ".claude/settings.json").write_text(
            json.dumps({"model": "opus", "hooks": {"Stop": [OTHER_HOOK]}}, indent=2))
        self.plugin = self.home / ".codex/plugins/cache/openai-bundled/unified-computer-use/26.1021.31345/.mcp.json"
        self.plugin.parent.mkdir(parents=True)
        self.plugin.write_text(json.dumps({"mcpServers": {"cua_repl": {
            "command": sys.executable, "args": [str(FAKE)],
            "env": {"CUA_REPL_NODE_REPL_PATH": sys.executable, "CUA_REPL_ENABLED_SURFACES": "computer"}}}}))

    def opencode(self):
        (self.home / ".config/opencode").mkdir(parents=True)
        (self.home / ".config/opencode/opencode.json").write_text(json.dumps(OPENCODE, indent=2) + "\n")

    def read_json(self, relative):
        return json.loads((self.home / relative).read_text())

    def ucu(self, *args):
        result = subprocess.run([sys.executable, str(ROOT / "ucu"), *args], capture_output=True, text=True, env=self.env)
        return result.returncode, result.stdout + result.stderr

    def stop_hooks(self):
        return self.read_json(".claude/settings.json")["hooks"]["Stop"]

    def test_install_doctor_uninstall(self):
        self.opencode()
        code, output = self.ucu("install", "--python", sys.executable)
        self.assertEqual(code, 0, output)

        servers = self.read_json(".claude.json")["mcpServers"]
        self.assertEqual(sorted(servers), ["other", "ucu"])
        self.assertEqual(servers["ucu"], {"type": "stdio", "command": sys.executable, "args": ["-I", PROXY], "env": {},
                                          "timeout": 300000, "alwaysLoad": True})
        self.assertEqual(self.read_json(".claude/settings.json")["model"], "opus")
        self.assertEqual(self.stop_hooks()[0], OTHER_HOOK)
        self.assertEqual(self.stop_hooks()[1]["hooks"][0]["command"], 'pkill -INFO -P "$PPID" -f ucu-proxy.py; exit 0')
        self.assertEqual(self.read_json(".config/opencode/opencode.json"), {**OPENCODE, "mcp": {
            **OPENCODE["mcp"], "ucu": {"type": "local", "command": [sys.executable, "-I", PROXY], "enabled": True}}})
        policy = self.read_json(POLICY)
        self.assertIn("Keychain Access", policy["deny"])
        self.assertNotIn("allow", policy)
        backup = next((self.home / "Library/Application Support/ucu/backups").iterdir())
        self.assertEqual(json.loads((backup / "opencode.json").read_text()), OPENCODE)
        self.assertTrue((self.home / ".local/bin/ucu-window").exists())

        edited = {"deny": ["Mail"]}
        (self.home / POLICY).write_text(json.dumps(edited))
        code, output = self.ucu("install", "--python", sys.executable)  # again: nothing doubles up
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.stop_hooks()), 2)
        self.assertEqual(self.read_json(POLICY), edited)

        code, output = self.ucu("doctor")
        self.assertEqual(code, 0, output)
        self.assertIn("Codex computer-use plugin 26.1021.31345 is usable", output)
        self.assertIn("the approval policy denies 1 apps and approves every other app", output)
        self.assertNotIn("✗", output)

        code, output = self.ucu("uninstall")
        self.assertEqual(code, 0, output)
        self.assertEqual(self.read_json(".claude.json")["mcpServers"], {"other": {"command": "x"}})
        self.assertEqual(self.read_json(".claude/settings.json"), {"model": "opus", "hooks": {"Stop": [OTHER_HOOK]}})
        self.assertEqual(self.read_json(".config/opencode/opencode.json"), OPENCODE)
        self.assertFalse((self.home / ".local/bin/ucu-window").exists())
        self.assertEqual(self.read_json(POLICY), edited)

        code, output = self.ucu("doctor")
        self.assertEqual(code, 1, output)

    def test_install_on_a_mac_with_no_claude_settings_or_opencode(self):
        (self.home / ".claude/settings.json").unlink()
        code, output = self.ucu("install", "--python", sys.executable)
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.stop_hooks()), 1)
        self.assertFalse((self.home / ".config/opencode").exists())

    def test_deny_allow_and_denied(self):
        self.assertEqual(self.ucu("denied")[1].strip(), "No apps are denied: every app is approved.")
        self.assertEqual(self.ucu("deny", "Mail")[0], 0)
        self.assertEqual(self.ucu("deny", "mail")[0], 0)  # the same app: no duplicate
        self.assertEqual(self.ucu("deny", "Messages")[0], 0)
        self.assertEqual(self.read_json(POLICY)["deny"], ["mail", "Messages"])
        self.assertEqual(self.ucu("allow", "MAIL")[0], 0)
        self.assertEqual(self.ucu("denied")[1].strip(), "Messages")

    def test_deny_keeps_a_broken_policy_as_it_was(self):
        (self.home / POLICY).parent.mkdir(parents=True)
        (self.home / POLICY).write_text("{")
        code, output = self.ucu("deny", "Mail")
        self.assertEqual(code, 1, output)
        self.assertEqual((self.home / POLICY).read_text(), "{")

    def test_install_changes_nothing_when_no_plugin_is_usable(self):
        self.plugin.write_text(json.dumps({"mcpServers": {"cua_repl": {"command": "node", "enabled": False}}}))
        code, output = self.ucu("install", "--python", sys.executable)
        self.assertEqual(code, 1, output)
        self.assertIn("nothing was changed", output)
        self.assertEqual(sorted(self.read_json(".claude.json")["mcpServers"]), ["other"])
        self.assertEqual(self.stop_hooks(), [OTHER_HOOK])
        self.assertFalse((self.home / "Library/Application Support/ucu").exists())

    def test_install_changes_nothing_when_cua_repl_does_not_answer(self):
        entry = json.loads(self.plugin.read_text())["mcpServers"]["cua_repl"]
        self.plugin.write_text(json.dumps({"mcpServers": {"cua_repl": {**entry, "command": "/usr/bin/false"}}}))
        code, output = self.ucu("install", "--python", sys.executable)
        self.assertEqual(code, 1, output)
        self.assertIn("ucu did not start with Codex plugin 26.1021.31345", output)
        self.assertEqual(sorted(self.read_json(".claude.json")["mcpServers"]), ["other"])


if __name__ == "__main__":
    unittest.main()
