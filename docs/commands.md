# Commands

`auth` is on the [accounts](accounts.md#auth) page. Everything the real CLI
defines passes through untouched.

| verb | short | what it adds |
|---|---|---|
| `usage` | `u` | plan burn, tokens and estimated cost, per account |
| `resume` | `r` | sessions across every account, resumed by number |
| `search` | `s` | a session found by what was said in it |
| `chat` | `c` | conversations that are not about a repository |
| `spawn` | | a session in a new terminal window |
| `status` | | the status line, and the snapshot `usage` reads |

## usage

```
claude usage                 # every account
claude usage --account acme  # one account in detail
claude usage daily|weekly|monthly
claude usage projects        # totals per project directory
claude usage --since 2026-08-01
claude usage --json
```

The 5h/7d percentages exist only in the payload Claude Code pushes to the status
line each turn, so `claude status` caches each one in
`<config_dir>/ratelimit.json` and this reads the freshest. A snapshot older than
its own window is marked `(stale)`. Tokens, cost and time come from the transcripts
under `<config_dir>/projects/`.

Plan burn is reported once per subscription. Several accounts can hold separate
logins that bill one plan, and those share one meter, so a percentage per account
would show four budgets where there is one. The grouping is read from the
credentials on each run. Tokens, cost and time stay per account, which is what
answers what a client's work cost. Time is the union of session spans, not their
sum, since subagents run beside the session that started them.

Cost is an estimate. A transcript carries no billed amount, so list prices are
applied to the token counts, per model family, and a model with no published price
is counted in tokens and shown as `+N unpriced`.
Turns are deduplicated across files, since resuming a session copies earlier
records into the new transcript.

## resume

```
claude resume                # ten most recent, across every account
claude resume --account acme # one account
claude resume --here         # only sessions started in this repository
claude resume --limit 40
claude resume 3              # resume #3, in its directory and its account
claude resume export 3 -o session.md
claude resume --json
```

```
recent sessions  all accounts
  #    age  account  directory           title                                branch
    1   2m  acme     acme/firmware       Fix the release deploy step          wip-boot-order
    2   1h  default  work/bench-harness  Add a smoke test for the ingest job  oeqa-split
    3   4h  acme     acme/handbook       Review and rethink the draft ADRs

resume:  claude resume <#>
```

Native `claude --resume` offers a picker for the account you are in. This one lists
every account's sessions in one table and sets `CLAUDE_CONFIG_DIR` before
resuming, which native cannot, because each config directory knows only its own
sessions. The branch prints only when it is not `main`, `master` or `HEAD`.

Only sessions you could reopen are listed. Claude Code writes a transcript for
every subagent, and a headless `claude -p` run from a hook or a script looks like a
session at the file level. Both are skipped, by the `entrypoint` Claude Code stamps
on a session started for a terminal, and so is a transcript rebuilt from
`history.jsonl` after the retention sweep, which holds prompts and no replies. The
title is the one Claude Code generated, else the first prompt you typed.

The directory shown is the one you typed. Claude Code resolves symlinks before
writing a transcript, so each launch logs the shell's `$PWD` to
`~/.config/claude-extras/state/launches` and `resume` prefers that.

## search

```
claude search "link-dest"                 # every account
claude search --mine "why does"           # only what you asked
claude search --regex 'CVE-[0-9]+' -a acme
claude search --here "flash"              # only this repository
claude search "link-dest" 2               # resume match #2
```

```
matches  personal
  #    age  title                              match
    1  13h  Backup design for the config tree  ...rsync `--link-dest` generations so it stays...
    2  12d  Usage tracking across accounts     ...a curated `--link-dest` for what survives...

resume:  claude search <pattern> <#>
```

What is matched is the readable text of what you and Claude said. A pattern that
only appears inside a tool call, a file path or JSON escaping does not count.
`--mine` narrows to your side, for when you
remember asking about something but not the answer. Subagent transcripts and
headless runs are skipped, by the rule `resume` uses.

There is no index. Each transcript is rejected on raw bytes before being decoded,
which keeps a full scan tolerable. 4,807 transcripts, 3.0 GB, took about eleven
seconds cold.

## chat

```
claude chat                     # this account's chats, newest first
claude chat 3                   # resume chat #3
claude chat new                 # start one; the name comes later
claude chat new tax return      # or name it now
claude chat account             # the account a new chat here would use
claude chat --json              # the listing, for a script
claude chat export 3 -o tax.md
```

A chat is an ordinary session whose working directory is its own folder under
`<project>/chats`, so the conversation's attachments and artifacts have somewhere
to live. `resume`, `usage` and your backups treat it like any other session.

```
~/work/acme/chats/2026-03-08-tax-return-2025/
    attachments/
    artifacts/
    files/
```

Which account a chat lands in is decided once, and every entry point uses the same
rule. `--account NAME` if you pass it, else the account the current directory
routes to, else the account the focused window's directory routes to, else
`default`. So `claude chat new` from a terminal in a client's project files the
chat under that client, and a status bar follows the window you are looking at.
The chats directory is the account's route plus `/chats`. `default` has no route,
so its chats sit in the directory the routes have in common, or `~/chats` while
there are none. A short `CLAUDE.md` is written there once, telling Claude Code the
directory is not a project. File a chat by who a leak would harm.

A new chat starts as `<date>-untitled-<HHMM>`. A turn or two in, Claude Code writes
a title into the transcript, and the `claude chat tidy` hook renames the directory
to match when the chat ends. Renaming moves the chat directory, its transcript
directory and the `cwd` inside every transcript record together, and only once no
other Claude Code session is running in it.

`export` writes one chat as markdown or as one self-contained HTML file. Tool calls
are dropped, since tool results carry file contents and command output from your
machine. `--include-tools` adds the names of the tools that ran, never their
output. The same options work on `claude resume export`.

`claude chat account` and `claude chat --json` are for scripts. `contrib/waybar/`
builds a bar button and a launcher menu on them.

## spawn

```
claude spawn                          # in the focused window's directory
claude spawn ~/work/acme              # somewhere specific
claude spawn ~/work/acme --resume ID  # pick a session back up
claude spawn --account acme           # override routing for this window
```

With no directory it asks `hyprctl` for the focused window and walks from that
process to its newest descendant, whose working directory is the shell's. On any
other desktop, and whenever that finds nothing, it uses your home directory.

The terminal is `$CLAUDE_TERMINAL`, then `~/.config/claude-extras/terminal`, then
the first of `foot`, `alacritty`, `kitty`, `wezterm` or `xterm` installed. Every
window carries the app id `claude-code`, so a compositor rule can key on that
instead of the terminal name. `xterm` is the exception, since its `-class` also
changes which X resources apply.

## status

Piped Claude Code's status line payload, it renders the line under the prompt and
writes the 5h/7d snapshot `usage` reads. Typed at a terminal, it prints the last
snapshot for the account you are in.

```
Opus high | ctx 8% | 5h 23% (resets 14:00) | 7d 41% | default
```

The fields are model and effort, context used, 5h burn with its reset time, 7d
burn, and the account. Percentages turn yellow at 70% and red at 90%.
`claude auth doctor --fix` wires it in with a `refreshInterval`, which keeps the
snapshot fresh between messages, and that is what lets `usage` report percentages
when no session is running. The payload carries `rate_limits` only for Pro, Max and
Team accounts and only after the first API response, so the fields read `-` until
then.
