"""ucu-proxy.py against tests/fake_cua_repl.py, talking JSON lines over stdio like Claude Code."""
import itertools
import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROXY = ROOT / "scripts/ucu-proxy.py"
FAKE = ROOT / "tests/fake_cua_repl.py"


FAKE_LOG_COMMAND = """#!/bin/sh
exec tail -n 0 -f "$FAKE_ALERTS"
"""


class Proxy:
    """A running proxy around the fake cua_repl. --stop-grace 0 stops cua_repl as soon as a turn ends.

    With from_plugin, the proxy is started without a command, as Claude Code starts it, and finds
    the fake through the plugin folders written by plugin().
    """

    def __init__(self, test, from_plugin=False):
        self.home = tempfile.TemporaryDirectory()  # HOME without Sky, so turn ends notify nothing real
        self.log = Path(self.home.name) / "fake.log"
        self.alert_file = Path(self.home.name) / "alerts.txt"
        self.alert_file.touch()
        self.window_log = Path(self.home.name) / "windows.log"
        self.window_reports = Path(self.home.name) / "window-reports.txt"
        self.window_reports.touch()
        self.tools = tools = Path(self.home.name) / "tools"
        tools.mkdir()
        (tools / "log").write_text(FAKE_LOG_COMMAND)
        (tools / "log").chmod(0o755)
        env = {**os.environ, "HOME": self.home.name, "FAKE_LOG": str(self.log), "FAKE_ALERTS": str(self.alert_file),
               "FAKE_WINDOW_LOG": str(self.window_log), "FAKE_WINDOW_REPORTS": str(self.window_reports),
               "FAKE_SAFARI_STATE": str(Path(self.home.name) / "safari.json"),
               "PATH": f"{tools}:{os.environ['PATH']}"}
        command = [] if from_plugin else ["--", sys.executable, "-I", str(FAKE)]
        folder = Path(self.home.name) / "My Project"
        folder.mkdir()
        self.process = subprocess.Popen([sys.executable, "-I", str(PROXY), "--stop-grace", "0", *command],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, cwd=folder)
        self.messages = queue.Queue()
        self.ids = itertools.count(1)
        threading.Thread(target=self._read, daemon=True).start()
        test.addCleanup(self.close)

    def plugin(self, version, surfaces):
        folder = Path(self.home.name) / ".codex/plugins/cache/openai-bundled/unified-computer-use" / version
        folder.mkdir(parents=True)
        (folder / ".mcp.json").write_text(json.dumps({"mcpServers": {"cua_repl": {
            "command": sys.executable, "args": ["-I", str(FAKE)],
            "env": {"CUA_REPL_NODE_REPL_PATH": sys.executable, "CUA_REPL_ENABLED_SURFACES": surfaces}}}}))

    def policy(self, **lists):
        path = Path(self.home.name) / "Library/Application Support/ucu/policy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(lists))

    def window_helper(self):
        """A stand-in for ucu-window that echoes its arguments and fails to find a window titled "Missing"."""
        path = Path(self.home.name) / ".local/bin/ucu-window"
        path.parent.mkdir(parents=True)
        path.write_text('#!/bin/sh\necho "args: $*"\n'
                        '[ "$3" = Missing ] && { echo \'No window title contains "Missing".\' >&2; exit 2; }\nexit 0\n')
        path.chmod(0o755)

    def approvals(self):
        path = Path(self.home.name) / "Library/Logs/ucu/approve.log"
        return [line.split(" ", 2)[2] for line in path.read_text().splitlines()] if path.exists() else []

    def _read(self):
        for line in self.process.stdout:
            self.messages.put(json.loads(line))

    def send(self, message):
        self.process.stdin.write((json.dumps({"jsonrpc": "2.0", **message}) + "\n").encode())
        self.process.stdin.flush()

    def receive(self):
        return self.messages.get(timeout=10)

    def request(self, method, params=None):
        request_id = next(self.ids)
        self.send({"id": request_id, "method": method, "params": params or {}})
        reply = self.receive()
        assert reply.get("id") == request_id, reply
        return reply

    def initialize(self):
        reply = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}},
                                            "clientInfo": {"name": "test client", "version": "1"}})
        self.send({"method": "notifications/initialized"})
        return reply

    def call(self, name, code="1"):
        return self.request("tools/call", {"name": name, "arguments": {"code": code}})

    def end_turn(self):
        self.process.send_signal(signal.SIGINFO)  # what the Stop hook sends

    def events(self, kind):
        lines = self.log.read_text().splitlines() if self.log.exists() else []
        return [event for event in map(json.loads, lines) if event["event"] == kind]

    def received(self, method):
        return [event for event in self.events("recv") if event["message"].get("method") == method]

    def wait_until(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate():
            if time.monotonic() > deadline:
                raise AssertionError("timed out")
            time.sleep(0.05)

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:
                self.process.wait(10)
            except subprocess.TimeoutExpired:
                self.process.kill()
        self.process.stdout.close()
        self.home.cleanup()


def meta(reply):
    return json.loads(reply["result"]["content"][-1]["text"])["x-codex-turn-metadata"]


class ProxyTest(unittest.TestCase):
    def test_answers_server_discover_without_starting_cua_repl(self):
        proxy = Proxy(self)
        proxy.send({"id": 0, "method": "server/discover"})
        self.assertEqual(proxy.receive()["error"]["code"], -32601)
        self.assertEqual(proxy.events("start"), [])

    def test_adds_turn_metadata_and_starts_a_new_turn_after_the_stop_hook(self):
        proxy = Proxy(self)
        proxy.initialize()
        first = meta(proxy.call("js"))
        self.assertTrue(first["session_id"].startswith("claude-code-"))
        self.assertEqual(first["turn_id"], f"{first['session_id']}-turn-1")

        proxy.end_turn()
        proxy.wait_until(lambda: len(proxy.events("exit")) == 1)  # stopped after the turn
        second = meta(proxy.call("js"))
        self.assertEqual(second["turn_id"], f"{first['session_id']}-turn-2")
        self.assertEqual(len(proxy.events("start")), 2)
        self.assertEqual(len(proxy.received("initialize")), 2)  # replayed to the restarted cua_repl
        self.assertEqual(len(proxy.received("notifications/initialized")), 2)
        self.assertEqual([e for e in proxy.received("tools/call") if e["message"]["params"]["name"] == "turn_ended"], [])

    def test_leaves_out_documentation_this_chat_already_has(self):
        proxy = Proxy(self)
        proxy.initialize()
        self.assertTrue(proxy.call("js")["result"]["content"][0]["text"].startswith("# cua_repl documentation"))

        proxy.end_turn()
        proxy.wait_until(lambda: len(proxy.events("exit")) == 1)
        repeated = proxy.call("js")["result"]["content"][0]["text"]
        self.assertTrue(repeated.startswith("[ucu restarted and sent its first-use documentation again"), repeated)

        proxy.call("js_reset")
        proxy.end_turn()
        proxy.wait_until(lambda: len(proxy.events("exit")) == 2)
        self.assertTrue(proxy.call("js")["result"]["content"][0]["text"].startswith("# cua_repl documentation"))

    def test_fails_a_call_whose_cua_repl_crashed_and_restarts_on_the_next(self):
        proxy = Proxy(self)
        proxy.initialize()
        error = proxy.call("js", "crash")["error"]
        self.assertEqual(error["code"], -32603)
        self.assertIn("ucu stopped unexpectedly", error["message"])
        self.assertIn("may already have happened", error["message"])
        self.assertIn("result", proxy.call("js"))
        self.assertEqual(len(proxy.events("start")), 2)

    def test_asks_claude_when_there_is_no_policy(self):
        proxy = Proxy(self)
        proxy.initialize()
        proxy.send({"id": 99, "method": "tools/call", "params": {"name": "js", "arguments": {"code": "elicit"}}})
        prompt = proxy.receive()
        self.assertEqual(prompt["method"], "elicitation/create")
        proxy.send({"id": prompt["id"], "result": {"action": "accept", "content": {}}})
        reply = proxy.receive()
        self.assertEqual(reply["id"], 99)
        self.assertEqual(json.loads(reply["result"]["content"][0]["text"])["action"], "accept")
        self.assertEqual(proxy.approvals(), ["test-client:My-Project asked about Calculator"])

    def test_answers_approval_prompts_from_the_policy(self):
        proxy = Proxy(self)
        proxy.policy(deny=["Keychain Access"])
        proxy.initialize()
        allowed = proxy.call("js", "elicit")  # the reply comes straight back: nothing asked Claude
        self.assertEqual(json.loads(allowed["result"]["content"][0]["text"]), {"action": "accept", "content": {}})
        denied = proxy.call("js", "elicit:Keychain Access")
        self.assertEqual(json.loads(denied["result"]["content"][0]["text"]), {"action": "decline"})
        self.assertEqual(proxy.approvals(), ["test-client:My-Project allowed Calculator",
                                             "test-client:My-Project denied Keychain Access"])

    def test_tells_the_browser_surface_when_a_turn_that_used_it_ends(self):
        proxy = Proxy(self)
        proxy.initialize()
        proxy.call("js", 'let tab = await cua.getTab({ url: "https://example.com" });')
        proxy.end_turn()
        proxy.wait_until(lambda: len(proxy.events("exit")) == 1)
        ended = [e["message"]["params"] for e in proxy.received("tools/call") if e["message"]["params"]["name"] == "turn_ended"]
        self.assertEqual(len(ended), 1)
        self.assertEqual(ended[0]["arguments"]["hook_event_name"], "Stop")

    def test_starts_cua_repl_from_the_codex_plugin_with_its_environment(self):
        proxy = Proxy(self, from_plugin=True)
        proxy.plugin("26.1021.31345", "browser,computer")
        proxy.initialize()
        self.assertIn("result", proxy.call("js"))
        self.assertEqual([e["surfaces"] for e in proxy.events("start")], ["browser,computer"])

    def test_picks_up_a_codex_update_the_next_time_cua_repl_starts(self):
        proxy = Proxy(self, from_plugin=True)
        proxy.plugin("26.1016.62342", "old")
        proxy.initialize()
        proxy.call("js")
        proxy.plugin("26.1021.31345", "new")
        proxy.end_turn()
        proxy.wait_until(lambda: len(proxy.events("exit")) == 1)
        self.assertIn("result", proxy.call("js"))
        self.assertEqual([e["surfaces"] for e in proxy.events("start")], ["old", "new"])

    def test_says_why_it_cannot_start_without_a_plugin(self):
        proxy = Proxy(self, from_plugin=True)
        reply = proxy.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                             "clientInfo": {"name": "test", "version": "1"}})
        self.assertIn("ucu could not start: no usable Codex computer-use plugin", reply["error"]["message"])

    def test_adds_the_usage_rules_after_cua_repls_own_instructions(self):
        proxy = Proxy(self)
        instructions = proxy.initialize()["result"]["instructions"]
        self.assertTrue(instructions.startswith("Fake cua_repl.\n\nucu rules:"), instructions)
        self.assertIn("`list_windows`", instructions)

    def notes(self, reply):
        return [item["text"] for item in reply["result"]["content"] if "alert sound" in item["text"]]

    def test_tells_the_agent_about_an_alert_sound_from_its_app(self):
        proxy = Proxy(self)
        proxy.initialize()
        proxy.call("js", 'let app = await cua.getApp("Calculator");')
        time.sleep(0.3)  # the fake log's start
        self.assertEqual(self.notes(proxy.call("js", "alert:Calculator")), [
            "[ucu: Calculator played an alert sound during your last actions, which usually means it rejected "
            "an input. Check the app before going on.]"])
        self.assertEqual(self.notes(proxy.call("js")), [])  # told once

    def test_tells_the_agent_about_a_sound_too_late_for_the_last_result(self):
        proxy = Proxy(self)
        proxy.initialize()
        proxy.call("js", 'let app = await cua.getApp("Calculator");')
        time.sleep(0.3)
        proxy.call("js", "alert-late:Calculator")
        time.sleep(0.3)
        self.assertEqual(len(self.notes(proxy.call("js"))), 1)

    def test_ignores_other_apps_other_sounds_and_sounds_between_calls(self):
        proxy = Proxy(self)
        proxy.initialize()
        proxy.call("js", 'let app = await cua.getApp("Calculator");')
        time.sleep(0.3)
        self.assertEqual(self.notes(proxy.call("js", "alert:Safari")), [])
        self.assertEqual(self.notes(proxy.call("js", "other-sound:Calculator")), [])
        with proxy.alert_file.open("a") as handle:  # the user, a while after the last call
            time.sleep(1.2)
            handle.write("-> Incoming Request : actionID 4096, inClientPID 7(Calculator), inBehavior 1\n")
        time.sleep(0.3)
        self.assertEqual(self.notes(proxy.call("js")), [])

    def test_exits_even_when_cua_repl_dies_as_it_starts(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
        for _ in range(20):  # the race needs cua_repl to win it, which isn't every time
            process = subprocess.Popen([sys.executable, "-I", str(PROXY), "--", "/usr/bin/false"],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self.addCleanup(process.stdout.close)
            self.addCleanup(process.kill)
            process.stdin.write((json.dumps(request) + "\n").encode())
            process.stdin.close()
            self.assertEqual(process.wait(5), 0)

    def test_stops_watching_for_sounds_when_cua_repl_stops(self):
        proxy = Proxy(self)
        proxy.initialize()
        proxy.call("js")
        tails = lambda: subprocess.run(["pgrep", "-f", str(proxy.alert_file)], capture_output=True, text=True).stdout.split()
        proxy.wait_until(lambda: len(tails()) == 1)
        proxy.end_turn()
        proxy.wait_until(lambda: not tails())

    def test_lists_the_window_tools_after_cua_repls_own(self):
        proxy = Proxy(self)
        proxy.initialize()
        tools = proxy.request("tools/list")["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools],
                         ["js", "js_reset", "list_windows", "focus_window", "move_to_desktop", "safari"])

    def test_runs_the_window_tools_without_starting_cua_repl(self):
        proxy = Proxy(self)
        proxy.window_helper()
        listed = proxy.request("tools/call", {"name": "list_windows", "arguments": {"app": "Windows App"}})
        self.assertEqual(listed["result"], {"content": [{"type": "text", "text": "args: list Windows App"}],
                                            "isError": False})
        focused = proxy.request("tools/call", {"name": "focus_window", "arguments": {
            "app": "Windows App", "title": "Work PC", "unfullscreen": True}})
        self.assertEqual(focused["result"]["content"][0]["text"], "args: focus Windows App Work PC --unfullscreen")
        missing = proxy.request("tools/call", {"name": "focus_window", "arguments": {"app": "Notes", "title": "Missing"}})
        self.assertTrue(missing["result"]["isError"])
        self.assertIn('No window title contains "Missing".', missing["result"]["content"][0]["text"])
        self.assertEqual(proxy.events("start"), [])

    def test_says_how_to_get_the_window_helper_when_it_is_missing(self):
        proxy = Proxy(self)
        result = proxy.request("tools/call", {"name": "list_windows", "arguments": {"app": "Finder"}})["result"]
        self.assertTrue(result["isError"])
        self.assertIn("`ucu install` builds it", result["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
