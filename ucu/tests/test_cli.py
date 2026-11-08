"""`ucu install`, `doctor`, `uninstall` and the deny commands in a temporary HOME.

claude, swiftc and cargo are fakes in $HOME/.local/bin, which ucu searches first, so nothing outside the
temporary HOME is touched, and the real engine in target/release is only copied if it is there.
"""
import fcntl
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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


FAKE_CARGO = """
if Path.home().joinpath("no-rust").exists():
    sys.exit("error: no rust")
built = Path.home() / "target/release/agent-desktop"
built.parent.mkdir(parents=True, exist_ok=True)
built.write_text("#!/bin/sh\\necho version\\n")
built.chmod(0o755)
"""


class CliTest(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        self.home = Path(home.name)
        bin_dir = self.home / ".local/bin"
        bin_dir.mkdir(parents=True)
        for name, body in (("claude", FAKE_CLAUDE), ("swiftc", FAKE_SWIFTC), ("cargo", FAKE_CARGO)):
            path = bin_dir / name
            path.write_text(f"#!{sys.executable}\nimport json, sys\nfrom pathlib import Path\n" + body)
            path.chmod(0o755)
        self.env = {**os.environ, "HOME": str(self.home), "PATH": f"{bin_dir}:/usr/bin:/bin",
                    "CARGO_TARGET_DIR": str(self.home / "target"), "GIT_AUTHOR_NAME": "Test",
                    "GIT_AUTHOR_EMAIL": "test@example.com", "GIT_COMMITTER_NAME": "Test",
                    "GIT_COMMITTER_EMAIL": "test@example.com"}
        self.assertEqual(shutil.which("claude", path=self.env["PATH"]), str(bin_dir / "claude"))
        self.updates_off = self.home / "Library/Application Support/ucu/no-auto-update"
        self.updates_off.parent.mkdir(parents=True)
        self.updates_off.touch()  # the servers that install and doctor start would update the clone tests run from

        (self.home / ".claude.json").write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "numStartups": 3}))
        (self.home / ".claude").mkdir()
        (self.home / ".claude/settings.json").write_text(
            json.dumps({"model": "opus", "hooks": {"Stop": [OTHER_HOOK]}}, indent=2))

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
        self.assertTrue((self.home / ".local/bin/ucu-engine").exists())

        edited = {"deny": ["Mail"]}
        (self.home / POLICY).write_text(json.dumps(edited))
        code, output = self.ucu("install", "--python", sys.executable)  # again: nothing doubles up
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.stop_hooks()), 2)
        self.assertEqual(self.read_json(POLICY), edited)

        code, output = self.ucu("doctor")
        self.assertEqual(code, 0, output)
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
        (self.home / POLICY).write_text("{")
        code, output = self.ucu("deny", "Mail")
        self.assertEqual(code, 1, output)
        self.assertEqual((self.home / POLICY).read_text(), "{")

    # --- update: a throwaway repository with a copy of ucu/ and a stand-in for the engine's code, and a clone of it
    def git(self, folder, *args):
        result = subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def clone(self):
        origin = self.home / "origin"
        shutil.copytree(ROOT, origin / "ucu", ignore=shutil.ignore_patterns("tests", "__pycache__", "assets", "benchmarks"))
        (origin / "Cargo.toml").write_text("[workspace]\n")
        self.git(origin, "init", "-q", "-b", "ucu")
        self.git(origin, "add", "-A")
        self.git(origin, "commit", "-q", "-m", "first")
        self.git(self.home, "clone", "-q", str(origin), "clone")
        return origin, self.home / "clone"

    def commit(self, folder, path, text):
        (folder / path).write_text(text)
        self.git(folder, "commit", "-q", "-am", f"change {path}")

    def update(self, clone, *args):
        result = subprocess.run([sys.executable, str(clone / "ucu/ucu"), "update", *args], capture_output=True,
                                text=True, env=self.env)
        return result.returncode, result.stdout + result.stderr

    def same(self, origin, clone):
        return self.git(origin, "rev-parse", "HEAD") == self.git(clone, "rev-parse", "HEAD")

    def test_update_fast_forwards_and_rebuilds_only_what_changed(self):
        origin, clone = self.clone()
        engine, helper = self.home / ".local/bin/ucu-engine", self.home / ".local/bin/ucu-window"
        self.assertEqual(self.update(clone), (0, "✓ ucu is up to date\n"))

        self.commit(origin, "ucu/README.md", "new words\n")
        code, output = self.update(clone)
        self.assertEqual(code, 0, output)
        self.assertTrue(self.same(origin, clone))
        self.assertFalse(engine.exists() or helper.exists(), output)  # only ucu/ changed: nothing to build

        self.commit(origin, "Cargo.toml", "[workspace]\nmembers = []\n")
        self.commit(origin, "ucu/native/ucu-window.swift", "// new\n")
        code, output = self.update(clone)
        self.assertEqual(code, 0, output)
        self.assertTrue(self.same(origin, clone))
        self.assertTrue(engine.exists() and helper.exists(), output)

    def test_update_leaves_local_changes_and_commits_alone(self):
        origin, clone = self.clone()
        self.commit(origin, "ucu/README.md", "theirs\n")
        (clone / "ucu/README.md").write_text("mine\n")
        code, output = self.update(clone)
        self.assertEqual(code, 1, output)
        self.assertIn("this clone has local changes, so it was left as it is", output)
        self.commit(clone, "ucu/README.md", "mine\n")
        code, output = self.update(clone)
        self.assertEqual(code, 1, output)
        self.assertIn("this clone has commits that origin/ucu doesn't", output)
        self.assertEqual((clone / "ucu/README.md").read_text(), "mine\n")

    def test_update_goes_back_when_the_engine_does_not_build(self):
        origin, clone = self.clone()
        before = self.git(clone, "rev-parse", "HEAD")
        (self.home / "no-rust").touch()
        self.commit(origin, "Cargo.toml", "[workspace]\nmembers = []\n")
        code, output = self.update(clone)
        self.assertEqual(code, 1, output)
        self.assertIn("went back to the version before the update", output)
        self.assertEqual(self.git(clone, "rev-parse", "HEAD"), before)
        self.assertEqual(self.git(clone, "status", "--porcelain", "--untracked-files=no"), "")

    def test_chats_update_one_at_a_time_and_at_most_every_ten_minutes(self):
        origin, clone = self.clone()
        state = self.home / "Library/Application Support/ucu"
        self.updates_off.unlink()
        self.commit(origin, "ucu/README.md", "one\n")
        self.assertEqual(self.update(clone, "--auto"), (0, ""))
        self.assertTrue(self.same(origin, clone))
        self.assertIn("✓ updated to", (self.home / "Library/Logs/ucu/update.log").read_text())

        self.commit(origin, "ucu/README.md", "two\n")
        self.update(clone, "--auto")  # the last check was just now
        self.assertFalse(self.same(origin, clone))
        os.utime(state / "update-checked", (0, 0))
        with (state / "update.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)  # another chat is updating
            self.update(clone, "--auto")
        self.assertFalse(self.same(origin, clone))
        self.updates_off.touch()
        self.update(clone, "--auto")
        self.assertFalse(self.same(origin, clone))
        self.updates_off.unlink()
        self.update(clone, "--auto")
        self.assertTrue(self.same(origin, clone))

    def test_install_changes_nothing_when_the_engine_does_not_build(self):
        (self.home / "no-rust").touch()
        code, output = self.ucu("install", "--python", sys.executable)
        self.assertEqual(code, 1, output)
        self.assertIn("could not build the engine", output)
        self.assertEqual(sorted(self.read_json(".claude.json")["mcpServers"]), ["other"])
        self.assertEqual(self.stop_hooks(), [OTHER_HOOK])


if __name__ == "__main__":
    unittest.main()
