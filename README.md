# claude-extras

A drop-in `claude` that gives the official CLI more than one account.

Claude Code keeps everything in one config directory, so a second login means
swapping `CLAUDE_CONFIG_DIR` by hand, and swapping it back. This routes a
directory to an account instead. Log in once from inside a project, and every
session started there uses that account.

With more than one account, each built-in command sees only the account it was
started in. The other verbs look across all of them.

| | native | here |
|---|---|---|
| `claude auth` | one login | accounts, routing, health checks |
| `claude usage` | `/usage`, in-session | every account, on demand, per day/model/project |
| `claude resume` | `--resume`, this account | every account, by number |
| `claude search` | titles only | what was actually said |
| `claude chat` | none | conversations that are not about a repository |
| `claude spawn` | none | a session in a new terminal window |

`r`, `s`, `c` and `u` are short forms of the four you type most. `claude status`
is the status line command that caches the plan burn `usage` reads.

## Install

```
git clone https://github.com/maximdeclercq/claude-extras
uv tool install --force --editable ./claude-extras
claude auth doctor --fix
```

Needs [uv](https://docs.astral.sh/uv/). The package itself has no dependencies.

`uv tool install` puts `claude` in `~/.local/bin`, shadowing the real binary, so
there is no second command to learn. Anthropic's native installer keeps its own
launcher at that path, which is what `--force` replaces; the binaries under
`~/.local/share/claude/versions` stay put and the newest one is what runs. With
any other install, put `~/.local/bin` ahead of it on your `PATH`.

`claude auth doctor --fix` writes the status line and the session hooks into
`~/.claude/settings.json` and prints each change. It is the same command you
run later when something looks wrong, so there is no separate installer to keep
in step with it.

To take it out again:

```
claude auth doctor --undo
uv tool uninstall claude-extras
```

Written against Claude Code 2.1.259. It reads files Claude Code does not
document (the transcript JSONL, the status line's `ratelimit.json`, the native
installer's `versions/` layout), so a release that changes one of them breaks a
verb, never a session.

## Accounts

The default account is `~/.claude`, used everywhere automatically. To put a
project on a different one, log in to it from inside the project.

```
cd ~/work/acme
claude auth login acme      # routes this project to the acme account
claude auth login default   # reverts it
claude auth list            # accounts and routes
claude --account acme       # one-off, ignoring routing for this session
```

The route is keyed to the project's git root and lives in a tool-managed
`~/.config/claude-extras/routes` file you never edit. An account's model lives in
its own `settings.json`, copied from the default on first use so it can diverge.
Native `claude auth login/logout/status` keep working and act on the routed
account.

## Usage

```
claude usage                 # every account
claude usage --account acme  # one account's own windows, token split and models
claude usage daily|weekly|monthly
claude usage projects        # totals per project directory
claude usage --since 2026-08-01
claude usage --json
```

```
plan  max 20x  default, globex.io, acme.dev, personal  4 logins   as of 18:44
  5h  [##############--------]  65%  resets 21:20
  7d  [########--------------]  37%  resets Sun 06:00

  account    sessions  time  tokens  est cost
  default         217   61h  287.7M      $562
  globex.io        43  154h    1.4B     $2821
  acme.dev        383  154h    3.4B     $7623
  personal        270  123h  638.8M     $1238
  total           913  383h    5.8B    $12244
```

Plan burn is reported once per subscription, since accounts on the same plan
share one meter. Tokens, cost and time are per account, from the local
transcripts, priced at list rates; they say what the work would have cost on the
API, not what you were billed. How each number is derived, and what it is not, is
in [commands.md](docs/commands.md#usage).

## The rest

Same shape throughout. List, pick by number, act.

```
claude resume                      # resumable sessions, newest first, every account
claude resume 3                    # resume #3, in its own directory and account
claude search "link-dest"          # find one by what was said in it
claude search --mine "why does"    # only your side of the conversation
claude chat new                    # a conversation that is not about a repository
claude chat export 3 -o out.md     # markdown, or one self-contained html file
```

Claude Code's own session picker searches titles and summaries, and only within
the account it was started in. `search` reads what was said, and both cross
accounts.

## How it works

`claude` is a Python console script that resolves the account for the current
directory, sets `CLAUDE_CONFIG_DIR`, and either handles one of its own verbs or
execs the real binary. A bare launch imports almost nothing, since that cost is
paid on every session.

Those verb names are reserved out of the real CLI's namespace. If a Claude Code
release adds one of its own, `claude -- <verb>` gives up this tool's verbs for
that call and hands everything over.

Parsed transcripts are cached per file next to Claude Code's own
`stats-cache.json`, and only files that changed are read again.

## Scope

Linux, and one person's daily driver. Some verbs call out to `git`, a terminal
emulator, `hyprctl` or a launcher such as wofi. A missing one costs you that
feature and nothing else, and [install.md](docs/install.md#requirements) has the
table. Cost figures are estimates at list prices.

Full documentation is at https://maximdeclercq.github.io/claude-extras/, built
from `docs/`.
