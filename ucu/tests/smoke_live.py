#!/usr/bin/env python3
"""Live check against the installed Codex plugin: start this repo's proxy as Claude Code would, let it
find the real cua_repl, take one screenshot of Calculator, wait on it with js/wait.mjs and list its
windows with the list_windows tool. It answers the app approval itself and
changes no configuration. Not part of the unit tests.

  python3 -I tests/smoke_live.py
"""
import importlib.util
import itertools
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_proxy():
    spec = importlib.util.spec_from_file_location("ucu_proxy", ROOT / "scripts/ucu-proxy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Client:
    def __init__(self, definition):
        self.process = subprocess.Popen([definition["command"], *definition["args"]], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, env={**os.environ, **definition["env"]},
                                        cwd=str(Path.home()))
        self.messages = queue.Queue()
        self.ids = itertools.count(1)
        self.approvals = []
        threading.Thread(target=lambda: [self.messages.put(json.loads(line)) for line in self.process.stdout],
                         daemon=True).start()

    def send(self, message):
        self.process.stdin.write((json.dumps({"jsonrpc": "2.0", **message}) + "\n").encode())
        self.process.stdin.flush()

    def request(self, method, params, timeout=120):
        request_id = next(self.ids)
        self.send({"id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            message = self.messages.get(timeout=max(0.1, deadline - time.monotonic()))
            if message.get("id") == request_id and "method" not in message:
                return message
            if message.get("method") == "elicitation/create":
                self.approvals.append(message["params"].get("message"))
                self.send({"id": message["id"], "result": {"action": "accept", "content": {}}})
            elif message.get("method") == "roots/list":
                self.send({"id": message["id"], "result": {"roots": []}})
            elif message.get("method") == "ping":
                self.send({"id": message["id"], "result": {}})

    def js(self, code):
        return self.call("js", {"code": code})

    def call(self, name, arguments):
        started = time.monotonic()
        reply = self.request("tools/call", {"name": name, "arguments": arguments})
        code = arguments.get("code", arguments)
        result = reply.get("result") or {}
        text = " ".join(item.get("text", f"<{item.get('type')}>") for item in result.get("content", []))
        ok = "result" in reply and not result.get("isError")
        print(f"{'✓' if ok else '✗'} {name} {code!r} ({time.monotonic() - started:.1f} s): "
              f"{text[:160]!r}{'…' if len(text) > 160 else ''}{'' if ok else ' ' + json.dumps(reply)[:400]}")
        return ok


def main():
    try:
        version, _command, _env = load_proxy().plugin_command()
    except RuntimeError as error:
        print(f"✗ {error}")
        return 1
    definition = {"command": sys.executable, "args": ["-I", str(ROOT / "scripts/ucu-proxy.py")], "env": {}}
    print(f"plugin {version}, proxy {definition['args'][1]}")
    subprocess.run(["open", "-g", "-a", "Calculator"], check=True)
    client = Client(definition)
    try:
        init = client.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}},
                                             "clientInfo": {"name": "ucu-smoke", "version": "1"}})
        print(f"✓ initialize: {init['result']['serverInfo']}")
        rules = "ucu rules:" in init["result"].get("instructions", "")
        print(f"{'✓' if rules else '✗'} usage rules in the instructions")
        client.send({"method": "notifications/initialized"})
        tools = [tool["name"] for tool in client.request("tools/list", {})["result"]["tools"]]
        print(f"✓ tools: {tools}")
        ok = client.js('let app = await cua.getApp("Calculator");')
        ok = client.js('const shot = await app.getScreenshot({ emit: false }); nodeRepl.write(`screenshot ${shot.length} bytes`);') and ok
        ok = client.js(f'const {{ waitForChange }} = await import("{ROOT / "js/wait.mjs"}"); '
                       'nodeRepl.write(JSON.stringify(await waitForChange(app, null, { timeoutMs: 1000 })));') and ok
        ok = client.call("list_windows", {"app": "Calculator"}) and ok
        print(f"approvals answered: {client.approvals}")
        client.process.send_signal(signal.SIGINFO)  # end the turn, as the Stop hook does
        time.sleep(2)
        return 0 if ok and rules else 1
    finally:
        client.process.stdin.close()
        client.process.wait(15)


if __name__ == "__main__":
    sys.exit(main())
