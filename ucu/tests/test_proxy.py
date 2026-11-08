"""ucu-proxy.py against tests/fake_engine.py, talking JSON lines over stdio like Claude Code."""
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
FAKE = ROOT / "tests/fake_engine.py"
ALERT = ("2026-10-31 14:32:01.598 Df systemsoundserverd[644:37dae] -> Incoming Request : actionID 4096, "
         "inClientPID 3959({}), inBehavior 0\n")


FAKE_LOG_COMMAND = """#!/bin/sh
exec tail -n 0 -f "$FAKE_ALERTS"
"""


class Proxy:
    """A running server around the fake engine. --stop-grace 0 stops the helpers as soon as a turn ends."""

    def __init__(self, test, policy=True):
        self.home = tempfile.TemporaryDirectory()
        self.engine_log = Path(self.home.name) / "engine.log"
        self.alert_file = Path(self.home.name) / "alerts.txt"
        self.alert_file.touch()
        self.window_log = Path(self.home.name) / "windows.log"
        self.window_reports = Path(self.home.name) / "window-reports.txt"
        self.window_reports.touch()
        self.tools = tools = Path(self.home.name) / "tools"
        tools.mkdir()
        (tools / "log").write_text(FAKE_LOG_COMMAND)
        (tools / "log").chmod(0o755)
        engine = Path(self.home.name) / ".local/bin/ucu-engine"
        engine.parent.mkdir(parents=True)
        engine.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{FAKE}" "$@"\n')
        engine.chmod(0o755)
        env = {**os.environ, "HOME": self.home.name, "FAKE_ENGINE_LOG": str(self.engine_log),
               "FAKE_ALERTS": str(self.alert_file), "FAKE_WINDOW_LOG": str(self.window_log),
               "FAKE_WINDOW_REPORTS": str(self.window_reports),
               "FAKE_SAFARI_STATE": str(Path(self.home.name) / "safari.json"), "PATH": f"{tools}:{os.environ['PATH']}"}
        folder = Path(self.home.name) / "My Project"
        folder.mkdir()
        updates_off = Path(self.home.name) / "Library/Application Support/ucu/no-auto-update"
        updates_off.parent.mkdir(parents=True)
        updates_off.touch()  # or the server would update the clone these tests run from
        self.process = subprocess.Popen([sys.executable, "-I", str(PROXY), "--stop-grace", "0"],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env, cwd=folder)
        self.messages = queue.Queue()
        self.ids = itertools.count(1)
        threading.Thread(target=self._read, daemon=True).start()
        test.addCleanup(self.close)
        if policy:
            self.policy(deny=[])

    def policy(self, **lists):
        path = Path(self.home.name) / "Library/Application Support/ucu/policy.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(lists))

    def window_helper(self):
        """A stand-in for ucu-window that echoes its arguments and fails to find a window titled "Missing"."""
        path = Path(self.home.name) / ".local/bin/ucu-window"
        path.parent.mkdir(parents=True, exist_ok=True)
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

    def call(self, name, **arguments):
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def look(self, app="Calculator", **arguments):
        return self.call("look", app=app, **arguments)

    def act(self, *steps, app="Calculator"):
        return self.call("act", app=app, steps=list(steps))

    def text(self, reply):
        return "\n".join(item.get("text", "") for item in reply["result"]["content"])

    def end_turn(self):
        self.process.send_signal(signal.SIGINFO)  # what the Stop hook sends

    def engine_calls(self, command=None):
        lines = self.engine_log.read_text().splitlines() if self.engine_log.exists() else []
        calls = [json.loads(line)["args"] for line in lines]
        return [call for call in calls if command in (None, call[0])]

    def batched(self):
        return [entry for call in self.engine_calls("batch") for entry in json.loads(call[1])]

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



