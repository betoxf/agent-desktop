# This fork

`betoxf/agent-desktop` is agent-desktop from [Vercel Labs](https://github.com/vercel-labs/agent-desktop), plus UCU:
everything that lets agents work on a Mac without getting in the user's way.

| Branch | What it holds |
| --- | --- |
| `ucu` | Our product: upstream, our contributions, and `ucu/`. The default branch. |
| `main` | An exact copy of upstream. Nothing is committed here. |
| `contrib/<topic>` | One improvement for upstream, cut from `main`, following upstream's `AGENTS.md`. |

## Adding an improvement

Anything upstream might want starts on a `contrib/<topic>` branch and merges into `ucu`. Open the upstream PR from that
branch whenever it's ready: `ucu` doesn't wait for it. Work that only makes sense for UCU goes straight to `ucu/`.

## Staying up to date

```bash
git fetch upstream
git push origin upstream/main:main
git checkout ucu && git merge upstream/main
```

`ucu/` keeps UCU's own README, tests and license notice. It still runs on the Codex engine: `ucu/scripts/ucu-proxy.py`
starts `cua_repl`, and replacing that with this repo's engine is the work in progress.
