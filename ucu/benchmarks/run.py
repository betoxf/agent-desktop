#!/usr/bin/env python3
"""Measure ucu, the Codex engine on its own and agent-desktop on the same tasks, on this Mac.

    python3 -I benchmarks/run.py --agent-desktop /path/to/agent-desktop [--rounds 3]

The tools take turns sample by sample, so drift on the Mac hits all three alike, and every result is the
median of all its samples, with the middle half of them in "spread". It drives Calculator (it presses buttons) and reads Font Book, which it opens in the
background and quits at the end. Close them first if you are using them. Results go to
benchmarks/results.json with the versions and the Mac model, and never include what was on screen.

Tokens are estimated as characters / 4: there is no Claude tokenizer on the machine. Screenshots are
left out of the token counts, because the three tools return the same image.
"""
import argparse
import importlib.util
import itertools
import json
import os
import platform
import queue
import re
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROXY = ROOT / "scripts/ucu-proxy.py"
SMALL, DENSE = "Calculator", "Font Book"
READS, CLICKS, TASKS, COLD = 10, 10, 3, 2  # samples per round


def load_proxy():
    spec = importlib.util.spec_from_file_location("ucu_proxy", PROXY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rss_mb(root_pids):
    """Resident memory of these processes and all their descendants."""
    rows = [line.split() for line in subprocess.run(["ps", "-axo", "pid=,ppid=,rss="], capture_output=True,
                                                    text=True).stdout.splitlines()]
    kids, memory = {}, {}
    for pid, ppid, rss in rows:
        kids.setdefault(int(ppid), []).append(int(pid))
        memory[int(pid)] = int(rss)
    total, stack, seen = 0, list(root_pids), set()
    while stack:
        pid = stack.pop()
        if pid not in seen:
            seen.add(pid)
            total += memory.get(pid, 0)
            stack.extend(kids.get(pid, []))
    return total / 1024


def timed(call):
    start = time.perf_counter()
    result = call()
    return (time.perf_counter() - start) * 1000, result


class Mcp:
    """A stdio MCP client that drives the engine, directly or through the ucu proxy, as an agent's host would."""

    def __init__(self, command, env):
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.DEVNULL, env={**os.environ, **env}, cwd=str(Path.home()))
        self.messages = queue.Queue()
        self.ids = itertools.count(1)
        self.session = f"benchmark-{os.getpid()}"
        threading.Thread(target=self._read, daemon=True).start()
        self.info = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {"elicitation": {}},
                                                "clientInfo": {"name": "ucu-benchmark", "version": "1"}})["result"]
        self._send({"method": "notifications/initialized"})

    def _read(self):
        for line in self.process.stdout:
            self.messages.put(json.loads(line))

    def _send(self, message):
        self.process.stdin.write((json.dumps({"jsonrpc": "2.0", **message}) + "\n").encode())
        self.process.stdin.flush()

    def request(self, method, params):
        request_id = next(self.ids)
        self._send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.messages.get(timeout=120)
            if message.get("id") == request_id and "method" not in message:
                return message
            if message.get("method") == "elicitation/create":  # as Codex does once an app is approved for good
                self._send({"id": message["id"], "result": {"action": "accept", "content": {}}})
            elif "method" in message and "id" in message:
                self._send({"id": message["id"], "result": {}})

    def js(self, code):
        """Run code; return the text the model would read."""
        meta = {"x-codex-turn-metadata": {"session_id": self.session, "turn_id": f"{self.session}-turn-1"}}
        reply = self.request("tools/call", {"name": "js", "arguments": {"code": code, "timeout_ms": 60000},
                                            "_meta": meta})
        result = reply.get("result") or {}
        if "error" in reply or result.get("isError"):
            raise RuntimeError(json.dumps(reply)[:300])
        return "".join(item.get("text", "") for item in result.get("content", []))

    def close(self):
        self.process.stdin.close()
        try:
            self.process.wait(10)
        except subprocess.TimeoutExpired:
            self.process.kill()


