#!/usr/bin/env python3
"""Stand-in for `osascript` running native/safari.js: logs each request to $FAKE_WINDOW_LOG and answers like Safari.

The user has window 51 with two tabs, and is viewing the second. State between runs is kept in $FAKE_SAFARI_STATE.
A URL containing "no-javascript" fails like Safari with JavaScript from Apple Events turned off.
"""
import json
import os
import sys
import time

LOG = os.environ["FAKE_WINDOW_LOG"]
STATE = os.environ["FAKE_SAFARI_STATE"]
USER_WINDOW = {"window_id": 51, "visible": True, "current_tab_index": 2, "tabs": [
    {"window_id": 51, "tab_index": 1, "url": "https://example.com/a", "title": "A"},
    {"window_id": 51, "tab_index": 2, "url": "https://example.com/b", "title": "B"}]}


def load():
    return json.load(open(STATE)) if os.path.exists(STATE) else {"windows": [USER_WINDOW]}


def answer(request, state):
    op = request["op"]
    if str(request.get("url")).find("no-javascript") >= 0:
        raise RuntimeError("You must enable 'Allow JavaScript from Apple Events' in the Develop menu")
    if op == "list":
        return state["windows"]
    if op == "create_window":
        state["windows"].append({"window_id": 900, "visible": False, "current_tab_index": 1, "tabs": []})
        return {"window_id": 900}
    if op == "open_tab":
        window = next(window for window in state["windows"] if window["window_id"] == request["window_id"])
        window["tabs"].append({"url": request["url"]})
        return {"window_id": 900, "tab_index": len(window["tabs"]), "url": request["url"], "title": "T", "loaded": True}
    if op == "close_window":
        state["windows"] = [window for window in state["windows"] if window["window_id"] != request["window_id"]]
    if op == "select_tab":
        return {"changed": False}
    return {"op": op}


def main():
    request = json.loads(sys.argv[-1])
    with open(LOG, "a") as handle:
        handle.write(json.dumps({"at": time.time(), "safari": request}) + "\n")
    state = load()
    try:
        reply = {"ok": True, "result": answer(request, state)}
    except RuntimeError as error:
        reply = {"ok": False, "error": str(error)}
    json.dump(state, open(STATE, "w"))
    print(json.dumps(reply))


if __name__ == "__main__":
    main()
