#!/usr/bin/env python3
"""Draw the README's charts from benchmarks/results.json.

    python3 -I benchmarks/chart.py

Every number and shape comes from the results. The only generated art is the dark background in
assets/benchmarks/plate-*.jpg, which carries no data. Each image has at most three groups.
"""
import base64
import json
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COLORS = {"ink": "#f5f4f1", "muted": "#a3a29a", "rule": "#ffffff", "ucu": "#f0efec", "agent-desktop": "#8d8c85"}
NAMES = {"ucu": "UCU", "agent-desktop": "agent-desktop"}
BASE = "#03050a"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
WIDTH, PLATE_HEIGHT, HEIGHT, CANDLE, CANDLE_GAP, MIN_BODY = 1000, 570, 474, 84, 20, 6


def ms(value):
    return f"{value / 1000:.1f} s" if value >= 1000 else f"{value:.0f} ms"


def count(value):
    return f"{value:,.0f}"


def times(value):
    return f"{value:.1f}×" if value < 10 else f"{value:.0f}×"


def text(x, y, content, fill="ink", size=15, weight=400, anchor="start", extra=""):
    return (f'<text x="{x:.1f}" y="{y:.1f}" fill="{COLORS[fill]}" font-size="{size}" font-weight="{weight}" '
            f'text-anchor="{anchor}" {extra}>{escape(content)}</text>')


def legend():
    parts, x = [], 762
    for tool, width in (("ucu", 72), ("agent-desktop", 0)):
        parts.append(f'<rect x="{x}" y="46" width="14" height="14" rx="4" fill="{COLORS[tool]}"/>')
        parts.append(text(x + 22, 58, NAMES[tool], "muted", 16))
        x += width
    return "".join(parts)


def card(title, height, body, footer, plate, shift=0):
    """A rounded dark card. The art moves by shift, so its brightest part stays clear of the text."""
    image = base64.b64encode((ROOT / f"assets/benchmarks/plate-{plate}.jpg").read_bytes()).decode()
    heading = text(44, 64, title, "ink", 32, 700) if title else ""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}" font-family="{FONT}" role="img" aria-label="{escape(title or footer)}">
<title>{escape(title or footer)}</title>
<defs>
<clipPath id="card"><rect width="{WIDTH}" height="{height}" rx="24"/></clipPath>
<filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="6"/></filter>
<linearGradient id="fade" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BASE}"/><stop offset="1" stop-color="{BASE}" stop-opacity="0"/></linearGradient>
</defs>
<g clip-path="url(#card)">
<rect width="{WIDTH}" height="{height}" fill="{BASE}"/>
<image href="data:image/jpeg;base64,{image}" y="{shift}" width="{WIDTH}" height="{PLATE_HEIGHT}" preserveAspectRatio="xMidYMin slice"/>
<rect y="{shift}" width="{WIDTH}" height="170" fill="url(#fade)"/>
{heading}
{body}
{text(44, height - 24, footer, "muted", 14)}
</g>
<rect x="0.75" y="0.75" width="{WIDTH - 1.5}" height="{height - 1.5}" rx="23.5" fill="none" stroke="{COLORS['rule']}" stroke-opacity="0.14" stroke-width="1.5"/>
</svg>
"""


def candle(x, mark, y):
    """The middle half of the samples as a box, with the median as a bright line, like a stock candle."""
    tool, low, middle, high = mark
    color, top = COLORS[tool], y(high)
    height = max(MIN_BODY, y(low) - top)
    line = f'x1="{x - 6}" x2="{x + CANDLE + 6}" y1="{y(middle):.1f}" y2="{y(middle):.1f}"'
    return (f'<rect x="{x}" y="{top:.1f}" width="{CANDLE}" height="{height:.1f}" rx="6" fill="{color}" fill-opacity="0.24" '
            f'stroke="{color}" stroke-opacity="0.75" stroke-width="1.5"/>'
            f'<line {line} stroke="{color}" stroke-width="8" stroke-opacity="0.55" filter="url(#glow)"/>'
            f'<line {line} stroke="{color}" stroke-width="3.5" stroke-linecap="round"/>'
            + text(x + CANDLE / 2, top - 13, ms(middle), "ink", 20, 600, "middle"))


def candles(title, groups, footer, plate, shift=0):
    left, right, upper, lower = 44, 956, 138, 372
    body = [legend(), f'<line x1="{left}" x2="{right}" y1="{lower}" y2="{lower}" stroke="{COLORS["rule"]}" stroke-opacity="0.24"/>']
    width = (right - left) / len(groups)
    for index, (label, marks) in enumerate(groups):
        center, top = left + width * (index + 0.5), max(mark[3] for mark in marks)
        y = lambda value: lower - value / top * (lower - upper)
        x = center - (len(marks) * CANDLE + (len(marks) - 1) * CANDLE_GAP) / 2
        body.append(f'<g data-group="{escape(label)}">')
        for mark in marks:
            body.append(candle(x, mark, y))
            x += CANDLE + CANDLE_GAP
        body.append(text(center, lower + 34, label, "ink", 20, 600, "middle"))
        body.append("</g>")
    return card(title, HEIGHT, "".join(body), footer, plate, shift)


def speed_groups(results):
    medians, spread = results["medians"], results["spread"]

    def group(label, metric):
        return label, [(tool, spread[tool][metric][0], medians[tool][metric], spread[tool][metric][1]) for tool in NAMES]

    return [
        group("Read a small window", "read small app (ms)"),
        group("Click a button", "click (ms)"),
        group("Take a screenshot", "screenshot (ms)"),
        group("Finish a task", "task (ms)"),
        group("Read a large window", "read dense app (ms)"),
        group("Start from nothing", "cold start (ms)"),
    ]


def token_costs(results):
    """Each tool's tokens to set up a session and to finish one task."""
    medians, setup = results["medians"], results["setup chars"]
    return {tool: (sum(setup[tool].values()) / 4, medians[tool]["task tokens read"] + medians[tool]["task tokens written"])
            for tool in NAMES}