class Engine:
    """The Computer Use engine, on its own or behind the ucu proxy."""

    def __init__(self, name, command, env):
        self.name, self.command, self.env = name, command, env
        self.mcp = Mcp(command, env)
        self.app_state = {}

    def docs_and_state(self, app, variable):
        """Bind an app; the first call in a process also returns the first-use documentation."""
        return self.mcp.js(f'globalThis.{variable} = await cua.getApp("{app}");')

    def ready(self):
        first = self.docs_and_state(SMALL, "calc")
        self.docs_chars = len(first) - len(self.read(SMALL))
        self.docs_and_state(DENSE, "dense")
        self.setup = {"tool definitions": len(json.dumps(self.mcp.request("tools/list", {})["result"]["tools"])),
                      "instructions": len(self.mcp.info.get("instructions") or ""),
                      "first-use docs": self.docs_chars}

    def read(self, app):
        return self.mcp.js(f"await {'calc' if app == SMALL else 'dense'}.getAXState({{ disableDiffing: true }});")

    def screenshot(self):
        self.mcp.js("await calc.getScreenshot({ emit: false });")

    @staticmethod
    def button(state, label):
        patterns = {"Add": r"(\d+) button Add", "Equals": r"(\d+) button Equals",
                    "Change Sign": r"(\d+) button Description: Change Sign",
                    "Clear": r"(\d+) button (?:Clear|Description: All Clear)"}  # "All Clear" once the display is empty
        return int(re.search(patterns.get(label, rf"(\d+) button Description: {label}, ID"), state).group(1))

    def click_sign(self, state):
        self.mcp.js(f"await calc.click({self.button(state, 'Change Sign')});")

    def reset(self):
        for _ in range(2):  # Clear, then All Clear
            self.mcp.js(f"await calc.click({self.button(self.read(SMALL), 'Clear')});")

    def task(self):
        """12 + 34 =: see the buttons, then press them and read the answer in one call."""
        sent, seen = [], []
        code = "await calc.getAXState({ disableDiffing: true });"
        state = self.mcp.js(code)
        sent.append(code), seen.append(state)
        presses = "".join(f"await calc.click({self.button(state, label)});\n"
                          for label in ("1", "2", "Add", "3", "4", "Equals"))
        code = presses + "await calc.getAXState();"
        answer = self.mcp.js(code)
        sent.append(code), seen.append(answer)
        if "46" not in answer:
            raise RuntimeError(f"{self.name}: the answer was not 46: {answer[-700:]}")
        return sent, seen

    def memory_mb(self):
        return rss_mb([self.mcp.process.pid])

    def cold_start(self):
        def start():
            mcp = Mcp(self.command, self.env)
            mcp.js(f'globalThis.calc = await cua.getApp("{SMALL}");')
            return mcp
        ms, mcp = timed(start)
        mcp.close()
        return ms

    def close(self):
        self.mcp.close()


class Desktop:
    """agent-desktop through its CLI, with the options its docs recommend to save tokens."""

    name = "agent-desktop"

    def __init__(self, binary):
        self.binary = str(Path(binary).expanduser())
        self.folder = tempfile.mkdtemp()

    def cli(self, *args):
        result = subprocess.run([self.binary, *args], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"{args[0]}: {(result.stdout + result.stderr)[:300]}")
        return result.stdout

    def ready(self):
        docs = len(self.cli("skills", "get", "desktop")) + len(self.cli("skills", "get", "platform"))
        self.setup = {"tool definitions": 0, "instructions": 0, "first-use docs": docs}

    @staticmethod
    def buttons(snapshot):
        found, stack = {}, [json.loads(snapshot)["data"]["tree"]]
        while stack:
            node = stack.pop()
            stack.extend(node.get("children", []))
            if node.get("ref_id") and node.get("name"):
                found[node["name"]] = node["ref_id"]
            if node.get("role") == "scrollarea":
                found["display"] = node.get("ref_id")
        return found

    def read(self, app, skeleton=False):
        return self.cli("snapshot", "--app", app, *(["--skeleton"] if skeleton else []), "-i", "--compact")

    def screenshot(self):
        self.cli("screenshot", "--app", SMALL, str(Path(self.folder) / "shot.png"))
        (Path(self.folder) / "shot.png").unlink()

    def click_sign(self, state):
        self.cli("click", self.buttons(state)["Change Sign"])

    def reset(self):
        for _ in range(2):  # Clear, then All Clear
            refs = self.buttons(self.read(SMALL))
            self.cli("click", refs.get("Clear") or refs["All Clear"])

    def task(self):
        """12 + 34 =: see the buttons, press them in a batch, then read the answer in the same batch."""
        sent, seen = [], []
        state = self.read(SMALL)
        sent.append(f'agent-desktop snapshot --app {SMALL} -i --compact'), seen.append(state)
        refs = self.buttons(state)
        steps = [{"command": "click", "args": {"ref_id": refs[label]}} for label in ("1", "2", "Add", "3", "4", "Equals")]
        steps.append({"command": "find", "args": {"app": SMALL, "role": "statictext"}})  # its cheapest way to read it
        batch = json.dumps(steps)
        answer = self.cli("batch", batch, "--stop-on-error")
        sent.append(f"agent-desktop batch '{batch}' --stop-on-error"), seen.append(answer)
        if "46" not in answer:
            raise RuntimeError(f"agent-desktop: the answer was not 46: {answer[:200]}")
        return sent, seen

    def host_pids(self):
        rows = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True).stdout
        return [int(line.split()[0]) for line in rows.splitlines()
                if self.binary in line and line.split()[1] == "1"]

    def memory_mb(self):
        return rss_mb(self.host_pids())

    def cold_start(self):
        for pid in self.host_pids():
            os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        return timed(lambda: self.read(SMALL))[0]

    def close(self):
        pass


