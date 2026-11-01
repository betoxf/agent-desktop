"""How ucu-proxy.py keeps apps out of the way, reports new windows and shortens what agents read."""
import base64
import copy
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

ENGINE_TOOLS = [{"name": "js", "description": "Control apps.\n\nUse the first matching browser control option from the "
                                              "request:\n\nmany browser examples\n\nIf the user specifies an app, get it."},
                {"name": "js_add_node_module_dir", "description": "Add a directory."},
                {"name": "js_reset", "description": "Reset."},
                {"name": "turn_ended", "description": "Notify."}]
DOCS = """## Computer Use

## API

type AppInfo = { id: string };

type BrowserInfo = { id: string };
interface Browser { readonly browserId: string; }

type State = { apps: AppInfo[]; };

type BrowserOptions = { browser?: string };
interface Tab { readonly id: string; }

declare const cua: {
  getApp(target: string): Promise<App>;

  /** Select without opening a tab. */
  getBrowser(): Promise<Browser>;
  listTabs(): Promise<TabInfo[]>;
};

MCP App tabs support DOM-based interaction.

## Native apps

## Notes

- For browser tabs, `click` holds each press.
- `pressKey()` presses a key.
- Browser `paste` does not restore the clipboard.

"""
POLICY = "# Computer Use Confirmations Policy\n\nThe long policy.\n"


