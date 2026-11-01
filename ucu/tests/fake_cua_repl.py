#!/usr/bin/env python3
"""Stand-in for cua_repl in tests: a line-delimited JSON-RPC MCP server that logs what it gets.

Its start, every line it receives and its exit are appended as JSON to $FAKE_LOG (when set).
The `js` tool understands these `code` values: "crash" exits without answering, "elicit" (or
"elicit:<app>") asks the client to approve Calculator (or <app>) and returns the answer, and
"alert:<app>", "alert-late:<app>" and "other-sound:<app>" write a sound to $FAKE_ALERTS for the fake
`log` command: an alert during the call, an alert just after it, or a sound that isn't an alert.
"no-windows", "refuse" and "no-key" return the engine's errors for a window it can't find, an app it may not use and
a key it doesn't know, and "slow" takes a moment.
Any other call returns its `_meta`.
The first `js` result of each process starts with a long documentation block, like cua_repl's.
"""
import json
import os
import sys
import time

LOG = os.environ.get("FAKE_LOG")
ALERTS = os.environ.get("FAKE_ALERTS")
DOCS = "# cua_repl documentation\n" + "Use cua.getApp(name) to get an app.\n" * 200
TOOLS = [{"name": name, "inputSchema": {"type": "object"}} for name in ("js", "js_reset")]


def log(event, **fields):
    if LOG:
        with open(LOG, "a") as handle:
            handle.write(json.dumps({"event": event, "pid": os.getpid(), "at": time.time(), **fields}) + "\n")


def send(message):
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    sys.stdout.flush()


def sound(app, action_id=4096):
    with open(ALERTS, "a") as handle:
        handle.write(f"-> Incoming Request : actionID {action_id}, inClientPID 7({app}), inBehavior 1\n")


def texts(request_id, *items):
    send({"id": request_id, "result": {"content": [{"type": "text", "text": item} for item in items]}})


def main():
    log("start", surfaces=os.environ.get("CUA_REPL_ENABLED_SURFACES"))
    docs_sent = False
    for line in sys.stdin:
        message = json.loads(line)
        log("recv", message=message)
        method, request_id = message.get("method"), message.get("id")
        if method == "initialize":
            send({"id": request_id, "result": {"protocolVersion": message["params"]["protocolVersion"],
                                               "capabilities": {"tools": {}},
                                               "instructions": "Fake cua_repl.",
                                               "serverInfo": {"name": "fake-cua-repl", "version": "1"}}})
        elif method == "tools/list":
            send({"id": request_id, "result": {"tools": TOOLS}})
        elif method == "tools/call":
            params = message["params"]
            name, code = params["name"], (params.get("arguments") or {}).get("code")
            if name == "js" and code == "crash":
                log("exit", code=1)
                sys.exit(1)
            if name == "js" and code.startswith("elicit"):
                app = code.partition(":")[2] or "Calculator"
                send({"id": "elicit-1", "method": "elicitation/create", "params": {
                    "message": f'Allow Computer Use to use "{app}"?',
                    "requestedSchema": {"type": "object", "properties": {}}}})
                answer = json.loads(sys.stdin.readline())
                log("recv", message=answer)
                texts(request_id, json.dumps(answer.get("result")))
                continue
            errors = {"no-windows": "Computer Use server error -10005: noWindowsAvailable",
                      "no-key": 'Computer Use server error -10005: keyNotFound(",")',
                      "refuse": 'Computer Use was not approved to use "Terminal"'}
            if name == "js" and code in errors:
                texts(request_id, errors[code])
                continue
            if name == "js" and code == "slow":
                time.sleep(0.3)
            kind, _, app = str(code).partition(":")
            if name == "js" and kind == "alert-late":
                texts(request_id, "done")
                sound(app)
                continue
            if name == "js" and kind in ("alert", "other-sound"):
                sound(app, 4096 if kind == "alert" else 1100)
                time.sleep(0.3)  # the fake log's turn
            meta = json.dumps(params.get("_meta"))
            if name == "js" and not docs_sent:
                docs_sent = True
                texts(request_id, DOCS, meta)
            else:
                texts(request_id, meta)
        elif method is not None and request_id is not None:
            send({"id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})
    log("exit", code=0)


if __name__ == "__main__":
    main()
