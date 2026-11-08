#!/usr/bin/env python3
"""Stand-in for agent-desktop in tests: logs its arguments and answers like the real one, without touching any app.

The window has a display and two buttons. Each click changes the display, so a look after it finds a change. A ref ending
in e99 is stale. An app called "Slow" takes a while, "Many" has two windows, and "Broken" answers with a snapshot ucu
can't read.
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
    return {"app": app, "window": {"title": app}, "tree": {
        "role": "window", "name": app, "bounds": {"x": 100.0, "y": 200.0, "width": 300.0, "height": 400.0}, "children": [
            {"role": "group", "children": [
                {"role": "statictext", "name": str(clicks()), "description": "Display"},
                {"role": "button", "name": "Add", "ref_id": "@s1:e1", "bounds": box, "available_actions": ["Click"]},
                {"role": "textfield", "name": "Note", "value": "hi", "ref_id": "@s1:e2",
                 "available_actions": ["SetValue", "SetFocus"], "states": ["focused"]}]}]}}


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
            COUNT.write_text(str(clicks() + 1))
            results.append({"ok": True, "command": entry["command"], "data": {}})
        reply({"results": results})
    else:
        reply(error={"code": "INVALID_ARGS", "message": f"unknown {command}"})


if __name__ == "__main__":
    main()
