# Accounts

An account is a Claude config directory with its own credentials and its own
`settings.json`. The default account is `~/.claude`, used everywhere until a
project says otherwise. A named account `acme` uses
`~/.config/claude-extras/accounts/acme/` as its `CLAUDE_CONFIG_DIR`. On first use
it is seeded from the default. `settings.json` is copied, so the model and the
permissions can diverge, and `CLAUDE.md`, hooks, skills, memory and commands are
shared by symlink.

## Putting a project on an account

Log in to the account from inside the project, the way you would set a git
identity in a work repo:

```
cd ~/work/acme
claude auth login acme      # logs in if needed, and routes this project to acme
claude auth login default   # revert this project to the default account
claude auth list            # accounts and routes, with plan tier
```

From then on, launching `claude` anywhere under that project uses `acme`. The
route is keyed to the project, meaning its git root or the directory itself when
it is not a repo, and stored in `~/.config/claude-extras/routes`. A
`CLAUDE_CONFIG_DIR` set in your shell is ignored from here on, since routing
replaces it on every launch.

For one session under a specific account, whatever the directory routes to:

```
claude --account acme          # this session uses acme
claude -a acme -p "summarize"  # same, non-interactive
```

`--account` also scopes `usage`, `resume`, `search` and `chat`, where it accepts
`all`.

To remove an account, run `claude auth login default` in each project routed to
it, then delete `~/.config/claude-extras/accounts/<name>/` and
`auth/<name>.json`. The snapshot counts as the account still existing, so leaving
it behind keeps the name selectable and the doctor reporting it.

## The model

An account's model lives in its own `settings.json`, seeded from the default and
then free to diverge:

```json
{ "env": { "ANTHROPIC_MODEL": "claude-opus-5" } }
```

## auth

`claude auth` extends the native command. Native subcommands act on the account
the current directory routes to, and anything unrecognised is handed straight
through. `claude auth list` adds the cross-account overview:

```
accounts
  account  tier     login     email
  default  max 20x  10851c19  you@example.com
  acme     max 20x  28daa606  you@example.com
  globex   max 20x  8f3b2db4  you@example.com

routes
* ~/work/acme -> acme
  ~/work/globex -> globex

* governs ~/work/acme/firmware
```

The `*` marks the route governing your current directory. `login` is a fingerprint
of the credential, never the credential itself. It is there because an account is
a directory, not an identity, so the same email can be logged in four times and two
directories can share one login.

`claude auth save <name>` copies the active credentials to
`~/.config/claude-extras/auth/<name>.json`. An account directory restores from that
snapshot the first time it is seeded, which is how you move an account to a new
machine.

### doctor

`claude auth doctor` reports faults that otherwise stay invisible, and exits
non-zero when one is failing. `--fix` repairs what is safe to repair, `--undo`
takes those repairs back out.

- A symlink whose target has disappeared, which reads as "no overrides".
- A credential with no refresh token or no recorded expiry.
- The `claude chat tidy` hook missing, or written at the top level of
  `settings.json` where nothing reads it.
- Two accounts sharing one login, so a token rotation in one leaves the other
  with a stale copy.
- Accounts billing one subscription, so separate accounts are visibly not buying
  separate quota.
- `cleanupPeriodDays` unset or low, which is how transcripts get deleted without
  anyone deciding to. Reported only.
- `hooks` or `statusLine` in an account that drifted from the default account's,
  since `settings.json` is copied once and never resynced. `--fix` copies the
  default's over.
- A `statusLine` with no `refreshInterval`, which lets the usage snapshot go stale.
- A route to a directory that no longer exists, which bills default without a word.
  `--fix` drops it, the one case where anything but `claude auth login` writes the
  routes file.
- Orphaned `auth/*.json` snapshots. They hold credentials, so they are reported and
  left alone.

## Security

Credentials and the routes file live only under `~/.config/claude-extras`, at
mode 700 throughout, and never belong in a repository. The account names alone say
which businesses you hold separate logins for.