def load_proxy():
    spec = importlib.util.spec_from_file_location("ucu_proxy_under_test", ROOT / "scripts/ucu-proxy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WatchingProxy(Proxy):
    def __init__(self, test):
        super().__init__(test)
        path = Path(self.home.name) / ".local/bin/ucu-window"
        path.parent.mkdir(parents=True)
        path.write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{ROOT / "tests/fake_ucu_window.py"}" "$@"\n')
        path.chmod(0o755)
        (self.tools / "osascript").write_text(f'#!/bin/sh\nexec "{sys.executable}" -I "{ROOT / "tests/fake_osascript.py"}" "$@"\n')
        (self.tools / "screencapture").write_text('#!/bin/sh\nfor last; do :; done\nprintf picture > "$last"\n')
        (self.tools / "osascript").chmod(0o755)
        (self.tools / "screencapture").chmod(0o755)
        self.initialize()

    def safari(self, **arguments):
        if not (Path(self.home.name) / "Library/Application Support/ucu/policy.json").exists():
            self.policy(deny=[])
        return self.request("tools/call", {"name": "safari", "arguments": arguments})["result"]

    def safari_calls(self, op=None):
        return [command["safari"] for command in self.commands("safari") if op in (None, command["safari"]["op"])]

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
    def test_opens_a_closed_app_hidden_before_cua_repl_gets_the_call(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=["Terminal"])
        reply = proxy.call("js", 'let app = await cua.getApp("Closed App");')
        self.assertEqual(proxy.commands("open")[0]["deny"], ["Terminal"])
        self.assertGreater(proxy.events("recv")[-1]["at"], proxy.commands("open")[0]["at"] + 0.15)
        self.assertEqual(len(proxy.notes(reply)), 1)
        self.assertIn("Closed App was closed, so ucu opened it hidden", proxy.notes(reply)[0])

    def test_never_watches_a_denied_app(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=["Closed Secrets"])
        proxy.call("js", 'let app = await cua.getApp("Closed Secrets");')
        self.assertEqual(proxy.commands("watch"), [])

    def test_drops_a_call_cancelled_while_its_app_opens(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        proxy.send({"id": 99, "method": "tools/call", "params": {
            "name": "js", "arguments": {"code": 'let app = await cua.getApp("Closed App");'}}})
        proxy.send({"method": "notifications/cancelled", "params": {"requestId": 99}})
        proxy.wait_until(lambda: [command for command in proxy.commands("busy") if not command["busy"]])
        self.assertEqual([event for event in proxy.received("tools/call") if event["message"].get("id") == 99], [])

    def test_says_nothing_about_an_app_that_was_already_running(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        self.assertEqual(proxy.notes(proxy.call("js", 'let app = await cua.getApp("Notes");')), [])

    def test_leaves_a_denied_app_closed_and_cua_repl_refuses_it(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=["Closed Secrets"])
        reply = proxy.call("js", 'let app = await cua.getApp("Closed Secrets");')
        self.assertEqual(proxy.notes(reply), [])

    def test_opens_nothing_while_the_policy_cannot_be_read(self):
        proxy = WatchingProxy(self)
        proxy.call("js", 'let app = await cua.getApp("Closed App");')
        self.assertEqual(proxy.commands("open"), [])

    def test_shows_a_hidden_app_before_input_by_coordinates(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        proxy.call("js", 'let app = await cua.getApp("Closed App");')
        proxy.call("js", "await app.click(12);")
        self.assertEqual(proxy.commands("park"), [])
        proxy.call("js", "await app.click([100, 200]);")
        self.assertEqual(proxy.commands("park")[0]["park"], ["Closed App"])
        calls = [event["message"]["params"]["arguments"]["code"] for event in proxy.received("tools/call")]
        self.assertEqual(calls[-2:], ['await cua.getApp("Closed App");', "await app.click([100, 200]);"])
        self.assertGreater(proxy.received("tools/call")[-2]["at"], proxy.commands("park")[0]["at"])

    def test_reads_a_window_only_after_it_moved(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        proxy.call("js", 'let app = await cua.getApp("Closed App");')
        proxy.call("js", "await app.click([100, 200]);")
        count = len(proxy.received("tools/call"))
        proxy.call("js", "await app.click([100, 200]);")
        self.assertEqual(len(proxy.received("tools/call")), count + 1)

    def test_shows_a_hidden_app_behind_the_user_without_a_desktop(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        proxy.call("js", 'let app = await cua.getApp("Closed Stuck App");')
        proxy.call("js", "await app.click([100, 200]);")
        self.assertEqual(proxy.commands("show")[0]["show"], ["Closed Stuck App"])
        proxy.call("js", "await app.click([100, 200]);")
        self.assertEqual(len(proxy.commands("park")), 1)

    def test_never_moves_a_denied_app(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=["Passwords"])
        result = proxy.request("tools/call", {"name": "move_to_desktop", "arguments": {"app": "Passwords"}})["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(proxy.commands("park"), [])

    def test_leaves_an_app_the_user_took_back_alone(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        proxy.call("js", 'let app = await cua.getApp("Closed App");')
        proxy.call("js", "await app.click([100, 200]);")
        self.assertTrue(proxy.commands("park")[0]["onlyHidden"])

    def test_keeps_the_docs_for_the_agent_when_moving_an_app_first(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        proxy.request("tools/call", {"name": "move_to_desktop", "arguments": {"app": "Blender"}})
        self.assertEqual(proxy.received("tools/call"), [])

    def test_stays_busy_until_the_last_call_ends(self):
        proxy = WatchingProxy(self)
        for number in (91, 92):
            proxy.send({"id": number, "method": "tools/call", "params": {"name": "js", "arguments": {"code": "slow"}}})
        proxy.receive(), proxy.receive()
        proxy.wait_until(lambda: not proxy.commands("busy")[-1]["busy"])
        self.assertEqual([command["busy"] for command in proxy.commands("busy")], [True, True, False])

    def test_moves_an_app_to_the_desktop_when_asked(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        result = proxy.request("tools/call", {"name": "move_to_desktop", "arguments": {"app": "Blender"}})["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(proxy.commands("park")[0]["park"], ["Blender"])
        failed = proxy.request("tools/call", {"name": "move_to_desktop", "arguments": {"app": "Stuck"}})["result"]
        self.assertTrue(failed["isError"])

    def test_tells_the_agent_when_a_running_app_got_a_window(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        self.assertIn("had no open window", proxy.notes(proxy.call("js", 'let app = await cua.getApp("Empty App");'))[0])

    def test_works_on_the_window_a_call_names(self):
        proxy = WatchingProxy(self)
        proxy.request("tools/call", {"name": "js", "arguments": {
            "code": "1", "window": {"app": "TextEdit", "title": "Notes"}}})
        self.assertEqual(proxy.commands("args")[0]["args"], ["focus", "TextEdit", "Notes"])
        self.assertEqual(proxy.received("tools/call")[-1]["message"]["params"]["arguments"], {"code": "1"})
        self.assertGreater(proxy.received("tools/call")[-1]["at"], proxy.commands("args")[0]["at"])

    def test_runs_calls_for_windows_one_at_a_time(self):
        proxy = WatchingProxy(self)
        for number, title in ((91, "One"), (92, "Two")):
            proxy.send({"id": number, "method": "tools/call", "params": {"name": "js", "arguments": {
                "code": "slow", "window": {"app": "TextEdit", "title": title}}}})
        replies = [proxy.receive(), proxy.receive()]
        self.assertEqual(sorted(reply["id"] for reply in replies), [91, 92])
        first, second = proxy.commands("args")
        self.assertGreater(second["at"], first["at"] + 0.25)

    def test_keeps_going_after_a_window_that_is_not_there(self):
        proxy = WatchingProxy(self)
        reply = proxy.request("tools/call", {"name": "js", "arguments": {
            "code": "1", "window": {"app": "TextEdit", "title": "Missing"}}})
        self.assertIn("error", reply)
        again = proxy.request("tools/call", {"name": "js", "arguments": {
            "code": "1", "window": {"app": "TextEdit", "title": "Notes"}}})
        self.assertIn("result", again)

    def test_shows_a_hidden_app_when_the_engine_finds_no_window_on_screen(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        proxy.call("js", 'let app = await cua.getApp("Closed App");')
        reply = proxy.call("js", "no-windows")  # `app.click(point)`: nothing here says it uses coordinates
        self.assertIn("Closed App was hidden", proxy.notes(reply)[0])
        proxy.wait_until(lambda: proxy.commands("park"))

    def test_leaves_the_app_alone_after_the_agent_showed_it(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        proxy.call("js", 'let app = await cua.getApp("Closed App");')
        proxy.request("tools/call", {"name": "focus_window", "arguments": {
            "app": "Closed App", "title": "x", "activate": True}})
        self.assertEqual(proxy.commands("allow")[0]["allow"], "Closed App")
        proxy.call("js", "await app.click([100, 200]);")
        self.assertEqual(proxy.commands("park"), [])

    def test_shows_a_hidden_app_that_turned_an_action_down(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        proxy.call("js", 'let app = await cua.getApp("Closed App");')
        time.sleep(0.3)  # the fake log's start
        notes = proxy.notes(proxy.call("js", "alert:Closed App"))
        self.assertIn("played an alert sound", notes[0])
        self.assertIn("Closed App was hidden", notes[1])

    def test_tells_the_agent_about_a_new_window_once(self):
        proxy = WatchingProxy(self)
        proxy.call("js")
        proxy.report(event="window", app="Safari", kind="dialog", title="Sign in", text="Your password", buttons=["OK"])
        self.assertEqual(proxy.notes(proxy.call("js")), [
            '[ucu: Safari opened a dialog titled "Sign in". Its text: "Your password". Buttons: "OK". '
            "To act on it, call focus_window with its title, then cua.getApp.]"])
        self.assertEqual(proxy.notes(proxy.call("js")), [])

    def test_keeps_a_burst_of_windows_short(self):
        proxy = WatchingProxy(self)
        proxy.call("js")
        for index in range(5):
            proxy.report(event="window", app="Safari", kind="window", title=f"Tab {index}")
        notes = proxy.notes(proxy.call("js"))
        self.assertEqual(len(notes), 4)
        self.assertIn("and 2 more", notes[-1])

    def test_tells_the_agent_when_the_user_got_their_app_back(self):
        proxy = WatchingProxy(self)
        proxy.call("js")
        proxy.report(event="focus", app="Safari", back="Claude")
        self.assertIn("Safari came to the front", proxy.notes(proxy.call("js"))[0])

    def test_marks_the_time_a_call_runs_and_the_apps_it_drives(self):
        proxy = WatchingProxy(self)
        proxy.call("js", 'let app = await cua.getApp("Calculator");')
        proxy.wait_until(lambda: [command for command in proxy.commands("busy") if not command["busy"]])
        self.assertEqual(proxy.commands("watch")[0]["watch"], ["calculator"])
        self.assertEqual([command["busy"] for command in proxy.commands("busy")], [True, False])

    def test_stops_the_helper_when_cua_repl_stops(self):
        proxy = WatchingProxy(self)
        proxy.call("js")
        proxy.wait_until(lambda: proxy.commands("busy"))
        self.assertEqual(proxy.commands("exited"), [])
        proxy.end_turn()
        proxy.wait_until(lambda: proxy.commands("exited"))

    def test_works_with_a_helper_from_before_it_could_watch(self):
        proxy = Proxy(self)
        path = Path(proxy.home.name) / ".local/bin/ucu-window"
        path.parent.mkdir(parents=True)
        path.write_text('#!/bin/sh\necho "usage: ucu-window" >&2\nexit 1\n')
        path.chmod(0o755)
        proxy.policy(deny=[])
        proxy.initialize()
        reply = proxy.call("js", 'let app = await cua.getApp("Closed App");')
        self.assertFalse(any(text.startswith("[ucu:") for text in [item["text"] for item in reply["result"]["content"]]))

    def test_explains_errors_where_they_happen(self):
        proxy = WatchingProxy(self)
        self.assertIn("deny list", proxy.notes(proxy.call("js", "refuse"))[0])
        self.assertIn("xdotool names", proxy.notes(proxy.call("js", "no-key"))[0])


class SharedMacTest(unittest.TestCase):
    def other_chat(self, proxy, pid, apps, color=None):
        folder = Path(proxy.home.name) / "Library/Application Support/ucu/sessions"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{pid}.json").write_text(json.dumps({"who": "claude-code:website", "color": color, "apps": apps}))
        return folder / f"{pid}.json"

    def test_says_when_another_agent_is_using_an_app_once(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        self.other_chat(proxy, os.getpid(), {"calculator": time.time()})
        notes = proxy.notes(proxy.call("js", 'let app = await cua.getApp("Calculator");'))
        self.assertIn("Another agent (claude-code:website) is using Calculator", notes[0])
        self.assertEqual(proxy.notes(proxy.call("js", 'let app = await cua.getApp("Calculator");')), [])

    def test_survives_a_session_file_with_anything_in_it(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        path = self.other_chat(proxy, os.getpid(), {})
        for junk in ("[]", "{", '{"apps": 3}', '{"apps": {"calculator": "soon"}}', '{"color": []}'):
            path.write_text(junk)
            self.assertIn("result", proxy.call("js", 'let app = await cua.getApp("Calculator");'))

    def test_ignores_other_apps_old_use_and_chats_that_ended(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        self.other_chat(proxy, os.getpid(), {"calculator": time.time() - 1000, "safari": time.time()})
        ended = self.other_chat(proxy, 999999, {"calculator": time.time()})
        self.assertEqual(proxy.notes(proxy.call("js", 'let app = await cua.getApp("Calculator");')), [])
        self.assertFalse(ended.exists())

    def test_publishes_what_it_uses_and_cleans_up(self):
        proxy = WatchingProxy(self)
        proxy.policy(deny=[])
        proxy.call("js", 'let app = await cua.getApp("Calculator");')
        files = list((Path(proxy.home.name) / "Library/Application Support/ucu/sessions").glob("*.json"))
        self.assertIn("calculator", json.loads(files[0].read_text())["apps"])
        self.assertIn("test-client", json.loads(files[0].read_text())["who"])
        proxy.process.stdin.close()
        proxy.process.wait(10)
        self.assertFalse(files[0].exists())

    def cursor_color(self, proxy):
        proxy.policy(deny=[])
        proxy.call("js", 'let app = await cua.getApp("Calculator");')
        return proxy.commands("cursor")[0]["cursor"]

    def test_gives_the_helper_a_cursor_color_before_anything_else(self):
        proxy = WatchingProxy(self)
        color = self.cursor_color(proxy)
        self.assertRegex(color, r"^#[0-9A-F]{6}$")
        self.assertIn("cursor", json.loads(proxy.window_log.read_text().splitlines()[0]))
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
        self.assertEqual(proxy.received("tools/call"), [])  # no need for the engine

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
        self.assertIn("`safari` tool", proxy.notes(proxy.call("js", 'let app = await cua.getApp("Safari");'))[0])


class ShorterTest(unittest.TestCase):
    def setUp(self):
        self.proxy = load_proxy()

    def test_lists_windows_of_every_app_without_an_app(self):
        self.proxy.WINDOW_HELPER = Path("/bin/echo")
        self.assertEqual(self.proxy.run_window_tool("list_windows", {}), ("list", False))
        self.assertEqual(self.proxy.run_window_tool("list_windows", {"app": "Safari"}), ("list Safari", False))

    def test_hides_tools_and_browser_examples_from_the_tool_list(self):
        message = {"result": {"tools": copy.deepcopy(ENGINE_TOOLS)}}
        tools = json.loads(self.proxy.Proxy.edit_tools(message))["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools],
                         ["js", "js_reset", "list_windows", "focus_window", "move_to_desktop", "safari"])
        self.assertNotIn("many browser examples", tools[0]["description"])
        self.assertIn("cua.getState()", tools[0]["description"])
        self.assertIn("If the user specifies an app", tools[0]["description"])

    def test_adds_the_window_tools_to_the_last_page_only(self):
        message = {"result": {"tools": copy.deepcopy(ENGINE_TOOLS), "nextCursor": "2"}}
        tools = json.loads(self.proxy.Proxy.edit_tools(message))["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["js", "js_reset"])

    def test_leaves_the_browser_part_out_and_returns_it(self):
        shorter, removed = self.proxy.shrink_docs(DOCS + POLICY, keep_browser=False)
        for gone in ("BrowserInfo", "interface Tab", "listTabs", "MCP App tabs", "For browser tabs", "Browser `paste`"):
            self.assertNotIn(gone, shorter)
            self.assertIn(gone, removed)
        for kept in ("type AppInfo", "type State", "declare const cua", "getApp(target", "## Native apps", "pressKey()"):
            self.assertIn(kept, shorter)
        self.assertIn("about browsers", shorter)

    def test_keeps_the_browser_part_for_a_first_call_that_uses_it(self):
        shorter, removed = self.proxy.shrink_docs(DOCS + POLICY, keep_browser=True)
        self.assertEqual((shorter, removed), (DOCS + POLICY, ""))

    def test_summarizes_only_the_policy_the_summary_was_written_from(self):
        self.assertIn("The long policy.", self.proxy.shrink_docs(DOCS + POLICY, keep_browser=True)[0])
        self.proxy.POLICY_SHA256 = self.proxy.hashlib.sha256(POLICY.split("Policy", 1)[1].encode()).hexdigest()
        shorter = self.proxy.shrink_docs(DOCS + POLICY, keep_browser=True)[0]
        self.assertNotIn("The long policy.", shorter)
        self.assertIn(self.proxy.POLICY_SUMMARY, shorter)

    def test_gives_the_browser_part_to_the_first_call_that_needs_it(self):
        proxy = self.proxy.Proxy(None, 1, 1, 1)
        first = {"result": {"content": [{"type": "text", "text": DOCS + POLICY + "x" * 5000}]}}
        proxy.shorten_docs(first, browser=False)
        self.assertNotIn("BrowserInfo", first["result"]["content"][0]["text"])
        later = {"result": {"content": [{"type": "text", "text": "state"}]}}
        self.assertIsNone(proxy.shorten_docs(later, browser=False))
        proxy.shorten_docs(later, browser=True)
        self.assertIn("BrowserInfo", later["result"]["content"][0]["text"])
        last = {"result": {"content": [{"type": "text", "text": "state"}]}}
        self.assertIsNone(proxy.shorten_docs(last, browser=True))

    def test_keeps_the_browser_part_through_a_browsers_own_docs(self):
        proxy = self.proxy.Proxy(None, 1, 1, 1)
        proxy.shorten_docs({"result": {"content": [{"type": "text", "text": DOCS + POLICY + "x" * 5000}]}}, browser=False)
        browser = {"result": {"content": [{"type": "text", "text": "# Other Browser APIs\n" + "y" * 5000}]}}
        proxy.shorten_docs(browser, browser=True)
        self.assertIn("interface Tab", browser["result"]["content"][0]["text"])
        self.assertIn("Other Browser APIs", browser["result"]["content"][1]["text"])

    def test_gives_the_browser_part_to_a_call_that_also_gets_the_docs_again(self):
        proxy = self.proxy.Proxy(None, 1, 1, 1)
        text = DOCS + POLICY + "x" * 5000
        proxy.shorten_docs({"result": {"content": [{"type": "text", "text": text}]}}, browser=False)
        again = {"result": {"content": [{"type": "text", "text": text}]}}
        proxy.shorten_docs(again, browser=True)
        self.assertIn("BrowserInfo", again["result"]["content"][0]["text"])
        self.assertIn("left out", again["result"]["content"][1]["text"])

    def test_shortens_the_sentences_the_engine_repeats(self):
        diff = ('The following is a diff from the previous accessibility tree for Window: "Calculator" with ~ and + '
                "representing changed and added elements, respectively. Removed elements are summarized by ID range.\n"
                "~\t4 text Value: 7")
        content = [{"type": "text", "text": diff}, {"type": "image", "data": "x"},
                   {"type": "text", "text": 'There has been no change in the accessibility tree for Window: "A "B"".\nx'}]
        self.assertTrue(self.proxy.shorten_states(content))
        self.assertEqual(content[0]["text"], 'Diff of "Calculator" (~ changed, + added, removed as ID ranges):\n~\t4 text Value: 7')
        self.assertEqual(content[2]["text"], 'No change in "A "B"".\nx')
        self.assertFalse(self.proxy.shorten_states([{"type": "text", "text": 'Window: "Calculator", App: Calculator.'}]))

    def test_quotes_what_a_window_says_on_one_line(self):
        note = self.proxy.window_note({"event": "window", "app": "Safari", "kind": "window", "title": 'A "quoted" title',
                                       "text": "line one\nline two " + "x" * 400, "buttons": []})
        self.assertIn(r'titled "A \"quoted\" title"', note)
        self.assertIn('"line one line two x', note)
        self.assertNotIn("\n", note)
        self.assertLess(len(note), 500)

    def test_frees_the_window_lock_for_a_cancelled_call_that_fails(self):
        proxy = self.proxy.Proxy(None, 1, 1, 1)
        self.proxy.run_window_tool = lambda *_: ("focused", False)
        proxy.client_requests.add("1")
        self.assertTrue(proxy.start_window_call({}, "1"))
        proxy.client_requests.discard("1")  # cancelled
        proxy.fail_own_request("1", "gone")
        self.assertTrue(proxy.window_turn.acquire(blocking=False))

    def test_says_how_to_reach_each_kind_of_window(self):
        sheet = self.proxy.window_note({"event": "window", "app": "Notes", "kind": "sheet"})
        system = self.proxy.window_note({"event": "window", "app": "SecurityAgent", "kind": "system dialog"})
        main = self.proxy.window_note({"event": "window", "app": "Notes", "kind": "window", "main": True})
        self.assertIn("getAXState shows it", sheet)
        self.assertIn("may need the user", system)
        self.assertIn("main window now", main)


if __name__ == "__main__":
    unittest.main()
