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
| Stays out of your way | A closed app opens on a private desktop you never see, and apps agents drive can't take your focus. |
| Has its own desktop | Apps that need clicks by position run on a private desktop you never see. |
| Clicks anything | A click by position presses the element at that point, or sends raw input to the window, even a canvas with no accessibility tree. |
| Shares your browsers | The `safari` and `chrome` tools work in the agent's own window, so you keep your tabs and your keyboard. |
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

UCU runs from this folder, so keep it, and it updates itself from there (see [Update](#update)). Start a new Claude Code
session to pick it up; open sessions need a restart. If `doctor` reports a problem, it says what to do next. The app that
runs Claude Code, such as Terminal, needs Accessibility permission in **System Settings > Privacy & Security >
Accessibility**, and Screen Recording for screenshots.

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
- `focus_window {app, title, unfullscreen?, activate?}`: makes a window the main window. With `activate: true`, the
  window comes to your screen and the front, only when you ask to see it.
- `move_to_desktop {app}`: moves an app's windows to the agent's private desktop.
- `safari {op, ...}` and `chrome {op, ...}`: work in Safari and Google Chrome in the agent's own window.

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

When an agent uses a closed app, UCU opens it on the agent's private desktop, a screen you never see. Its Dock icon
shows it is running, and nothing appears on your screens. A hidden app shows itself wherever its windows are as soon as
it comes forward, so UCU's apps live where showing shows you nothing, and a new window such an app opens joins it there
at once. Agents read it, take screenshots and act on it as usual, clicks by position too. If your Mac can't make the
private desktop, the app stays hidden instead, and goes back into hiding whenever it comes forward. A denied app stays
closed.

Agents never need the front: every action works in the background, and a shortcut such as Command-N runs through the
app's menu, which works even on a hidden app. The usage rules tell agents to bring an app forward only when you ask to
see it or be taken there, never to make an action work, and to leave your clipboard alone.

Apps still come forward by themselves, such as Chrome when it opens a window, a tab or an extension's popup. UCU puts
your app back at once, usually within a hundredth of a second, and tells the agent: for an app it opened, whenever that
happens while the chat runs; for an app you use too, while the chat works on it and for 10 seconds after. When the
agent's own step is what brings it forward, such as making its Chrome window, that always happens. Otherwise UCU leaves
the app in front if you clicked, pressed a modifier key, or hold Return just before, or if your app is no longer in
sight, because you may have switched apps or Spaces yourself. Typing in your app doesn't count, since that is when a
stolen focus hurts most. A new app that starts during a call is held back too, unless you did anything since just before
it started.

In an app a chat drives, one of the agent's windows out of sight can become the main window, such as its Chrome window
when it shows or opens a popup. Your window gets the main role back, so your typing, or your next Command-Tab to that
app, lands where you can see it.

When you ask to see what the agent is doing, or to be taken there, `focus_window` with `activate: true` brings that
window from the private desktop to your screen and to the front, and the browser tools' `show` hands you the agent's
tab. That is the only way UCU brings anything forward.

Every time an app comes forward during agent work, `~/Library/Logs/ucu/focus.log` says what UCU did, naming the apps but
no window titles, and `ucu doctor` sums up the last day.

### The private desktop

Each chat that needs one gets a virtual display, called UCU Agent Desktop, 1920 by 1200. macOS treats its windows as on
screen, so they keep rendering and take clicks, but nobody sees them. It touches your screens at one corner at most, so
your pointer won't wander onto it, and it appears in System Settings > Displays while it exists.

UCU creates it on first use and removes it 2 minutes after a turn ends, or when the chat closes. If you click such an app
in the Dock, its windows come back to your screen. If virtual displays are not available, apps are shown behind your
windows instead. A window left there when the desktop goes, such as an agent's browser window after a crash, goes to the
Dock rather than onto your screen.

### Safari and Chrome

Agents use the `safari` and `chrome` tools, and UCU tells them to if they name the browser while you are in it. Each chat
works in a window of its own, so you can keep browsing, and several agents can work at once:

- `list` shows every window and tab, yours too.
- `open` loads a page in a new tab of the agent's own window. Each chat has its own, closed when the chat ends.
- `navigate`, `read`, `click`, `type`, `wait` and `eval` work in those tabs, out of your sight.
- `upload` gives a page local files without its file picker, which would need the browser in front: they go into a file
  input as if chosen there, or are dropped on a drop zone. Agents confirm with you first.
- `show` hands you the agent's tab when you ask to see it: its window comes to your screen, in front, and stays yours.
- `shot` takes a screenshot. Your window keeps the keyboard.
- Changing one of your tabs needs `allow_user_tab`, and the tab you are viewing also needs `allow_current_tab`. Agents are
  told to use those only if you asked.

Safari's window stays hidden, and moves to the private desktop for a screenshot until the turn ends, since Safari stops
painting hidden windows. Chrome's window is on the private desktop whenever the agent works in it, where pages behave as
on a screen and `look` and `act` reach it by its title, which is how an agent handles Chrome's own controls. It is hidden
when the turn ends. Chrome comes forward for an instant when it makes the agent's window, once a chat, and UCU puts your
app back at once. Tabs open blank and go to their page through the page itself, which keeps Chrome behind, when Chrome
allows JavaScript from Apple Events. An extension's popup closes once Chrome loses focus, so agents open its page in
their tab instead, at `chrome-extension://<id>/<page>`.

Safari needs **Safari > Settings > Advanced > Show features for web developers**, then **Develop > Allow JavaScript from
Apple Events**. Chrome needs **View > Developer > Allow JavaScript from Apple Events** for everything that runs in a page.
Both need permission for the app running Claude Code to control the browser, which macOS asks for the first time, and
`shot` needs Screen Recording for that app. Clicks and typing are synthetic, so some logins ignore them.

A native Open dialog, such as Chrome's Load unpacked, opens in the background and its sidebar works, but choosing a file
in it needs the keyboard, so agents ask you to pick it. An unpacked extension loaded once can be reloaded in the background
from the Reload button on `chrome://extensions`.

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

UCU keeps itself up to date. As a chat starts, its server looks for new commits on the branch this clone follows, at most
every 10 minutes and in the background. It fast-forwards the clone and rebuilds the engine or `ucu-window` when their
code changed, and the next chat runs the new version. It leaves a clone with local changes or commits of its own alone,
and undoes an update whose engine doesn't build. What it did is in `~/Library/Logs/ucu/update.log`.

To update now, run `./ucu update`. To stop updating by itself:

```bash
touch ~/Library/Application\ Support/ucu/no-auto-update
```

## Uninstall

```bash
./ucu uninstall
```

This removes the server, hook, engine and `ucu-window`. Your policy, logs and backups stay in
`~/Library/Application Support/ucu/` and `~/Library/Logs/ucu/`.

## How it works

| Path | What it does |
| --- | --- |
| `ucu` | The `install`, `uninstall`, `update`, `doctor`, `deny`, `allow` and `denied` commands. |
| `scripts/ucu-proxy.py` | The MCP server. It runs the engine, keeps the deny list, renders windows as short text and adds notes. |
| `native/ucu-window.swift` | `ucu-window list/focus` and `ucu-window watch`, which opens apps hidden, reports new windows, holds the private desktop and draws each chat's cursor. |
| `native/browser.js` | The browser side of the `safari` and `chrome` tools, run with `osascript`. |
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
