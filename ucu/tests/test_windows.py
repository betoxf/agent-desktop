"""How ucu-proxy.py keeps apps out of the way, reports new windows and shares the Mac with other chats."""
import base64
import importlib.util
import json
import os
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))
from test_proxy import Proxy  # noqa: E402


def load_proxy():
    spec = importlib.util.spec_from_file_location("ucu_proxy_under_test", ROOT / "scripts/ucu-proxy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WatchingProxy(Proxy):
    def __init__(self, test):
        super().__init__(test)
        path = Path(self.home.name) / ".local/bin/ucu-window"
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{ROOT / "tests/fake_ucu_window.py"}" "$@"\n')
        path.chmod(0o755)
        (self.tools / "osascript").write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{ROOT / "tests/fake_osascript.py"}" "$@"\n')
        (self.tools / "screencapture").write_text(
            '#!/bin/sh\necho "$@" > "$HOME/screencapture.args"\nfor last; do :; done\nprintf picture > "$last"\n')
        (self.tools / "osascript").chmod(0o755)
        (self.tools / "screencapture").chmod(0o755)
        self.initialize()

    def safari(self, **arguments):
        return self.browser("safari", **arguments)

    def chrome(self, **arguments):
        return self.browser("chrome", **arguments)

    def browser(self, tool, **arguments):
        if not (Path(self.home.name) / "Library/Application Support/ucu/policy.json").exists():
            self.policy(deny=[])
        return self.request("tools/call", {"name": tool, "arguments": arguments})["result"]

    def safari_calls(self, op=None):
        return self.browser_calls("safari", op)

    def chrome_calls(self, op=None):
        return self.browser_calls("chrome", op)

    def browser_calls(self, browser, op=None):
        return [command["osascript"] for command in self.commands("osascript")
                if command["osascript"]["browser"] == browser and op in (None, command["osascript"]["op"])]

    def screencapture_args(self):
        return (Path(self.home.name) / "screencapture.args").read_text().split()

    def report(self, **fields):
        with self.window_reports.open("a") as handle:
            handle.write(json.dumps(fields) + "\n")
        time.sleep(0.2)  # the fake's turn

    def commands(self, kind):
        lines = self.window_log.read_text().splitlines() if self.window_log.exists() else []
        return [command for command in map(json.loads, lines) if kind in command]

    def texts(self, reply):
        return [item["text"] for item in reply["result"]["content"]]

    def notes(self, reply):
        return [text for text in self.texts(reply) if text.startswith("[ucu:")]


class WatchTest(unittest.TestCase):
    def test_opens_a_closed_app_hidden_before_reading_it(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=["Terminal"])
        reply = proxy.look("Closed App")
        self.assertEqual(proxy.commands("open")[0]["deny"], ["Terminal"])
        self.assertIn("Closed App was closed, so ucu opened it on your private desktop", proxy.notes(reply)[0])
        self.assertEqual((proxy.commands("park")[0]["park"], proxy.commands("park")[0]["onlyHidden"]),
                         (["Closed App"], True))

    def test_keeps_an_app_it_opens_hidden_without_a_desktop(self):
        proxy = WatchingProxy(self)
        self.assertIn("Closed Stuck was closed, so ucu opened it hidden", proxy.notes(proxy.look("Closed Stuck"))[0])

    def test_runs_a_shortcut_through_the_menu(self):
        proxy = WatchingProxy(self)
        proxy.look()
        result = proxy.act({"do": "click", "on": 1}, {"do": "press", "keys": "cmd+k"}, {"do": "click", "on": 1})
        self.assertFalse(result["result"]["isError"], proxy.text(result))
        self.assertEqual(proxy.commands("args")[0]["args"], ["press", "Calculator", "cmd+k"])
        self.assertEqual([entry["command"] for entry in proxy.batched()], ["click", "click"])

    def test_chooses_a_menu_command_by_its_path(self):
        proxy = WatchingProxy(self)
        proxy.look()
        result = proxy.act({"do": "menu", "text": "File > New Window"}, {"do": "click", "on": 1})
        self.assertFalse(result["result"]["isError"], proxy.text(result))
        self.assertEqual(proxy.commands("args")[0]["args"], ["menu", "Calculator", "File > New Window"])
        self.assertEqual([entry["command"] for entry in proxy.batched()], ["click"])

    def test_passes_on_what_a_menu_holds_when_the_path_is_wrong(self):
        proxy = WatchingProxy(self)
        proxy.look("Missing")
        result = proxy.act({"do": "menu", "text": "File > Nope"}, app="Missing")
        self.assertTrue(result["result"]["isError"])
        self.assertIn("Step 1 failed: args: menu Missing File > Nope", proxy.text(result))

    def test_sends_keys_for_a_shortcut_no_menu_has(self):
        proxy = WatchingProxy(self)
        proxy.look("Missing")
        result = proxy.act({"do": "press", "keys": "cmd+j"}, app="Missing")
        self.assertFalse(result["result"]["isError"], proxy.text(result))
        self.assertEqual(proxy.batched()[0], {"command": "press", "args": {"combo": "cmd+j", "app": "Missing"}})

    def test_says_when_neither_the_menu_nor_keys_take_a_shortcut(self):
        proxy = WatchingProxy(self)
        proxy.look("Missing")
        result = proxy.act({"do": "press", "keys": "cmd+k"}, app="Missing")
        self.assertTrue(result["result"]["isError"])
        self.assertIn("Step 1 failed", proxy.text(result))

    def test_never_watches_or_opens_a_denied_app(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=["Closed Secrets"])
        proxy.look("Closed Secrets")
        self.assertEqual(proxy.commands("watch"), [])
        self.assertEqual(proxy.commands("open"), [])

    def test_says_nothing_about_an_app_that_was_already_running(self):
        proxy = WatchingProxy(self)
        self.assertEqual(proxy.notes(proxy.look("Calculator")), [])

    def test_opens_nothing_while_the_policy_cannot_be_read(self):
        proxy = WatchingProxy(self)
        (Path(proxy.home.name) / "Library/Application Support/ucu/policy.json").unlink()
        proxy.look("Closed App")
        self.assertEqual(proxy.commands("open"), [])

    def test_moves_a_hidden_app_to_the_desktop_before_a_click_by_position(self):
        proxy = WatchingProxy(self)
        proxy.look("Closed App")
        proxy.act({"do": "click", "xy": [10, 10]}, app="Closed App")
        self.assertEqual(proxy.commands("park")[0]["park"], ["Closed App"])
        self.assertTrue(proxy.commands("park")[0]["onlyHidden"])
        self.assertEqual(proxy.batched()[0]["command"], "mouse-click")

    def test_shows_a_hidden_app_behind_the_user_without_a_desktop(self):
        proxy = WatchingProxy(self)
        proxy.look("Closed Stuck")
        proxy.act({"do": "click", "xy": [10, 10]}, app="Closed Stuck")
        self.assertEqual(proxy.commands("show")[0]["show"], ["Closed Stuck"])

    def test_leaves_alone_an_app_the_agent_never_hid(self):
        proxy = WatchingProxy(self)
        proxy.look("Calculator")
        proxy.act({"do": "click", "xy": [10, 10]})
        self.assertEqual(proxy.commands("park"), [])

    def test_moves_an_app_to_the_desktop_when_asked(self):
        proxy = WatchingProxy(self)
        self.assertIn("private desktop", proxy.text(proxy.call("move_to_desktop", app="Calculator")))
        self.assertEqual(proxy.commands("park")[0]["park"], ["Calculator"])

    def test_tells_the_agent_when_a_running_app_got_a_window(self):
        proxy = WatchingProxy(self)
        self.assertIn("had no open window", proxy.notes(proxy.look("Empty App"))[0])

    def test_leaves_the_app_alone_after_the_agent_showed_it(self):
        proxy = WatchingProxy(self)
        proxy.look("Closed App")
        proxy.call("focus_window", app="Closed App", title="x", activate=True)
        self.assertEqual(proxy.commands("allow")[0]["allow"], "Closed App")

    def test_tells_the_agent_about_a_new_window_once(self):
        proxy = WatchingProxy(self)
        proxy.look()
        proxy.report(event="window", app="Calculator", kind="window", title="About", text="", buttons=[], main=False)
        self.assertIn('Calculator opened a window titled "About"', proxy.notes(proxy.look())[0])
        self.assertEqual(proxy.notes(proxy.look()), [])

    def test_keeps_a_burst_of_windows_short(self):
        proxy = WatchingProxy(self)
        proxy.look()
        for index in range(6):
            proxy.report(event="window", app="Mail", kind="window", title=str(index), text="", buttons=[], main=False)
        notes = proxy.notes(proxy.look())
        self.assertEqual(len(notes), 4)
        self.assertIn("and 3 more", notes[3])

    def test_tells_the_agent_when_the_user_got_their_app_back(self):
        proxy = WatchingProxy(self)
        proxy.look()
        proxy.report(event="focus", app="Blender", back="Mail")
        self.assertIn("Blender came to the front", proxy.notes(proxy.look())[0])

    def test_marks_the_time_a_call_runs_and_the_apps_it_drives(self):
        proxy = WatchingProxy(self)
        proxy.look()
        self.assertEqual([command["busy"] for command in proxy.commands("busy")], [True, False])
        self.assertEqual(proxy.commands("watch")[0]["watch"], ["calculator"])

    def test_moves_the_cursor_to_each_element(self):
        proxy = WatchingProxy(self)
        proxy.look()
        proxy.act({"do": "click", "on": 1})
        proxy.wait_until(lambda: proxy.commands("cursorTo"))
        self.assertEqual(proxy.commands("cursorTo")[0]["cursorTo"], [120.0, 210.0])

    def test_stops_the_helper_after_the_turn_ends(self):
        proxy = WatchingProxy(self)
        proxy.look()
        proxy.end_turn()
        proxy.wait_until(lambda: proxy.commands("exited"))
        self.assertEqual(len(proxy.commands("rest")), 1)

    def test_starts_the_helper_again_on_the_next_turn(self):
        proxy = WatchingProxy(self)
        proxy.look()
        proxy.end_turn()
        proxy.wait_until(lambda: proxy.commands("exited"))
        proxy.look()
        self.assertEqual(len(proxy.commands("cursor")), 2)

    def test_keeps_the_helper_while_an_app_it_opened_runs(self):
        proxy = WatchingProxy(self)
        proxy.look("Closed App")
        proxy.end_turn()
        proxy.wait_until(lambda: proxy.commands("rest"))
        time.sleep(6)  # the server checks every 5 seconds: it must not ask again, nor close the helper
        self.assertEqual((len(proxy.commands("rest")), proxy.commands("exited")), (1, []))
        proxy.look()  # the next turn works with the same helper, and can rest it again
        self.assertEqual(len(proxy.commands("cursor")), 1)
        proxy.end_turn()
        proxy.wait_until(lambda: len(proxy.commands("rest")) == 2)
        proxy.process.stdin.close()  # the chat ends
        proxy.process.wait(10)
        proxy.wait_until(lambda: proxy.commands("exited"))

    def test_works_without_the_helper(self):
        proxy = Proxy(self)
        proxy.initialize()
        self.assertFalse(proxy.look()["result"]["isError"])


class SharedMacTest(unittest.TestCase):
    def other_chat(self, proxy, pid, apps, color=None):
        folder = Path(proxy.home.name) / "Library/Application Support/ucu/sessions"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{pid}.json").write_text(json.dumps({"who": "claude-code:website", "color": color, "apps": apps}))
        return folder / f"{pid}.json"

    def test_says_when_another_agent_is_using_an_app_once(self):
        proxy = WatchingProxy(self)
        self.other_chat(proxy, os.getpid(), {"calculator": time.time()})
        notes = proxy.notes(proxy.look())
        self.assertIn("Another agent (claude-code:website) is using Calculator", notes[0])
        self.assertEqual(proxy.notes(proxy.look()), [])

    def test_survives_a_session_file_with_anything_in_it(self):
        proxy = WatchingProxy(self)
        path = self.other_chat(proxy, os.getpid(), {})
        for junk in ("[]", "{", '{"apps": 3}', '{"apps": {"calculator": "soon"}}', '{"color": []}'):
            path.write_text(junk)
            self.assertIn("result", proxy.look())

    def test_ignores_other_apps_old_use_and_chats_that_ended(self):
        proxy = WatchingProxy(self)
        self.other_chat(proxy, os.getpid(), {"calculator": time.time() - 1000, "safari": time.time()})
        ended = self.other_chat(proxy, 999999, {"calculator": time.time()})
        self.assertEqual(proxy.notes(proxy.look()), [])
        self.assertFalse(ended.exists())

    def test_publishes_what_it_uses_and_cleans_up(self):
        proxy = WatchingProxy(self)
        proxy.look()
        files = list((Path(proxy.home.name) / "Library/Application Support/ucu/sessions").glob("*.json"))
        self.assertIn("calculator", json.loads(files[0].read_text())["apps"])
        self.assertIn("test-client", json.loads(files[0].read_text())["who"])
        proxy.process.stdin.close()
        proxy.process.wait(10)
        self.assertFalse(files[0].exists())

    def cursor_color(self, proxy):
        proxy.look()
        return proxy.commands("cursor")[0]["cursor"]

    def test_gives_the_helper_a_cursor_color_before_anything_else(self):
        proxy = WatchingProxy(self)
        color = self.cursor_color(proxy)
        self.assertRegex(color, r"^#[0-9A-F]{6}$")
        first = json.loads(proxy.window_log.read_text().splitlines()[0])
        self.assertIn("cursor", first)
        self.assertEqual(first["who"], "test-client:My-Project")
        files = list((Path(proxy.home.name) / "Library/Application Support/ucu/sessions").glob("*.json"))
        self.assertEqual(json.loads(files[0].read_text())["color"], color)

    def test_a_chat_doesnt_take_a_color_another_chat_has(self):
        taken = self.cursor_color(WatchingProxy(self))
        proxy = WatchingProxy(self)
        self.other_chat(proxy, os.getpid(), {}, color=taken)
        self.assertNotEqual(self.cursor_color(proxy), taken)

    def test_tells_the_helper_when_the_turn_ends(self):
        proxy = WatchingProxy(self)
        self.cursor_color(proxy)
        proxy.end_turn()
        deadline = time.time() + 7
        while not proxy.commands("turnEnded") and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(proxy.commands("turnEnded"))


class SafariTest(unittest.TestCase):
    def test_opens_tabs_in_one_window_of_its_own(self):
        proxy = WatchingProxy(self)
        first = json.loads(proxy.safari(op="open", url="https://a.test")["content"][0]["text"])
        proxy.safari(op="open", url="https://b.test")
        self.assertEqual(first["window_id"], 900)
        self.assertEqual(len(proxy.safari_calls("create_window")), 1)
        self.assertEqual([call["reuse_blank"] for call in proxy.safari_calls("open_tab")], [True, False])
        self.assertEqual(proxy.engine_calls(), [])  # no need for the engine

    def test_acts_on_its_last_tab_by_default(self):
        proxy = WatchingProxy(self)
        proxy.safari(op="open", url="https://a.test")
        proxy.safari(op="read", what="links")
        call = proxy.safari_calls("read")[0]
        self.assertEqual((call["window_id"], call["tab_index"], call["what"]), (900, 1, "links"))

    def test_stays_out_of_safari_when_it_is_denied(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=["Safari"])
        result = proxy.safari(op="list")
        self.assertTrue(result["isError"])
        self.assertIn("deny list", result["content"][0]["text"])
        self.assertEqual(proxy.safari_calls(), [])

    def test_marks_the_window_it_makes(self):
        proxy = WatchingProxy(self)
        proxy.safari(op="open", url="https://a.test")
        self.assertRegex(proxy.safari_calls("create_window")[0]["mark"], "^[0-9a-f]{32}$")

    def test_needs_a_tab_first(self):
        proxy = WatchingProxy(self)
        result = proxy.safari(op="read")
        self.assertTrue(result["isError"])
        self.assertIn("call `open` first", result["content"][0]["text"])

    def test_shows_every_window_and_marks_its_own(self):
        proxy = WatchingProxy(self)
        proxy.safari(op="open", url="https://a.test")
        windows = json.loads(proxy.safari(op="list")["content"][0]["text"])
        self.assertEqual([(window["window_id"], window["yours"]) for window in windows], [(51, False), (900, True)])

    def test_reads_the_users_tabs_but_changes_them_only_when_told(self):
        proxy = WatchingProxy(self)
        self.assertFalse(proxy.safari(op="read", window_id=51, tab_index=1)["isError"])
        refused = proxy.safari(op="click", window_id=51, tab_index=1, selector="a")
        self.assertIn("the user's", refused["content"][0]["text"])
        self.assertFalse(proxy.safari(op="click", window_id=51, tab_index=1, selector="a", allow_user_tab=True)["isError"])
        viewing = proxy.safari(op="click", window_id=51, tab_index=2, selector="a", allow_user_tab=True)
        self.assertIn("is viewing", viewing["content"][0]["text"])
        self.assertFalse(proxy.safari(op="click", window_id=51, tab_index=2, selector="a", allow_user_tab=True,
                                      allow_current_tab=True)["isError"])
        self.assertEqual(len(proxy.safari_calls("click")), 2)

    def test_takes_a_screenshot_on_the_agents_desktop_and_hides_after_the_turn(self):
        proxy = WatchingProxy(self)
        proxy.safari(op="open", url="https://a.test")
        image = proxy.safari(op="shot")["content"][0]
        self.assertEqual((image["type"], image["mimeType"], base64.b64decode(image["data"])),
                         ("image", "image/jpeg", b"picture"))
        self.assertEqual(proxy.safari_calls("show_window")[0]["bounds"]["x"], -1900)
        proxy.safari(op="shot")
        self.assertEqual(len(proxy.safari_calls("show_window")), 1)
        proxy.end_turn()
        proxy.wait_until(lambda: proxy.safari_calls("hide_window"))

    def test_captures_only_its_own_window(self):
        proxy = WatchingProxy(self)
        self.assertTrue(proxy.safari(op="shot", window_id=51, tab_index=2)["isError"])

    def test_closes_its_window_when_the_chat_ends(self):
        proxy = WatchingProxy(self)
        proxy.safari(op="open", url="https://a.test")
        proxy.process.stdin.close()
        proxy.process.wait(10)
        self.assertEqual(proxy.safari_calls("close_window")[0]["window_id"], 900)

    def test_says_how_to_turn_on_what_safari_needs(self):
        proxy = WatchingProxy(self)
        result = proxy.safari(op="open", url="https://no-javascript.test")
        self.assertTrue(result["isError"])
        self.assertIn("Safari > Develop > Allow JavaScript from Apple Events", result["content"][0]["text"])

    def test_points_to_the_tool_when_the_user_is_in_safari(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        self.assertIn("`safari` tool", proxy.notes(proxy.look("Safari"))[0])


    def test_puts_files_in_a_page_without_its_picker(self):
        proxy = WatchingProxy(self)
        report = Path(proxy.home.name) / "My Report.pdf"
        report.write_bytes(b"%PDF-1.4")
        proxy.safari(op="open", url="https://a.test")
        result = proxy.safari(op="upload", selector="#file", paths=[str(report)])
        self.assertEqual(json.loads(result["content"][0]["text"])["uploaded"], ["My Report.pdf"])
        call = proxy.safari_calls("upload")[0]
        self.assertEqual(call["files"], [{"path": str(report), "name": "My Report.pdf", "type": "application/pdf"}])
        self.assertEqual((call["window_id"], call["tab_index"], call["selector"]), (900, 1, "#file"))
        self.assertNotIn("paths", call)

    def test_uploads_only_files_it_can_find(self):
        proxy = WatchingProxy(self)
        proxy.safari(op="open", url="https://a.test")
        for paths in (["notes.txt"], [str(Path(proxy.home.name) / "missing.txt")], [proxy.home.name], []):
            result = proxy.safari(op="upload", paths=paths)
            self.assertTrue(result["isError"], paths)
        self.assertEqual(proxy.safari_calls("upload"), [])

    def test_uploads_to_a_tab_of_the_users_only_when_told(self):
        proxy = WatchingProxy(self)
        report = Path(proxy.home.name) / "report.csv"
        report.write_text("a,b")
        refused = proxy.safari(op="upload", window_id=51, tab_index=1, paths=[str(report)])
        self.assertIn("the user's", refused["content"][0]["text"])
        self.assertEqual(proxy.safari_calls("upload"), [])


class ChromeTest(unittest.TestCase):
    def test_works_in_a_window_of_its_own_on_the_agents_desktop(self):
        proxy = WatchingProxy(self)
        opened = json.loads(proxy.chrome(op="open", url="https://a.test")["content"][0]["text"])
        proxy.chrome(op="read", what="text")
        self.assertEqual(opened["window_id"], 901)
        made = proxy.chrome_calls("create_window")[0]
        self.assertEqual(made["bounds"], {"x": -1900, "y": -1160, "width": 1280, "height": 900})
        self.assertEqual(proxy.chrome_calls("read")[0]["window_id"], 901)
        self.assertIn("google chrome", [name for command in proxy.commands("watch") for name in command["watch"]])
        self.assertEqual(proxy.safari_calls(), [])

    def test_hides_its_window_after_the_turn_and_brings_it_back_for_the_next(self):
        proxy = WatchingProxy(self)
        proxy.chrome(op="open", url="https://a.test")
        self.assertEqual(proxy.chrome_calls("show_window"), [])
        proxy.end_turn()
        proxy.wait_until(lambda: proxy.chrome_calls("hide_window"))
        proxy.chrome(op="read")
        self.assertEqual(proxy.chrome_calls("show_window")[0]["bounds"]["x"], -1900)

    def test_closes_its_window_when_the_chat_ends(self):
        proxy = WatchingProxy(self)
        proxy.chrome(op="open", url="https://a.test")
        proxy.process.stdin.close()
        proxy.process.wait(10)
        self.assertEqual(proxy.chrome_calls("close_window")[0]["window_id"], 901)

    def test_captures_its_own_window_where_it_put_it(self):
        proxy = WatchingProxy(self)
        proxy.chrome(op="open", url="https://a.test")
        image = proxy.chrome(op="shot")["content"][0]
        self.assertEqual(base64.b64decode(image["data"]), b"picture")
        self.assertIn("-l6", proxy.screencapture_args())

    def test_picks_its_own_window_when_the_user_has_the_same_page_open(self):
        proxy = WatchingProxy(self)
        proxy.chrome(op="open", url="https://mail.test")
        proxy.look("Google Chrome", window="Mail")
        snapshot = proxy.engine_calls("snapshot")[0]
        self.assertEqual(snapshot[snapshot.index("--window-id") + 1], "w-6")

    def test_says_how_to_turn_on_what_chrome_needs(self):
        proxy = WatchingProxy(self)
        result = proxy.chrome(op="open", url="https://no-javascript.test")
        self.assertTrue(result["isError"])
        self.assertIn("ask them to turn on View > Developer > Allow JavaScript from Apple Events, in Chrome",
                      result["content"][0]["text"])

    def test_points_to_the_tool_when_the_user_is_in_chrome(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        self.assertIn("`chrome` tool", proxy.notes(proxy.look("Google Chrome"))[0])
        self.assertEqual(proxy.notes(proxy.look("Google Chrome", window="Mail")), [])

    def test_keeps_the_users_browser_windows_where_they_are(self):
        proxy = WatchingProxy(self)
        for app in ("Google Chrome", "Safari"):
            result = proxy.call("move_to_desktop", app=app)
            self.assertTrue(result["result"]["isError"])
            self.assertIn("tool", proxy.text(result))
        self.assertEqual(proxy.commands("park"), [])

    def test_stays_out_of_chrome_when_it_is_denied(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=["Google Chrome"])
        self.assertTrue(proxy.chrome(op="open", url="https://a.test")["isError"])
        self.assertEqual(proxy.chrome_calls(), [])

    def test_has_the_helper_undo_the_focus_its_own_steps_take(self):
        proxy = WatchingProxy(self)
        proxy.chrome(op="open", url="https://a.test")
        self.assertIn("Google Chrome", [command["ours"] for command in proxy.commands("ours")])

    def test_still_tells_the_agent_about_a_window_its_page_opens(self):
        proxy = WatchingProxy(self)
        proxy.chrome(op="open", url="https://a.test")
        time.sleep(3.1)  # long after its own window was made
        proxy.chrome(op="navigate", url="https://b.test")
        proxy.report(event="focus", app="Google Chrome", back="Mail")
        proxy.report(event="window", app="Google Chrome", kind="window", title="Sign in", text="", buttons=[],
                     main=False)
        notes = proxy.notes(proxy.look())
        self.assertEqual(len(notes), 1)
        self.assertIn("Sign in", notes[0])

    def test_brings_its_tab_to_the_user_who_asked_and_lets_them_keep_it(self):
        proxy = WatchingProxy(self)
        proxy.chrome(op="open", url="https://a.test")
        self.assertFalse(proxy.chrome(op="show")["isError"])
        self.assertEqual(proxy.chrome_calls("show_to_user")[0]["window_id"], 901)
        self.assertIn("Google Chrome", [command["allow"] for command in proxy.commands("allow")])
        self.assertIn("call `open` first", proxy.chrome(op="read")["content"][0]["text"])
        proxy.process.stdin.close()
        proxy.process.wait(10)
        self.assertEqual(proxy.chrome_calls("close_window"), [])  # the user's window now

    def test_shows_only_its_own_tab(self):
        proxy = WatchingProxy(self)
        self.assertTrue(proxy.chrome(op="show", window_id=61, tab_index=1)["isError"])
        self.assertEqual(proxy.chrome_calls("show_to_user"), [])

    def test_leaves_its_window_with_the_user_once_it_left_the_desktop(self):
        proxy = WatchingProxy(self)
        proxy.chrome(op="open", url="https://a.test")
        (Path(proxy.home.name) / "window-moved").touch()
        proxy.end_turn()
        proxy.wait_until(lambda: proxy.chrome_calls("hide_window"))
        self.assertEqual(proxy.chrome_calls("hide_window")[0]["only_at"]["x"], -1900)
        proxy.wait_until(lambda: "call `open` first" in proxy.chrome(op="read")["content"][0]["text"])

    def test_does_several_operations_in_one_call(self):
        proxy = WatchingProxy(self)
        result = proxy.chrome(steps=[{"op": "open", "url": "https://a.test"},
                                     {"op": "type", "selector": "#q", "text": "x"},
                                     {"op": "read", "what": "interactive"}])
        said = result["content"][0]["text"].splitlines()
        self.assertFalse(result["isError"])
        self.assertEqual([line.split(":")[0] for line in said], ["1 open", "2 type", "3 read"])
        self.assertIn('c1 a \\"Docs\\" -> /docs\\nc2 input[file]', said[2])
        self.assertEqual([call["op"] for call in proxy.chrome_calls()][-3:], ["open_tab", "type", "read"])

    def test_stops_at_the_operation_that_fails_and_says_what_ran(self):
        proxy = WatchingProxy(self)
        result = proxy.chrome(steps=[{"op": "open", "url": "https://a.test"},
                                     {"op": "click", "window_id": 61, "tab_index": 1, "selector": "a"},
                                     {"op": "read"}])
        said = result["content"][0]["text"]
        self.assertTrue(result["isError"])
        self.assertIn("1 open: ", said)
        self.assertIn("Step 2 failed: That tab is the user's", said)
        self.assertEqual(proxy.chrome_calls("read"), [])

    def test_doesnt_tell_the_agent_about_the_window_it_just_made(self):
        proxy = WatchingProxy(self)
        proxy.chrome(op="open", url="https://a.test")
        proxy.report(event="focus", app="Google Chrome", back="Mail")
        proxy.report(event="window", app="Google Chrome", kind="window", title="New Tab", text="", buttons=[],
                     main=False)
        self.assertEqual(proxy.notes(proxy.look()), [])


class FocusNoteTest(unittest.TestCase):
    def test_tells_the_agent_when_the_user_got_their_window_back(self):
        proxy = WatchingProxy(self)
        proxy.look()
        proxy.report(event="focus", app="Google Chrome", back="Inbox", window=True)
        note = proxy.notes(proxy.look())[0]
        self.assertIn("Google Chrome gave one of your windows the user's keyboard", note)


class WindowNoteTest(unittest.TestCase):
    def setUp(self):
        self.proxy = load_proxy()

    def test_lists_windows_of_every_app_without_an_app(self):
        self.proxy.WINDOW_HELPER = Path("/bin/echo")
        self.assertEqual(self.proxy.run_window_tool("list_windows", {}), ("list", False))
        self.assertEqual(self.proxy.run_window_tool("list_windows", {"app": "Safari"}), ("list Safari", False))

    def test_quotes_what_a_window_says_on_one_line(self):
        note = self.proxy.window_note({"event": "window", "app": "Safari", "kind": "window", "title": 'A "quoted" title',
                                       "text": "line one\nline two " + "x" * 400, "buttons": []})
        self.assertIn(r'titled "A \"quoted\" title"', note)
        self.assertIn('"line one line two x', note)
        self.assertNotIn("\n", note)
        self.assertLess(len(note), 500)

    def test_says_how_to_reach_each_kind_of_window(self):
        sheet = self.proxy.window_note({"event": "window", "app": "Notes", "kind": "sheet"})
        system = self.proxy.window_note({"event": "window", "app": "SecurityAgent", "kind": "system dialog"})
        main = self.proxy.window_note({"event": "window", "app": "Notes", "kind": "window", "main": True})
        self.assertIn("look shows it", sheet)
        self.assertIn("may need the user", system)
        self.assertIn("main window now", main)


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.proxy = load_proxy()

    def test_skips_containers_with_nothing_to_say(self):
        lines, _refs, _points, labels = self.proxy.render({"tree": {"role": "group", "children": [
            {"role": "button", "name": "OK", "ref_id": "@s:e3"}]}})
        self.assertEqual(lines, ["3 button OK"])
        self.assertEqual(labels, {3: ("ok", "3 button OK")})

    def test_leaves_out_what_only_repeats_the_element_it_is_in(self):
        lines = self.proxy.render({"tree": {"role": "webarea", "name": "Page", "children": [
            {"role": "link", "name": "Gmail ", "ref_id": "@s:e8", "available_actions": ["ScrollTo"], "children": [
                {"role": "statictext", "name": "Gmail", "available_actions": ["ScrollTo"]}]},
            {"role": "group", "name": "Total", "children": [{"role": "statictext", "name": "42"}]}]}})[0]
        self.assertEqual(lines, ["webarea Page", "\t8 link Gmail ", "\tgroup Total", "\t\tstatictext 42"])

    def test_keeps_the_lines_a_found_line_is_inside_of(self):
        lines = ["window W", "\tgroup A", "\t\t1 button Save", "\tgroup B", "\t\t2 button Open", "\t3 button Save as"]
        self.assertEqual(self.proxy.containing(lines, "save"),
                         ["window W", "\tgroup A", "\t\t1 button Save", "\t3 button Save as"])

    def test_writes_a_pages_elements_as_short_lines(self):
        lines = self.proxy.interactive_lines([
            {"ref": "c1", "tag": "a", "text": "Docs", "href": "https://a.test/docs"},
            {"ref": "c2", "tag": "input", "type": "file", "text": ""}], "https://a.test/start")
        self.assertEqual(lines, 'c1 a "Docs" -> /docs\nc2 input[file] ""')

    def test_diffs_keep_numbers_current(self):
        said = self.proxy.changes("Calc", ["1 a", "2 b", "3 c"], ["1 a", "2 b", "3 d"])
        self.assertIn("+ 3 d", said)
        self.assertIn("- 3 c", said)

    def test_shows_the_whole_window_when_most_of_it_changed(self):
        self.assertIsNone(self.proxy.changes("Calc", ["1 a", "2 b"], ["1 x", "2 y"]))


if __name__ == "__main__":
    unittest.main()
