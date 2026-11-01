<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/brand/ucu-logo-dark.svg">
    <img src="assets/brand/ucu-logo-light.svg" alt="UCU: Universal Computer Use" width="220">
  </picture>

  <h3>Universal Computer Use</h3>
  <p><strong>Let your AI agents use your Mac's apps in the background, without touching your mouse or getting in your way.</strong></p>
  <p>Fast, light on tokens, and ready for Claude Code, opencode and any MCP client. Built on <a href="https://github.com/vercel-labs/agent-desktop">agent-desktop</a> from Vercel Labs.</p>
</div>

## Why UCU

| Capability | What it gives you |
| --- | --- |
| Works in the background | Agents click, type and read other apps without moving your pointer or taking focus. |
| Stays out of your way | A closed app opens hidden, and if an app jumps to the front during an agent's action, your app is put back. |
| Has its own desktop | Apps that need clicks by position run on a private desktop you never see. |
| Clicks anything | A click by position presses the element at that point, or sends raw input to the window, even a canvas with no accessibility tree. |
| Shares Safari with you | The `safari` tool works in the agent's own hidden window, so you keep your tabs and your keyboard. |
| Runs side by side | Each chat has its own engine, and is told when another is using the same app. |
| Has its own cursors | Each chat gets a cursor color of its own, so you can tell at a glance who is doing what. |
| Sees every window | Agents are told when an app opens a new window, a sheet or a system dialog, with its text and buttons. |
| Hears errors | When an app plays the macOS alert sound, the agent is told. |
| Light on tokens | The model reads short numbered text and only what changed. |
| You choose the apps | Every app is approved except the ones you deny, with one command. |
| Shows who did what | Every app a chat uses is logged with the agent and folder that used it. |

## How fast it is

One M3 Max with macOS 27.2, on hidden apps. Run it yourself with `python3 -I benchmarks/run.py`.

| Test (median) | UCU |
| --- | ---: |
| Start the server | 50 ms |
| Read a small window (Calculator) | 127 ms |
| Tokens to read a small window | 148 |
| A task: `12 + 34 =` and read the answer | 1.3 s |
| Read a large window (Font Book) | 1.3 s |
| Tokens to read a large window | 1,953 |
| Tokens to set up a session | 1,710 |
| Server memory | 18 MB, plus about 20 MB for each chat's engine while it runs |

Tokens are estimated as characters / 4.

## Before you use it

- **Agents get real access.** Anything an app can do, an agent can do through it. Deny the apps you want to keep them
  out of.
- **The private desktop is a virtual display.** It uses a private macOS feature, and it appears in your display settings
  while an agent needs it.
- **Raw clicks use a macOS function Apple doesn't document.** Without it, a click by position only reaches elements that
  can be pressed.

## Requirements

