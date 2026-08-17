# Architecture

## The command

`claude` at `~/.local/bin` is `claude_extras.cli`. It finds the real binary by
walking `PATH` and skipping anything that resolves to itself, falling back to the
newest version under `~/.local/share/claude/versions/`. Then either the first
argument is one of the verbs and that module handles it, or the directory is
routed to an account, `CLAUDE_CONFIG_DIR` is set, and the real binary is exec'd
with the original arguments.

A bare launch is the hot path, so `cli.py` imports nothing beyond the standard
library and the account layout at module level, and each verb's module loads inside
the dispatch that needs it. The verb names are taken out of the real CLI's namespace, so when a
Claude Code release adds one of its own, `claude -- <verb>` hands that call over.

## Routing

`~/.config/claude-extras/routes` maps a project path to an account name, and the
longest matching prefix of the current directory wins. An unmatched directory is
the default account, and for that the variable is unset. Pointing it at `~/.claude`
would make Claude Code look for its state file inside that directory instead of
beside it, and leaving an inherited value alone is how a shell that started in a
routed directory would keep billing that account everywhere it cd's to.
`claude auth login` writes the file, and `claude auth doctor --fix` drops a route
whose directory is gone.

The config directory is named after this tool because `~/.config/claude` is where
a hand-set `CLAUDE_CONFIG_DIR` usually points, and Claude Code then fills it with
its own state.

## Usage data

`<config_dir>/ratelimit.json` is the 5h/7d snapshot `claude status` writes each
turn. It is the only place those numbers exist off session.
`<config_dir>/projects/**/*.jsonl` are the transcripts. Each assistant turn's
`usage` block is read, deduplicated by message id and request id, and grouped by
window, model, day and project. Percentages are grouped by
subscription, read from each account's credentials on every run, since an account
can move between subscriptions.

Parsed turns are cached per file in `<config_dir>/usage-cache.json`, keyed by size
and mtime, next to Claude Code's own `stats-cache.json` and with the same mode and
`version` convention. Rows keep their dedup key, since duplicates cross files and a
per-file sum could not remove them.

## Where things live

| Path | What |
|---|---|
| `~/.local/bin/claude` | the command (uv tool console script) |
| `~/.config/claude-extras/routes` | project to account routes |
| `~/.config/claude-extras/deny` | directories where launching is refused |
| `~/.config/claude-extras/accounts/<name>/` | per-account config and credentials |
| `~/.config/claude-extras/auth/<name>.json` | credential snapshots (`auth save`) |
| `~/.config/claude-extras/state/launches` | logical `$PWD` per launch, for `resume` |
| `~/.claude/` | the default account, owned by Claude Code |
| `<config_dir>/ratelimit.json` | cached plan-burn snapshot |
| `<config_dir>/usage-cache.json` | parsed transcript turns |
| `<project>/chats/` | chat directories, one per conversation |
