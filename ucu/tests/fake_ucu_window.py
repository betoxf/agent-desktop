#!/usr/bin/env python3
"""Stand-in for `ucu-window watch` in tests: logs what it gets and answers like the real one.

Every command and its exit are appended as JSON to $FAKE_WINDOW_LOG, with the time. An app counts as closed if its name
starts with "Closed", as windowless if it starts with "Empty", and as denied if it is in the request's `deny`. "Safari"
is the active app. An app with "Stuck" in its name can't move to the agent's desktop. Each line added to
$FAKE_WINDOW_REPORTS goes out as a report. Run without `watch`, it logs its arguments like the other commands.
"""
import json
import os
import sys
import threading
import time

LOG = os.environ["FAKE_WINDOW_LOG"]
REPORTS = os.environ["FAKE_WINDOW_REPORTS"]
OPEN_DELAY = 0.2  # opening an app takes a while, and cua_repl must not get the call before it is done
parked = set()


def send(message):
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def relay_reports():
    with open(REPORTS) as handle:
        while True:
            line = handle.readline()
            if line:
                send(json.loads(line))
            else:
                time.sleep(0.02)


def answer(command):
    if "open" in command:
        name = command["open"]
        if name in command["deny"]:
            state = "denied"
        elif name.startswith("Closed"):
            time.sleep(OPEN_DELAY)
            state = "opened"
        elif name.startswith("Empty"):
            state = "reopened"
        else:
            state = "running"
        send({"id": command["id"], "state": state, "active": name == "Safari"})
    elif "show" in command and "id" in command:
        send({"id": command["id"]})
    elif "park" in command and "id" in command:
        stuck = any("Stuck" in name for name in command["park"])
        fresh = not set(command["park"]) <= parked
        parked.update(command["park"])
        send({"id": command["id"], "state": "no desktop" if stuck else "parked", "moved": fresh})
    elif "desktop" in command:
        send({"id": command["id"], "bounds": [-1920, -1200, 1920, 1200]})


def log(entry):
    with open(LOG, "a") as handle:
        handle.write(json.dumps({"at": time.time(), **entry}) + "\n")


def main():
    if sys.argv[1:] != ["watch"]:
        log({"args": sys.argv[1:]})
        print(f"args: {' '.join(sys.argv[1:])}")
        sys.exit(2 if "Missing" in sys.argv else 0)
    threading.Thread(target=relay_reports, daemon=True).start()
    for line in sys.stdin:
        command = json.loads(line)
        log(command)
        answer(command)
    log({"exited": True})


if __name__ == "__main__":
    main()