def add(samples, tool, metric, value):
    samples.setdefault(tool, {}).setdefault(metric, []).append(value)


def measure(tools, order, samples, sizes):
    """One round. Tools take turns sample by sample, so a slow moment on the Mac hits all of them alike."""
    states = {}
    for tool in tools:
        tool.reset()  # the same display for every tool
        tool.read(DENSE)  # warm up: the first read of a window after a pause is not typical
        states[tool.name] = tool.read(SMALL)
    for _ in range(READS):
        for tool in order:
            ms, state = timed(lambda: tool.read(SMALL))
            add(samples, tool.name, "read small app (ms)", ms)
            add(samples, tool.name, "screenshot (ms)", timed(tool.screenshot)[0])
            sizes.setdefault(tool.name, {})["read small app (chars)"] = len(state)
    for _ in range(READS // 2):
        for tool in order:
            ms, dense = timed(lambda: tool.read(DENSE))
            add(samples, tool.name, "read dense app (ms)", ms)
            add(samples, tool.name, "read dense app (chars)", len(dense))
            if isinstance(tool, Desktop):
                ms, skeleton = timed(lambda: tool.read(DENSE, skeleton=True))
                add(samples, "agent-desktop skeleton", "read dense app (ms)", ms)
                add(samples, "agent-desktop skeleton", "read dense app (chars)", len(skeleton))
    for _ in range(CLICKS):
        for tool in order:
            add(samples, tool.name, "click (ms)", timed(lambda: tool.click_sign(states[tool.name]))[0])
    for _ in range(TASKS):
        for tool in order:
            tool.reset()
            ms, (sent, seen) = timed(tool.task)
            add(samples, tool.name, "task (ms)", ms)
            add(samples, tool.name, "task tokens read", sum(map(len, seen)) / 4)
            add(samples, tool.name, "task tokens written", sum(map(len, sent)) / 4)
            sizes[tool.name]["task model round trips"] = len(sent)


def idle_memory(proxy_tool, codex_tool, desktop):
    time.sleep(1)
    idle = {"Codex engine": codex_tool.memory_mb(), "agent-desktop": desktop.memory_mb()}
    proxy_tool.mcp.process.send_signal(signal.SIGINFO)  # a turn ended: with --stop-grace 0 ucu stops the engine
    time.sleep(3)
    idle["ucu"] = proxy_tool.memory_mb()
    return idle


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent-desktop", required=True, help="path to the agent-desktop binary")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", default=str(ROOT / "benchmarks/results.json"))
    args = parser.parse_args()

    proxy = load_proxy()
    plugin, command, env = proxy.plugin_command()
    subprocess.run(["open", "-g", "-a", SMALL], check=True)
    subprocess.run(["open", "-g", "-a", DENSE], check=True)
    time.sleep(4)

    ucu_command = [sys.executable, "-I", str(PROXY), "--stop-grace", "0", "--turn-idle", "3600"]
    samples, sizes, setup = {}, {}, {}
    codex, ucu, desktop = Engine("Codex engine", command, env), Engine("ucu", ucu_command, {}), Desktop(args.agent_desktop)
    tools = [codex, ucu, desktop]
    for tool in tools:
        tool.ready()
        setup[tool.name] = tool.setup
    for round_number in range(args.rounds):
        print(f"round {round_number + 1}", flush=True)
        measure(tools, tools[round_number % 3:] + tools[:round_number % 3], samples, sizes)
    for _ in range(COLD * args.rounds):
        for tool in tools:
            add(samples, tool.name, "cold start (ms)", tool.cold_start())
    memory = idle_memory(ucu, codex, desktop)
    for tool in tools:
        tool.close()
    subprocess.run(["killall", DENSE], capture_output=True)

    chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    results = {
        "versions": {"codex plugin": plugin, "agent-desktop": desktop.cli("version").split('"version":"')[1].split('"')[0],
                     "macOS": platform.mac_ver()[0], "chip": chip, "rounds": args.rounds},
        "medians": {tool: {metric: round(statistics.median(values), 1) for metric, values in metrics.items()}
                    for tool, metrics in samples.items()},
        "spread": {tool: {metric: [round(q, 1) for q in statistics.quantiles(values, n=4)[::2]]
                          for metric, values in metrics.items() if len(values) > 1 and metric.endswith("(ms)")}
                   for tool, metrics in samples.items()},
        "samples": {tool: {metric: len(values) for metric, values in metrics.items()} for tool, metrics in samples.items()},
        "sizes": sizes, "setup chars": setup, "idle memory (MB)": {tool: round(mb, 1) for tool, mb in memory.items()},
    }
    Path(args.output).write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results["medians"], indent=1))


if __name__ == "__main__":
    main()