- macOS, with the Xcode Command Line Tools (`xcode-select --install`)
- Rust, through [rustup](https://rustup.rs)
- Python 3.9 or newer
- [Claude Code](https://claude.com/claude-code), with the `claude` command on your `PATH`

## Install

```bash
git clone https://github.com/betoxf/agent-desktop.git
cd agent-desktop/ucu
./ucu install
./ucu doctor
```

UCU runs from this folder, so keep it. Start a new Claude Code session to pick it up; open sessions need a restart. If
`doctor` reports a problem, it says what to do next. The app that runs Claude Code, such as Terminal, needs Accessibility
permission in **System Settings > Privacy & Security > Accessibility**, and Screen Recording for screenshots.

`./ucu install` backs up what it changes to `~/Library/Application Support/ucu/backups/<time>/`, then:

- builds the engine to `~/.local/bin/ucu-engine` and the helper to `~/.local/bin/ucu-window`
- writes the approval policy, unless you already have one
- registers the `ucu` server in `~/.claude.json`
- adds a `Stop` hook to `~/.claude/settings.json`, so UCU knows when Claude's turn ends
- registers `ucu` in `~/.config/opencode/opencode.json`, if opencode is set up

To make Claude Code prefer UCU over its built-in computer use, add this to your `~/.claude/CLAUDE.md`:

```markdown
To operate native Mac apps, use the `ucu` MCP server instead of the built-in computer-use tools.
```

## Choosing which apps agents can use

Agents can use every app except the ones you deny. A new install denies password managers, Keychain Access
and System Settings.

```bash
./ucu deny "Mail"      # block an app
./ucu allow "Mail"     # unblock it
./ucu denied           # list the blocked apps
```

Changes apply at the next call, with no restart. Names match regardless of case, and a bundle id such as
`com.apple.mail` works too. The list is in `~/Library/Application Support/ucu/policy.json`. If that file is missing or
broken, no app is used, so a denied app can't slip through.

The first use of each app by a chat, and every refusal, goes to `~/Library/Logs/ucu/approve.log`:

```
2026-10-31 14:02:11 claude-code:my-project allowed Google Chrome
2026-10-31 14:02:40 opencode:website denied Mail
```

## Tools

- `look {app, window?, full?, screenshot?}`: an app's window as numbered text, then only what changed.
- `act {app, window?, steps}`: steps in order, then what changed. It stops at the first step that fails.
- `list_windows {app?}`: the windows of an app, or of every app, and which is the main window.
- `focus_window {app, title, unfullscreen?, activate?}`: makes a window the main window. With `activate: true`, the app
  comes to the front, for when you should see it.
- `move_to_desktop {app}`: moves an app's windows to the agent's private desktop.
- `safari {op, ...}`: works in Safari in the agent's own window.

A step is `{do, on, ...}`, where `on` is a number from `look`:

```json
{"app": "Calculator", "steps": [{"do": "click", "on": 15}, {"do": "press", "keys": "Return"}]}
```

`do` is `click`, `double-click`, `right-click`, `triple-click`, `focus`, `toggle`, `check`, `uncheck`, `expand`,
`collapse`, `clear`, `scroll-to`, `type` or `set-value` (with `text`), `select` (with `text`), `scroll` (with `direction`
and `amount`) or `press` (with `keys`, and no `on`). For a click where there is no number, pass `xy` with pixels from a
`look` screenshot.

When an app rejects an input it plays the macOS alert sound, which a person hears but an agent can't. UCU watches the
system sound server's log and adds a note to the result. It needs no permission and hears no audio. New windows work the
same way, and system dialogs such as password prompts too:

```
[ucu: Safari opened a dialog titled "Sign in". Its text: "Enter your password". Buttons: "Cancel", "OK". To act on it, pass its title as `window`.]
```

The server sends the usage rules in its MCP `instructions`, so clients get them without their own config.

### Staying out of your way

When an agent uses a closed app, UCU opens it hidden. Its Dock icon shows it is running, and nothing appears on screen
unless you click that icon or the agent calls `focus_window` with `activate: true`. Apps that show themselves anyway, such
as Blender, are hidden as soon as their window appears, behind your front app. A denied app stays closed.

Agents read a hidden app, take screenshots of it and act on its elements as usual. A click by position that ends up as
raw input needs a window on screen, and so do a few actions an app refuses while hidden. For those, UCU moves the app to
the agent's private desktop.

If an app comes to the front during an agent's call or just after it, and it is an app that chat drives or one that just
started, UCU puts your app back and tells the agent. If you pressed a key or clicked just before, it does nothing, because
you may have switched apps yourself.

### The private desktop

Each chat that needs one gets a virtual display, called UCU Agent Desktop, 1920 by 1200. macOS treats its windows as on
screen, so they keep rendering and take clicks, but nobody sees them. It touches your screens at one corner at most, so
your pointer won't wander onto it, and it appears in System Settings > Displays while it exists.

UCU creates it on first use and removes it 2 minutes after a turn ends, or when the chat closes. If you click such an app
in the Dock, its windows come back to your screen. If virtual displays are not available, apps are shown behind your
windows instead.

### Safari

Agents use the `safari` tool, and UCU tells them to if they name Safari while you are in it:

- `list` shows every window and tab, yours too.
- `open` loads a page in a new tab of the agent's own hidden window. Each chat has its own, closed when the chat ends.
- `navigate`, `read`, `click`, `type`, `wait` and `eval` work in those tabs while the window stays hidden.
- `shot` takes a screenshot. Safari stops painting hidden windows, so the window moves to the private desktop until the
  turn ends. Your window keeps the keyboard.
- Changing one of your tabs needs `allow_user_tab`, and the tab you are viewing also needs `allow_current_tab`. Agents are
  told to use those only if you asked.

It needs **Safari > Settings > Advanced > Show features for web developers**, then **Develop > Allow JavaScript from Apple
Events**, and permission for the app running Claude Code to control Safari. `shot` also needs Screen Recording for that
app. Clicks and typing are synthetic, so file pickers and some logins ignore them.

### Several agents

Each chat has its own engine, so chats on different apps work at the same time. Each also keeps a small file in
`~/Library/Application Support/ucu/sessions/`, and when an agent gets an app another agent used in the last 3 minutes, it
gets a note saying who. Two agents on the same window still undo each other's work, so the note asks it to wait or ask you.

### Cursors

Each chat has a cursor with a soft shadow, in a color no other chat is using. A chat in the same folder gets the same
color when it is free. The cursor takes a curved path to each action, leans into the move, stays just above the window it
is working on, and fades out when the turn ends. It takes no clicks and never moves your pointer. An agent working on a
hidden app or on the private desktop shows no cursor, because nobody could see it.

## Other MCP clients

Any client that speaks MCP over stdio can run the server directly, once `./ucu install` has built the engine:

```json
{
  "command": "python3",
  "args": ["-I", "/path/to/agent-desktop/ucu/scripts/ucu-proxy.py"]
}
```

Without the `Stop` hook, a turn ends after 2 minutes without a call.

## Update

```bash
git pull
./ucu install
```

Then start a new session.

## Uninstall

```bash
./ucu uninstall
```

This removes the server, hook, engine and `ucu-window`. Your policy, logs and backups stay in
`~/Library/Application Support/ucu/` and `~/Library/Logs/ucu/`.

## How it works

| Path | What it does |
| --- | --- |
| `ucu` | The `install`, `uninstall`, `doctor`, `deny`, `allow` and `denied` commands. |
| `scripts/ucu-proxy.py` | The MCP server. It runs the engine, keeps the deny list, renders windows as short text and adds notes. |
| `native/ucu-window.swift` | `ucu-window list/focus` and `ucu-window watch`, which opens apps hidden, reports new windows, holds the private desktop and draws each chat's cursor. |
| `native/safari.js` | The Safari side of the `safari` tool, run with `osascript`. |
| `..` | agent-desktop, the engine. See `../FORK.md`. |

## Tests

```bash
python3 -m unittest discover -s tests
```

These run the real server and installer against a fake engine in a temporary `HOME`, with fake `claude`, `swiftc` and
`cargo`.

## Credits

Built on [agent-desktop](https://github.com/vercel-labs/agent-desktop) from Vercel Labs (Apache-2.0). Claude is a trademark
of Anthropic, named here only to say what this project works with.

## License

[Apache-2.0](LICENSE). Copyright 2026 Humberto Prado.
