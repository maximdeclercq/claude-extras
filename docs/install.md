# Installation

## Requirements

- The official `claude` CLI, installed any way Anthropic supports.
- [uv](https://docs.astral.sh/uv/) and `python3` >= 3.11.

No third-party Python dependencies. A few verbs shell out to programs you probably
already have, and each has a fallback:

| program | needed by | without it |
|---|---|---|
| `git` | account routing | a project is its own directory, not its repo root |
| a terminal | `spawn` | told to set `$CLAUDE_TERMINAL` |
| `hyprctl` | `spawn` with no directory | falls back to your home directory |

## Install

```
git clone https://github.com/maximdeclercq/claude-extras
uv tool install --force --editable ./claude-extras
claude auth doctor --fix
```

`uv tool install` puts the console script at `~/.local/bin/claude`, ahead of the
real binary on `PATH`. Everything it does not implement is handed straight through.
The install is editable, so edits to the clone take effect with no reinstall.

Anthropic's native installer keeps its launcher at that same path, which is what
`--force` replaces. Since Claude Code 2.1.207 a replaced launcher is left alone, so
updates keep landing under `~/.local/share/claude/versions/` and the wrapper runs
the newest one there. Native `claude doctor` will report a launcher it did not
create. Any other install is found on `PATH` as before, and `CLAUDE_REAL_BIN` names
the binary outright when neither applies.

`claude auth doctor --fix` writes `~/.claude/settings.json` and prints each change.
It adds a `statusLine` entry and the `SessionEnd` hook that `chat` depends on. A
`statusLine` of your own is kept, and only its missing `refreshInterval` is filled
in. The [doctor](accounts.md#doctor) section lists what else it repairs and what it
only reports. Run it again whenever something looks wrong.

The config directory, `~/.config/claude-extras`, creates itself at mode 700 the
first time anything writes to it. `CLAUDE_EXTRAS_CONFIG` moves it, and
`XDG_CONFIG_HOME` is honoured. A `deny` file in it, one directory per line, names
where launching is refused.

## After installing

```
command -v claude   # should print ~/.local/bin/claude
claude auth list    # accounts, and the routes that govern projects
claude usage
```

The 5h/7d percentages stay blank until one session has run with the status line
active, because that is what writes the first snapshot.

## Uninstall

```
claude auth doctor --undo
uv tool uninstall claude-extras
```

The first takes the `statusLine` and the hook that `--fix` wrote back out of every
account's `settings.json`. Left in, they would call a command that no longer
exists. The second removes `~/.local/bin/claude` and nothing else. On a native
install, run Anthropic's installer again to put its launcher back.
`~/.config/claude-extras` holds every account's credentials and the routes. Delete
it when you are done with the accounts.
