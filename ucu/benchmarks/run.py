"""Times the installed server on Calculator and Font Book. It opens both hidden, and they stay hidden."""
import json, os, statistics as st, subprocess, sys, time
from pathlib import Path
SERVER = Path(__file__).resolve().parent.parent / "scripts/ucu-proxy.py"


class C:
    def __init__(self, cwd):
        self.p = subprocess.Popen([sys.executable, "-I", str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, cwd=cwd)
        self.i = 0
    def rpc(self, method, params):
        self.i += 1; i = self.i
        self.p.stdin.write((json.dumps({"jsonrpc": "2.0", "id": i, "method": method, "params": params}) + "\n").encode()); self.p.stdin.flush()
        while True:
            m = json.loads(self.p.stdout.readline())
            if m.get("id") == i: return m
    def tool(self, name, **a):
        t = time.perf_counter(); r = self.rpc("tools/call", {"name": name, "arguments": a})["result"]
        ms = (time.perf_counter() - t) * 1000
        return ms, "\n".join(x.get("text", "") for x in r["content"]), r.get("isError"), len(json.dumps(a))
t0 = time.perf_counter()
c = C(str(Path.home()))
init = c.rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "bench", "version": "1"}})
tools = c.rpc("tools/list", {})
print("start ms", int((time.perf_counter() - t0) * 1000))
print("setup tokens", (len(init["result"]["instructions"]) + len(json.dumps(tools["result"]))) // 4)
c.tool("look", app="Calculator")
samples = [c.tool("look", app="Calculator", full=True) for _ in range(12)]
print("read small ms", int(st.median(s[0] for s in samples)), "tokens", len(samples[0][1]) // 4)
print("read no change ms", int(st.median(c.tool("look", app="Calculator")[0] for _ in range(10))))
text = c.tool("look", app="Calculator", full=True)[1]
num = {}
for line in text.splitlines():
    w = line.strip().split(" ", 2)
    if len(w) == 3 and w[1] == "button": num[w[2].split(",")[0]] = int(w[0])
ones = [c.tool("act", app="Calculator", steps=[{"do": "click", "on": num["Point"]}])[0] for _ in range(8)]
print("act one click (with read) ms", int(st.median(ones)))
tasks = []
for _ in range(8):
    text = c.tool("look", app="Calculator", full=True)[1]
    for line in text.splitlines():
        w = line.strip().split(" ", 2)
        if len(w) == 3 and w[1] == "button": num[w[2].split(",")[0]] = int(w[0])
    clear = num.get("All Clear") or num.get("Clear")
    steps = [{"do": "click", "on": clear}] + [{"do": "click", "on": num[k]} for k in ("1", "2", "Add", "3", "4", "Equals")]
    ms, said, err, sent = c.tool("act", app="Calculator", steps=steps)
    tasks.append(ms); last = (said, sent)
print("task ms", int(st.median(tasks)), "tokens", (last[1] + len(last[0])) // 4, "answer ok", "46" in last[0])
big = [c.tool("look", app="Font Book", full=True) for _ in range(5)]
print("read large ms", int(st.median(s[0] for s in big[1:])), "tokens", len(big[-1][1]) // 4, "lines", big[-1][1].count("\n"), big[-1][2])
rss = subprocess.run(["ps", "-o", "rss=", "-p", str(c.p.pid)], capture_output=True, text=True).stdout.strip()
print("server rss MB", int(rss) // 1024)
c.p.stdin.close(); c.p.wait(10)
