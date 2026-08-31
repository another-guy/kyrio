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
import os
import pathlib
import sys

# Run as a file (``kyrio/scripts/kyrio/__main__.py``) and only this directory is
# importable; the package lives one level up. Bootstrapping here rather than
# through PYTHONPATH keeps the shims free of environment variables and of the
# separator difference between platforms.
_SCRIPTS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from kyrio import (capability, cli, config, emit, ingest, probe,  # noqa: E402 -- follows bootstrap
                   repo, scm)

USAGE = """\
kyrio repo map             entry points, module boundaries, build and test commands
kyrio repo churn           what changed most, and how often
     [--since 90d] [--top 25] [--path <p>]
kyrio repo owners [<path>] ownership, from an ownership file or from history
kyrio repo blame <path>:<line>[-<line>]
kyrio probe                what this machine has; writes nothing
kyrio probe servers        connected servers, and the state of each
kyrio probe record         record the interpreter for the launcher to reuse
     [--set <capability>=<transport>[:<value>]]  ... repeatable
     [--servers]           also cache what discovery last saw
     [--output-root <path>]  where generated artifacts go
kyrio probe permission [--apply]
                           the one permission rule the broker needs
kyrio scm pr diff <id>     the diff for one change under review
kyrio scm log [--since 7d] what merged, and when
kyrio scm pr comment <id> --file <p> --line <n> -f <body> [--post]
                           drafts by default; --post sends it
kyrio caps                 what this machine can reach, and what is missing
kyrio config explain       effective configuration, and the layer behind each value
kyrio ingest <kind> --file <path>
                           read a file the broker did not produce (S3)
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
    resolutions = capability.resolve_all(resolved)

    counts = dict.fromkeys(capability.STATUSES, 0)
    for resolution in resolutions:
        counts[resolution.status] += 1

    rows = [(r.capability, r.transport or "--", r.status) for r in resolutions]
    payload = cli.table(("CAPABILITY", "TRANSPORT", "STATUS"), rows)

    # Every gap carries the specific fix rather than one hint covering all of
    # them: "nothing is configured" and "configured for something this pack
    # has no adapter for" are different problems with different owners.
    gaps = {}
    for r in resolutions:
        if not r.usable and r.remediation:
            gaps.setdefault(r.remediation, []).append(r.capability)
    if gaps:
        payload += "\nGAPS\n" + "".join(
            "  %s: %s\n" % (", ".join(names), remediation)
            for remediation, names in gaps.items())
        payload += ("\nStatus is what configuration says, not what was last "
                    "probed.\n")

    probed = probe.last_probe()
    if probed:
        connected = [s for s in probed.get("servers") or []
                     if s.get("state") == probe.CONNECTED]
        payload += "\nLAST PROBED  %s, %d of %d server(s) connected\n" % (
            probed["at"], len(connected), len(probed.get("servers") or []))
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
    lines.append(cli.table(("KEY", "VALUE", "FROM"), rows).rstrip("\n"))

    return emit.ok("config", "\n".join(lines) + "\n",
                   layers=len(resolved.layers), keys=len(rows))


def _render(value):
    """A config value as one line."""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(", ", ": "), ensure_ascii=False)


# ---------------------------------------------------------------- repo


def repo_command(args):
    rest = list(args.rest)
    verb = rest[0] if rest else None
    handler = REPO_VERBS.get(verb)
    if handler is None:
        return emit.error("usage: kyrio repo <map|churn|owners|blame>",
                          known=sorted(REPO_VERBS))
    try:
        result = handler(args, rest[1:])
    except repo.RepoError as exc:
        return emit.error(str(exc), kind="repo")
    return emit.ok(result.kind, result.payload, transport="local",
                   **result.meta)


def _cwd(args):
    return pathlib.Path(args.cwd).resolve() if args.cwd else pathlib.Path.cwd()


def _repo_map(args, rest):
    # Configuration beats detection, so a repository with an unusual build
    # answers correctly without the detector learning about it.
    conventions = _resolve(args).get("conventions") or {}
    return repo.repo_map(_cwd(args), conventions=conventions)


def _repo_churn(args, rest):
    parser = cli.Parser(prog="kyrio repo churn")
    parser.add_argument("--since", default=repo.DEFAULT_WINDOW)
    parser.add_argument("--top", type=int, default=25)
    parser.add_argument("--path")
    flags = parser.parse_args(rest)
    return repo.churn(_cwd(args), window=flags.since, top_n=flags.top,
                      path=flags.path)


def _repo_owners(args, rest):
    parser = cli.Parser(prog="kyrio repo owners")
    parser.add_argument("path", nargs="?")
    flags = parser.parse_args(rest)
    return repo.owners(_cwd(args), path=flags.path)


def _repo_blame(args, rest):
    parser = cli.Parser(prog="kyrio repo blame")
    parser.add_argument("location", nargs="?")
    flags = parser.parse_args(rest)
    return repo.blame(_cwd(args), flags.location)


# --------------------------------------------------------------- probe


def probe_command(args):
    rest = list(args.rest)
    verb = rest[0] if rest else "report"
    if verb not in PROBE_VERBS:
        return emit.error("usage: kyrio probe [servers|record|permission]",
                          known=sorted(PROBE_VERBS))
    try:
        result = PROBE_VERBS[verb](args, rest[1:])
    except probe.ProbeError as exc:
        return emit.error(str(exc), kind="probe")
    except config.ConfigError as exc:
        return emit.error(str(exc), kind="config")
    return emit.ok(result.kind, result.payload, transport="local",
                   **result.meta)


def _probe_report(args, rest):
    start = pathlib.Path(args.cwd).resolve() if args.cwd else None
    return probe.report(start, discovery=probe.discover_servers(),
                        tools=probe.probe_tools())


def _probe_servers(args, rest):
    """Discovered servers, as fact. Which one serves which capability is a
    judgment, and judgment belongs to the skill above (I9)."""
    discovery = probe.discover_servers()
    if discovery.problem:
        raise probe.ProbeError(discovery.problem)
    rows = [(s.name, s.state, s.prefix, s.address)
            for s in discovery.servers]
    payload = (cli.table(("NAME", "STATE", "PREFIX", "ADDRESS"), rows) if rows
               else "  none configured\n")
    return repo.Result("servers", payload,
                       servers=len(discovery.servers),
                       connected=len(discovery.connected))


def _probe_record(args, rest):
    """Record the interpreter, and any capability the caller decided on.

    Assignments arrive already decided. Which discovered server serves which
    capability cannot be worked out from the listing -- it takes knowing what
    the server is -- so the skill above proposes and this validates and
    writes. Nothing is inferred here, and nothing invalid is stored: an entry
    that could not resolve later is refused now.
    """
    parser = cli.Parser(prog="kyrio probe record")
    parser.add_argument("--set", action="append", dest="assignments")
    parser.add_argument("--servers", action="store_true")
    parser.add_argument("--output-root", dest="output_root")
    flags = parser.parse_args(rest)

    entries = {}
    for assignment in flags.assignments or []:
        try:
            name, entry = capability.parse_assignment(assignment)
        except capability.SpecError as exc:
            raise probe.ProbeError(str(exc)) from exc
        entries[name] = entry

    discovery = probe.discover_servers() if flags.servers else None
    return probe.record(capabilities=entries, discovery=discovery,
                        output_root=flags.output_root)


def _probe_permission(args, rest):
    parser = cli.Parser(prog="kyrio probe permission")
    parser.add_argument("--apply", action="store_true")
    flags = parser.parse_args(rest)
    return probe.permission(apply=flags.apply)


# ------------------------------------------------------------------ scm


def _capability(args, name):
    """Resolve one capability, or say why it cannot be used.

    Every transport-backed noun goes through here so that a gap is reported
    the same way everywhere. A noun that composed its own wording would give
    the same machine two different explanations of one problem.
    """
    resolution = capability.resolve(name, _resolve(args))
    if not resolution.usable:
        raise _Unusable(resolution)
    return resolution


class _Unusable(Exception):
    def __init__(self, resolution):
        super().__init__(resolution.remediation or "")
        self.resolution = resolution


def scm_command(args):
    rest = list(args.rest)
    verb = rest[0] if rest else None
    handler = SCM_VERBS.get(verb)
    if handler is None:
        return emit.error("usage: kyrio scm pr diff <id>",
                          known=sorted(SCM_VERBS))
    try:
        return handler(args, rest[1:])
    except _Unusable as exc:
        return emit.unavailable("scm", exc.resolution.remediation
                                or "not usable on this machine")
    except scm.ScmError as exc:
        return emit.error(exc.message, exc.detail, kind="scm")


def _scm_pr(args, rest):
    verb = rest[0] if rest else None
    handler = PR_VERBS.get(verb)
    if handler is None:
        return emit.error("usage: kyrio scm pr [diff|comment] <id>",
                          known=sorted(PR_VERBS))
    return handler(args, rest[1:])


def _scm_pr_diff(args, rest):
    parser = cli.Parser(prog="kyrio scm pr diff")
    parser.add_argument("id", nargs="?")
    flags = parser.parse_args(rest)
    if not flags.id:
        return emit.error("usage: kyrio scm pr diff <id>")

    resolution = _capability(args, "scm")
    if scm.requires_manual(resolution):
        # A person is the transport, deliberately. The instructions end at
        # the inbound door rather than asking for a paste (S3).
        return emit.manual("scm", scm.manual_diff_instructions(flags.id))

    result = scm.pr_diff(resolution, flags.id, cwd=_cwd(args))
    return emit.ok(result.kind, result.payload,
                   transport=resolution.transport, **result.meta)


def _scm_pr_comment(args, rest):
    """Draft by default. ``--post`` is the only thing that sends (I6)."""
    parser = cli.Parser(prog="kyrio scm pr comment")
    parser.add_argument("id", nargs="?")
    parser.add_argument("--file")
    parser.add_argument("--line")
    parser.add_argument("-f", dest="body")
    parser.add_argument("--post", action="store_true")
    flags = parser.parse_args(rest)
    if not flags.id or not flags.body:
        return emit.error(
            "usage: kyrio scm pr comment <id> --file <path> --line <n> "
            "-f <body-file> [--post]")

    comment = scm.read_comment(flags.id, flags.file, flags.line, flags.body)

    resolution = _capability(args, "scm")
    if scm.requires_manual(resolution):
        return emit.manual("scm", scm.manual_comment_instructions(comment))

    result = scm.pr_comment(resolution, comment, flags.body,
                            post=flags.post, cwd=_cwd(args))
    if not result.meta.get("posted"):
        return emit.draft(result.kind, result.payload,
                          transport=resolution.transport, **result.meta)
    return emit.ok(result.kind, result.payload,
                   transport=resolution.transport, **result.meta)


# -------------------------------------------------------------- ingest


def ingest_command(args):
    """The single inbound door (S3).

    ``--file`` rather than a positional argument holding the content: a payload
    on the command line is bounded by the platform's argv limit, would need
    quoting the caller cannot reliably produce, and would be visible in process
    listings. A path is small, exact, and the same on every platform.
    """
    parser = cli.Parser(prog="kyrio ingest")
    parser.add_argument("kind", nargs="?")
    parser.add_argument("--file")
    flags = parser.parse_args(args.rest)
    try:
        result = ingest.ingest(flags.kind, flags.file)
    except ingest.IngestError as exc:
        return emit.error(str(exc), kind="ingest",
                          known=sorted(ingest.KINDS))
    return emit.ok(result.kind, result.payload, transport="ingest",
                   **result.meta)


PROBE_VERBS = {
    "report": _probe_report,
    "record": _probe_record,
    "permission": _probe_permission,
    "servers": _probe_servers,
}


def _scm_log(args, rest):
    parser = cli.Parser(prog="kyrio scm log")
    parser.add_argument("--since", default=DEFAULT_LOG_WINDOW)
    flags = parser.parse_args(rest)
    since, label = repo.since_date(flags.since)

    resolution = _capability(args, "scm")
    if scm.requires_manual(resolution):
        return emit.manual("scm", scm.manual_log_instructions(since, label))

    result = scm.log(resolution, since, label, cwd=_cwd(args))
    return emit.ok(result.kind, result.payload,
                   transport=resolution.transport, **result.meta)


#: Short on purpose. "What shipped" is nearly always a question about the last
#: few days; ``repo churn`` answers the long-range version of it.
DEFAULT_LOG_WINDOW = "7d"


PR_VERBS = {
    "diff": _scm_pr_diff,
    "comment": _scm_pr_comment,
}


SCM_VERBS = {
    "pr": _scm_pr,
    "log": _scm_log,
}


REPO_VERBS = {
    "map": _repo_map,
    "churn": _repo_churn,
    "owners": _repo_owners,
    "blame": _repo_blame,
}


COMMANDS = {
    "caps": caps,
    "config": config_command,
    "ingest": ingest_command,
    "probe": probe_command,
    "repo": repo_command,
    "scm": scm_command,
}


if __name__ == "__main__":
    sys.exit(main())
