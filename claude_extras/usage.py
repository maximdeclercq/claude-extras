"""The `claude usage` verb, for plan burn, tokens and estimated cost."""

import json
import sys
from datetime import UTC, datetime

from . import cache, identity, report, status
from .accounts import AccountError
from .args import parse

PERIODS = ("daily", "weekly", "monthly")
VIEWS = ("summary", *PERIODS, "projects")


def _parse_since(value):
    """A YYYY-MM-DD cutoff as a local midnight."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone()
    except ValueError:
        raise AccountError(f"--since needs a YYYY-MM-DD date, got '{value}'") from None


def _load(pairs, since, with_turns=True):
    accounts = []
    for name, config_dir in pairs:
        config_dir = str(config_dir)
        accounts.append({
            "name": name,
            "config_dir": config_dir,
            # Peers only complete the subscription, so their transcripts are never read
            "turns": cache.load_turns(config_dir, since=since) if with_turns else [],
            "rc": status.read_snapshot(config_dir),
            "identity": identity.read(config_dir),
        })
    return accounts


def _emit(obj, as_json, text):
    if as_json:
        json.dump(obj, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    else:
        print(text)


HELP = """Usage: claude usage [options] [command]

Report plan usage, token spend and time per account

Options:
  -a, --account <name>    Report one account, or "all" (default: all)
      --all               Shorthand for --account all
      --since <date>      Only count usage on or after YYYY-MM-DD
      --json              Output as JSON
  -h, --help              Display help for command

Commands:
  summary                 Plan burn per subscription, attribution per account (default)
  daily                   Per-day totals
  weekly                  Per-ISO-week totals
  monthly                 Per-month totals
  projects                Totals per project directory
  help                    Display help for command

Examples:
  # Everything, grouped by subscription and account:
  claude usage

  # One account's own windows, token split and models:
  claude usage --account acme

  # What each project cost this month:
  claude usage projects --since 2026-08-01
"""


FLAGS = {"--account": str, "-a": "--account", "--all": None, "--since": str, "--json": None}


def main(argv=None):
    """Parse the arguments, resolve the account scope, and render."""
    from .accounts import select_accounts

    options, rest = parse(list(argv or []), FLAGS, "usage")
    if options["--help"]:
        print(HELP, end="")
        return 0
    view = "summary"
    for arg in rest:
        if arg not in VIEWS:
            raise AccountError(f"claude usage: unexpected argument '{arg}'")
        view = arg
    scope = "all" if options["--all"] else options["--account"] or "all"

    focused = select_accounts(scope)
    names = {name for name, _ in focused}
    peers = [pair for pair in select_accounts("all") if pair[0] not in names]
    return render(view, focused, peers, options["--since"], options["--json"])


def render(view, focused, peers, since_text=None, as_json=False):
    """Load the selected accounts and emit one view of them."""
    since = _parse_since(since_text)
    accounts = _load(focused, since)
    peer_accounts = _load(peers, None, with_turns=False)
    now = datetime.now(UTC)
    use_color = sys.stdout.isatty() and not as_json

    if view == "summary":
        # One account wants its own windows and model split, several want the
        # attribution table
        if len(accounts) == 1:
            model = report.build_account(accounts[0], now, peer_accounts)
            _emit(model, as_json, report.render_account(model, use_color))
        else:
            model = report.build(accounts, now, since)
            _emit(model, as_json, report.render_summary(model, use_color))
        return 0

    multi = len(accounts) > 1
    sections, blocks = [], []
    for account in accounts:
        label = account["name"] if multi else None
        if view in PERIODS:
            rows = report.group_period(account["turns"], view)
            blocks.append(report.render_period(view, rows, account=label))
        else:
            rows = report.group_projects(account["turns"])
            blocks.append(report.render_projects(rows, account=label))
        sections.append({"account": account["name"], "rows": rows})

    payload = ({"view": view, "accounts": sections} if multi
               else {"view": view, "rows": sections[0]["rows"]})
    _emit(payload, as_json, "\n\n".join(blocks))
    return 0
