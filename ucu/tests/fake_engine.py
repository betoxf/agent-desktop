#!/usr/bin/env python3
"""Stand-in for agent-desktop in tests: logs its arguments and answers like the real one, without touching any app.

The window has a display and two buttons. Each click changes the display, so a look after it finds a change. A ref ending
in e99 is stale, and pressing cmd+k fails as in a hidden app, which has no focused field. An app called "Slow" takes a
while, "Many" has two windows, "Twins" two buttons called Add, "Long" 400 more buttons, and "Broken" answers with a
snapshot ucu can't read. "Rebuilt" has a button called Fresh that is a new element at every read, and the one from
the first read is gone. After two clicks every window has a button called Later. Google Chrome has two windows with
the same title: the user's, and one where ucu puts the agent's on the fake desktop.
"""
import json
import os
import sys
import time
from pathlib import Path

LOG = Path(os.environ["FAKE_ENGINE_LOG"])
COUNT = LOG.with_name("clicks")


def log(entry):
    with LOG.open("a") as handle:
        handle.write(json.dumps({"at": time.time(), **entry}) + "\n")


def reply(data=None, error=None):
    print(json.dumps({"ok": error is None, "data": data, "error": error, "version": "2.5"}))


def clicks():
    return int(COUNT.read_text()) if COUNT.exists() else 0


def snapshot(app):
    box = {"x": 100.0, "y": 200.0, "width": 40.0, "height": 20.0}
    extra = []
    if app == "Twins":
        extra = [{"role": "button", "name": "Add", "ref_id": "@s1:e3", "available_actions": ["Click"]}]
    if app == "Long":
        extra = [{"role": "button", "name": f"Item {at}", "ref_id": f"@s1:e{at}"} for at in range(10, 410)]
    if app == "Rebuilt":  # its window is new at every read, and what the first read saw is gone
        reads = LOG.with_name("rebuilt")
        reads.write_text(str(int(reads.read_text()) + 1 if reads.exists() else 1))
        extra = [{"role": "button", "name": "Fresh", "ref_id": f"@s1:e{70 + int(reads.read_text())}"}]
    if clicks() >= 2:  # something that only shows after two clicks
        extra.append({"role": "button", "name": "Later", "ref_id": "@s1:e5", "available_actions": ["Click"]})
    return {"app": app, "window": {"title": app}, "tree": {
        "role": "window", "name": app, "bounds": {"x": 100.0, "y": 200.0, "width": 300.0, "height": 400.0}, "children": [
            {"role": "group", "children": [
                {"role": "statictext", "name": str(clicks()), "description": "Display"},
                {"role": "button", "name": "Add", "ref_id": "@s1:e1", "bounds": box, "available_actions": ["Click"]},
                {"role": "textfield", "name": "Note", "value": "hi", "ref_id": "@s1:e2",
                 "available_actions": ["SetValue", "SetFocus"], "states": ["focused"]}, *extra]}]}}


def main():
    arguments = sys.argv[1:]
    log({"args": arguments, "session": os.environ.get("AGENT_DESKTOP_SESSION")})
    command = arguments[0]
    if command == "session":
        reply({"activation": {"environment": "AGENT_DESKTOP_SESSION", "value": "run-1"}})
    elif command == "snapshot":
        app = arguments[arguments.index("--app") + 1]
        if "--window-id" not in arguments and app == "Many":
            reply(error={"code": "AMBIGUOUS_TARGET", "message": "Several windows match"})
        elif app == "Broken":
            reply("not a snapshot")
        else:
            reply(snapshot(app))
    elif command == "list-windows" and arguments[arguments.index("--app") + 1] == "Google Chrome":
        reply([{"id": "w-5", "title": "Mail - Google Chrome",
                "bounds": {"x": 0, "y": 33, "width": 1512, "height": 859}},
               {"id": "w-6", "title": "Mail - Google Chrome",
                "bounds": {"x": -1900, "y": -1160, "width": 1280, "height": 900}}])
    elif command == "list-windows":  # the real engine's data is the list itself
        reply([{"id": "w-1", "title": "One"}, {"id": "w-2", "title": "Two"}])
    elif command == "screenshot":
        Path(arguments[1]).write_bytes(b"picture")
        reply({"format": "png", "width": 692, "height": 892, "scale_factor": 2.0, "path": arguments[1]})
    elif command == "batch":
        results = []
        for entry in json.loads(arguments[1]):
            if str(entry["args"].get("ref_id", "")).endswith("e99"):
                results.append({"ok": False, "command": entry["command"],
                                "error": {"code": "STALE_REF", "message": "That ref is stale"}})
                break
            if entry["args"].get("ref_id") == "@s1:e71":
                results.append({"ok": False, "command": entry["command"], "error": {
                    "code": "ACTION_FAILED", "message": "Accessibility surface read failed for AXRole"}})
                break
            if entry["command"] == "press" and entry["args"].get("combo") == "cmd+k":  # like a hidden app
                results.append({"ok": False, "command": "press", "error": {
                    "code": "ACTION_FAILED", "message": "Application has no verified focused element"}})
                break
            COUNT.write_text(str(clicks() + 1))
            results.append({"ok": True, "command": entry["command"], "data": {}})
        reply({"results": results})
    else:
        reply(error={"code": "INVALID_ARGS", "message": f"unknown {command}"})


if __name__ == "__main__":
    main()
