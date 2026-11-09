#!/usr/bin/env python3
"""Stand-in for `osascript` running native/browser.js: logs each request to $FAKE_WINDOW_LOG and answers like Safari or
Chrome, whichever the request names.

In Safari the user has window 51 with two tabs, and is viewing the second; in Chrome, window 61 with one tab. A window
ucu makes is 900 in Safari and 901 in Chrome. State between runs is kept in $FAKE_SAFARI_STATE. A URL containing
"no-javascript" fails like the browser with JavaScript from Apple Events turned off, and while $HOME/window-moved
exists, ucu's window has left the agent's desktop.
"""
import json
import os
import sys
import time

LOG = os.environ["FAKE_WINDOW_LOG"]
STATE = os.environ["FAKE_SAFARI_STATE"]
USER_WINDOWS = {
    "safari": {"window_id": 51, "visible": True, "current_tab_index": 2, "tabs": [
        {"window_id": 51, "tab_index": 1, "url": "https://example.com/a", "title": "A"},
        {"window_id": 51, "tab_index": 2, "url": "https://example.com/b", "title": "B"}]},
    "chrome": {"window_id": 61, "visible": True, "current_tab_index": 1, "tabs": [
        {"window_id": 61, "tab_index": 1, "url": "https://mail.test", "title": "Mail"}]}}
NEW_WINDOW = {"safari": 900, "chrome": 901}
NO_JAVASCRIPT = {"safari": "You must enable 'Allow JavaScript from Apple Events' in the Develop menu",
                 "chrome": "Executing JavaScript through AppleScript is turned off. To turn it on, from the menu bar, "
                           "go to View > Developer > Allow JavaScript from Apple Events."}


def load():
    return json.load(open(STATE)) if os.path.exists(STATE) else {name: {"windows": [window]}
                                                                  for name, window in USER_WINDOWS.items()}


def answer(request, state):
    op, browser = request["op"], request["browser"]
    if str(request.get("url")).find("no-javascript") >= 0:
        raise RuntimeError(NO_JAVASCRIPT[browser])
    if op == "list":
        return state["windows"]
    if op == "create_window":
        state["windows"].append({"window_id": NEW_WINDOW[browser], "visible": False, "current_tab_index": 1,
                                 "tabs": []})
        return {"window_id": NEW_WINDOW[browser]}
    if op == "open_tab":
        window = next(window for window in state["windows"] if window["window_id"] == request["window_id"])
        window["tabs"].append({"url": request["url"]})
        return {"window_id": request["window_id"], "tab_index": len(window["tabs"]), "url": request["url"],
                "title": "T", "loaded": True}
    if op == "close_window":
        state["windows"] = [window for window in state["windows"] if window["window_id"] != request["window_id"]]
    if op == "select_tab":
        return {"changed": False}
    if op == "upload":
        return {"uploaded": [file["name"] for file in request["files"]], "into": "input"}
    if op == "hide_window" and os.path.exists(os.path.join(os.environ["HOME"], "window-moved")):
        return {"hidden": False, "moved": True}
    return {"op": op}


def main():
    request = json.loads(sys.argv[-1])
    with open(LOG, "a") as handle:
        handle.write(json.dumps({"at": time.time(), "osascript": request}) + "\n")
    state = load()
    try:
        reply = {"ok": True, "result": answer(request, state[request["browser"]])}
    except RuntimeError as error:
        reply = {"ok": False, "error": str(error)}
    json.dump(state, open(STATE, "w"))
    print(json.dumps(reply))


if __name__ == "__main__":
    main()
