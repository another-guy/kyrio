"""Verb dispatch for the ``kyrio`` broker.

Everything a skill can ask for arrives here as a noun and a verb, is answered
deterministically, and leaves through ``emit`` (S1). There is deliberately no
passthrough verb and no way to name a command to run: the broker's single
permission rule is only narrow because nothing here executes caller-supplied
input (I5).

Only implemented commands appear in ``kyrio help``. A noun that is not yet
built is an error naming what does exist, never a silent no-op.
"""

import argparse
import json
import pathlib
import sys

from kyrio import cli, config, emit

USAGE = """\
kyrio caps                 what this machine can reach, and what is missing
kyrio config explain       effective configuration, and the layer behind each value
kyrio help                 this text

Global options
  --cwd <path>             resolve configuration as if run from here
"""


def main(argv=None):
    """Parse, dispatch, and return the process exit code."""
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        parser = cli.Parser(prog="kyrio")
        parser.add_argument("--cwd")
        parser.add_argument("command", nargs="?")
        parser.add_argument("rest", nargs=argparse.REMAINDER)
        args = parser.parse_args(argv)

        if args.command in (None, "help"):
            return _help(args)
        handler = COMMANDS.get(args.command)
        if handler is None:
            return emit.error(
                "unknown command: %s" % args.command,
                USAGE, known=sorted(COMMANDS) + ["help"])
        return handler(args)
    except cli.UsageError as exc:
        return emit.error(exc.message, USAGE)
    except config.ConfigError as exc:
        return emit.error(str(exc), kind="config")


def _resolve(args):
    start = pathlib.Path(args.cwd).resolve() if args.cwd else None
    return config.resolve(start=start)


def _help(args):
    if args.command is None and not args.rest:
        # A bare invocation is a caller mistake, not a request for help.
        return emit.error("no command given", USAGE)
    return emit.ok("help", USAGE)


# ---------------------------------------------------------------- caps


def caps(args):
    """Self-report: what this machine can reach.

    A report about *this* machine and nothing else. There is no comparison,
    no baseline, and no notion of a second environment (I4).
    """
    resolved = _resolve(args)
    configured = dict(config.INTRINSIC)
    for name, entry in (resolved.get("capabilities") or {}).items():
        configured[name] = entry

    rows = []
    counts = {"ready": 0, "configured": 0, "unconfigured": 0, "unavailable": 0}
    for name in config.CAPABILITIES:
        entry = configured.get(name)
        transport = entry.get("transport") if isinstance(entry, dict) else entry
        if name in config.INTRINSIC:
            status = "ready"
        elif transport in (None, ""):
            status = "unconfigured"
        elif transport == "unavailable":
            status = "unavailable"
        else:
            status = "configured"
        counts[status] += 1
        rows.append((name, transport or "--", status))

    payload = _table(("CAPABILITY", "TRANSPORT", "STATUS"), rows)
    if counts["unconfigured"] or counts["unavailable"]:
        payload += (
            "\nStatus is what configuration says, not what was last probed.\n"
            "Run /kyrio:setup to probe this machine and record the result.\n")
    return emit.ok("caps", payload, layers=len(resolved.layers), **counts)


# -------------------------------------------------------------- config


def config_command(args):
    verb = args.rest[0] if args.rest else None
    if verb != "explain":
        return emit.error(
            "usage: kyrio config explain",
            known=["explain"])
    return _config_explain(args)


def _config_explain(args):
    """Every effective value alongside the layer that supplied it.

    JSON carries no comments, so provenance is what explains a value. Layers
    are numbered and the numbers are reused in the FROM column, which keeps a
    long path off every row.
    """
    resolved = _resolve(args)
    if not resolved.layers:
        return emit.ok(
            "config",
            "No configuration layers found.\n"
            "Run /kyrio:setup to write the machine layer.\n",
            layers=0, keys=0)

    index = {layer.label: str(i) for i, layer in enumerate(resolved.layers, 1)}
    lines = ["LAYERS"]
    for number, layer in enumerate(resolved.layers, 1):
        lines.append("  [%d] %s" % (number, layer.label))
    lines.append("")

    rows = []
    for path in sorted(resolved.provenance):
        contributors = resolved.provenance[path]
        origin = "[%s]" % ",".join(index[c] for c in contributors)
        rows.append((path, _render(resolved.get(path)), origin))
    lines.append(_table(("KEY", "VALUE", "FROM"), rows).rstrip("\n"))

    return emit.ok("config", "\n".join(lines) + "\n",
                   layers=len(resolved.layers), keys=len(rows))


def _render(value):
    """A config value as one line."""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(", ", ": "), ensure_ascii=False)


def _table(headers, rows, indent="  "):
    """Fixed-width columns; the last column is never padded."""
    columns = len(headers)
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def line(cells):
        parts = [str(c).ljust(widths[i]) if i < columns - 1 else str(c)
                 for i, c in enumerate(cells)]
        return (indent + "  ".join(parts)).rstrip()

    return "\n".join([line(headers)] + [line(r) for r in rows]) + "\n"


COMMANDS = {
    "caps": caps,
    "config": config_command,
}


if __name__ == "__main__":
    sys.exit(main())