def tokens(results):
    costs = token_costs(results)
    left, right, upper, lower, last, top = 44, 780, 110, 372, 8, 21000
    x = lambda task: left + task / last * (right - left)
    y = lambda value: lower - value / top * (lower - upper)
    total = lambda tool, task: costs[tool][0] + task * costs[tool][1]
    (ucu_setup, ucu_task), (desktop_setup, desktop_task) = costs["ucu"], costs["agent-desktop"]
    crossing = (ucu_setup - desktop_setup) / (desktop_task - ucu_task)
    first = int(crossing) + 1
    body = [f'<line x1="{left}" x2="{right}" y1="{lower}" y2="{lower}" stroke="{COLORS["rule"]}" stroke-opacity="0.24"/>']
    for task, label in ((0, "Setup"), (2, "2 tasks"), (4, "4 tasks"), (6, "6 tasks"), (8, "8 tasks")):
        body.append(text(x(task), lower + 30, label, "muted", 16, 400, "start" if task == 0 else "middle"))
    points = {tool: " ".join(f"{x(task):.1f},{y(total(tool, task)):.1f}" for task in range(last + 1)) for tool in NAMES}
    body.append(f'<polyline points="{points["ucu"]}" fill="none" stroke="{COLORS["ucu"]}" stroke-width="8" '
                f'stroke-opacity="0.45" filter="url(#glow)"/>')
    for tool, stroke in (("agent-desktop", 3), ("ucu", 4)):
        body.append(f'<polyline points="{points[tool]}" fill="none" stroke="{COLORS[tool]}" stroke-width="{stroke}" '
                    f'stroke-linejoin="round" stroke-linecap="round"/>')
        body.append(f'<circle cx="{x(last):.1f}" cy="{y(total(tool, last)):.1f}" r="6" fill="{COLORS[tool]}"/>')
        body.append(text(x(last) + 18, y(total(tool, last)) + 2, count(total(tool, last)), "ink", 20, 600))
        body.append(text(x(last) + 18, y(total(tool, last)) + 22, NAMES[tool], "muted", 15))
    cross_x, cross_y = x(crossing), y(ucu_setup + crossing * ucu_task)
    body.append(f'<circle cx="{cross_x:.1f}" cy="{cross_y:.1f}" r="12" fill="{COLORS["ink"]}" fill-opacity="0.18"/>')
    body.append(f'<circle cx="{cross_x:.1f}" cy="{cross_y:.1f}" r="6" fill="{COLORS["ink"]}"/>')
    body.append(text(cross_x - 12, cross_y + 44, f"UCU costs less from task {first}", "ink", 20, 600))
    footer = "One setup, then the same task each time. Tokens are estimated as characters / 4."
    return card("Total tokens in a session", HEIGHT, "".join(body), footer, "dots")


def headline(results, footer):
    medians, memory = results["medians"], results["idle memory (MB)"]
    read = "read small app (ms)"
    costs = token_costs(results)
    tiles = [
        ("faster window reads", medians["ucu"][read], medians["agent-desktop"][read], ms, "agent-desktop"),
        ("fewer tokens per task", costs["ucu"][1], costs["agent-desktop"][1], count, "agent-desktop"),
        ("less idle memory", memory["ucu"], memory["Codex engine"], lambda value: f"{value:.0f} MB", "the Codex engine"),
    ]
    body = []
    for index, (label, ours, theirs, shown, against) in enumerate(tiles):
        x = 44 + index * 312
        body.append(f'<g data-group="{escape(label)}">')
        body.append(text(x, 136, times(theirs / ours), "ink", 104, 700, "start", 'letter-spacing="-3"'))
        body.append(text(x, 182, label, "ink", 22, 600))
        body.append(text(x, 212, f"{shown(ours)} vs {shown(theirs)} for {against}", "muted", 16))
        body.append("</g>")
    return card("", 284, "".join(body), footer, "arc", -20)


def charts(results):
    chip = results["versions"]["chip"]
    speed = speed_groups(results)
    footer = f"Medians on an {chip}. Boxes show the middle half of the samples. Each group has its own scale."
    return {
        "headline": headline(results, f"Medians on an {chip}."),
        "speed-actions": candles("Speed: single actions", speed[:3], footer, "arc", 130),
        "speed-jobs": candles("Speed: longer jobs", speed[3:], footer, "dots"),
        "tokens": tokens(results),
    }


def main():
    results = json.loads((ROOT / "benchmarks/results.json").read_text())
    folder = ROOT / "assets/benchmarks"
    for name, svg in charts(results).items():
        (folder / f"{name}.svg").write_text(svg)


if __name__ == "__main__":
    main()