class ServerTest(unittest.TestCase):
    def setUp(self):
        self.server = Proxy(self)
        self.server.initialize()

    def test_answers_server_discover_with_method_not_found(self):
        reply = self.server.request("server/discover")
        self.assertEqual(reply["error"]["code"], -32601)

    def test_sends_the_usage_rules_and_lists_its_tools(self):
        server = Proxy(self)
        rules = server.initialize()["result"]["instructions"]
        self.assertIn("ucu rules:", rules)
        self.assertIn("confirmations", rules)
        names = [tool["name"] for tool in server.request("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["look", "act", "list_windows", "focus_window", "move_to_desktop", "safari"])

    def test_keeps_tool_definitions_and_rules_small(self):
        tools = json.dumps(self.server.request("tools/list")["result"]["tools"])
        self.assertLess(len(tools), 6000)

    def test_shows_a_window_as_numbered_text(self):
        text = self.server.text(self.server.look())
        self.assertIn('Window: "Calculator".', text)
        self.assertIn("\t1 button Add", text)
        self.assertIn("2 textfield (focused) Note, Value: hi, Actions: SetValue", text)
        self.assertIn("statictext 0, Description: Display", text)

    def test_says_when_nothing_changed_and_shows_all_when_asked(self):
        self.server.look()
        self.assertEqual(self.server.text(self.server.look()), 'No change in "Calculator".')
        self.assertIn("Window:", self.server.text(self.server.look(full=True)))

    def test_reads_only_what_changed_after_an_action(self):
        self.server.look()
        text = self.server.text(self.server.act({"do": "click", "on": 1}))
        self.assertIn("+ statictext 1, Description: Display", text)
        self.assertIn("- statictext 0, Description: Display", text)
        self.assertNotIn("button Add", text)
        self.assertEqual(self.server.batched()[0], {"command": "click", "args": {"ref_id": "@s1:e1"}})

    def test_runs_a_stretch_of_steps_in_one_engine_run(self):
        self.server.look()
        self.server.act({"do": "click", "on": 1}, {"do": "click", "on": 1}, {"do": "click", "on": 1})
        self.assertEqual(len(self.server.engine_calls("batch")), 1)
        self.assertEqual(len(self.server.batched()), 3)

    def test_looks_first_when_a_chat_acts_without_one(self):
        self.server.act({"do": "click", "on": 1})
        self.assertEqual(self.server.engine_calls()[1][0], "snapshot")

    def test_turns_each_kind_of_step_into_its_command(self):
        self.server.look()
        self.server.act({"do": "type", "on": 2, "text": "hello"}, {"do": "set-value", "on": 2, "text": "x"},
                        {"do": "scroll", "on": 1, "direction": "up", "amount": 2}, {"do": "press", "keys": "cmd+n"})
        self.assertEqual(self.server.batched(), [
            {"command": "type", "args": {"ref_id": "@s1:e2", "text": "hello"}},
            {"command": "set-value", "args": {"ref_id": "@s1:e2", "value": "x"}},
            {"command": "scroll", "args": {"ref_id": "@s1:e1", "direction": "up", "amount": 2}},
            {"command": "press", "args": {"combo": "cmd+n", "app": "Calculator"}}])

    def test_refuses_a_number_the_window_doesnt_have(self):
        self.server.look()
        reply = self.server.act({"do": "click", "on": 1}, {"do": "click", "on": 7})
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("Step 2 failed: There is no element 7", self.server.text(reply))
        self.assertIn("The 1 before it ran", self.server.text(reply))

    def test_says_what_to_do_about_a_stale_element(self):
        self.server.look()
        original = self.server.engine_log
        reply = self.server.act({"do": "bounce", "on": 1})
        self.assertIn("Unknown step bounce", self.server.text(reply))
        self.assertTrue(original.exists())

    def test_turns_screenshot_pixels_into_a_place_on_screen(self):
        self.server.look(screenshot=True)
        reply = self.server.act({"do": "click", "xy": [100, 60]})
        self.assertFalse(reply["result"]["isError"])
        # (692 / 2 - 300) / 2 = 23 points of shadow around the window at 100,200
        self.assertEqual(self.server.batched()[0]["args"], {"xy": "127,207", "app": "Calculator"})

    def test_adds_a_screenshot_when_asked(self):
        content = self.server.look(screenshot=True)["result"]["content"]
        self.assertEqual(content[1], {"type": "image", "data": "cGljdHVyZQ==", "mimeType": "image/png"})

    def test_picks_a_window_by_title(self):
        self.server.look(app="Many", window="Two")
        self.assertEqual(self.server.engine_calls("snapshot")[0][-2:], ["--window-id", "w-2"])

    def test_takes_the_first_window_when_there_are_several(self):
        self.assertIn("button Add", self.server.text(self.server.look(app="Many")))

    def test_says_which_windows_there_are_when_a_title_matches_none(self):
        reply = self.server.look(app="Many", window="Three")
        self.assertIn('Windows: "One", "Two"', self.server.text(reply))

    def test_acts_in_a_window_picked_by_title(self):
        reply = self.server.call("act", app="Many", window="Two", steps=[{"do": "click", "on": 1}])
        self.assertFalse(reply["result"]["isError"])
        self.assertIn("+ statictext 1, Description: Display", self.server.text(reply))
        self.assertTrue(all(call[-2:] == ["--window-id", "w-2"] for call in self.server.engine_calls("snapshot")))

    def test_answers_when_ucu_itself_fails(self):
        reply = self.server.look(app="Broken")
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("ucu hit a bug", self.server.text(reply))
        self.assertIn("in render", self.server.text(reply))
        self.assertIn("button Add", self.server.text(self.server.look()))

    def test_gives_the_chat_an_engine_session_of_its_own_and_ends_it(self):
        self.server.look()
        lines = [json.loads(line) for line in self.server.engine_log.read_text().splitlines()]
        self.assertEqual(lines[0]["args"], ["session", "start"])
        self.assertEqual(lines[1]["session"], "run-1")
        self.server.process.stdin.close()
        self.server.process.wait(10)
        self.assertIn(["session", "end", "run-1"], self.server.engine_calls("session"))

    def test_says_how_to_get_the_engine_when_it_is_missing(self):
        (Path(self.server.home.name) / ".local/bin/ucu-engine").unlink()
        reply = self.server.look()
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("`ucu install`", self.server.text(reply))

    def test_tells_the_agent_about_an_alert_sound_from_its_app(self):
        self.server.look()
        self.server.alert_file.write_text(ALERT.format("Calculator"))
        time.sleep(0.2)
        self.assertIn("played an alert sound", self.server.text(self.server.act({"do": "click", "on": 1})))

    def test_ignores_other_apps_sounds(self):
        self.server.look()
        self.server.alert_file.write_text(ALERT.format("Mail"))
        time.sleep(0.2)
        self.assertNotIn("alert sound", self.server.text(self.server.act({"do": "click", "on": 1})))

    def test_runs_the_window_tools(self):
        self.server.window_helper()
        reply = self.server.call("focus_window", app="Calculator", title="Calculator")
        self.assertEqual(self.server.text(reply), "args: focus Calculator Calculator")

    def test_says_how_to_get_the_window_helper_when_it_is_missing(self):
        reply = self.server.call("list_windows")
        self.assertTrue(reply["result"]["isError"])
        self.assertIn("`ucu install`", self.server.text(reply))

    def test_exits_when_the_client_goes(self):
        self.server.process.stdin.close()
        self.assertEqual(self.server.process.wait(10), 0)


if __name__ == "__main__":
    unittest.main()
