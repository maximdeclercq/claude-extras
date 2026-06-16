"""Argument parsing shared by every verb: one table per verb, one loop here."""

from .accounts import AccountError


def take_value(argv, index, flag):
    """The value after a flag, or a clean error when the flag ends the line."""
    if index + 1 >= len(argv):
        raise AccountError(f"{flag} needs a value")
    return argv[index + 1]


def take_count(value):
    """A positive row count, or a usage error. int() alone raises ValueError."""
    if not value.isdigit() or int(value) < 1:
        raise AccountError(f"--limit needs a positive number, got '{value}'")
    return int(value)


def parse(argv, spec, verb):
    """(options, positionals) from argv, by a table of what each flag takes.

    A flag maps to a converter for its value, or to None for a switch; a short
    form maps to its long form. A value comes as `--flag value` or `--flag=value`.
    -h, --help and the word help set options["--help"] and end the parse, so a
    mistyped flag after them never hides the help. Anything else that looks like a
    flag is an error, and the rest are positionals, in order.
    """
    longs = {flag: kind for flag, kind in spec.items() if not isinstance(kind, str)}
    options = {flag: (False if kind is None else None) for flag, kind in longs.items()}
    options["--help"] = False
    rest, index = [], 0
    while index < len(argv):
        arg = argv[index]
        if arg in ("-h", "--help", "help"):
            options["--help"] = True
            return options, rest
        name, has_inline, inline = arg.partition("=")
        name = spec[name] if isinstance(spec.get(name), str) else name
        if name not in longs:
            if arg.startswith("-"):
                raise AccountError(f"claude {verb}: unexpected argument '{arg}'")
            rest.append(arg)
            index += 1
            continue
        kind = longs[name]
        if kind is None:
            options[name] = True
            index += 1
        elif has_inline:
            options[name] = kind(inline)
            index += 1
        else:
            options[name] = kind(take_value(argv, index, arg))
            index += 2
    return options, rest
