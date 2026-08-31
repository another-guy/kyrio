"""What this machine has, and recording it.

Probing is **by execution, never by presence**. A name resolving on ``PATH``
proves nothing: a placeholder shim resolves and does nothing useful, which is
the ordinary state of ``python`` on a fresh Windows install.

Nothing here installs software and nothing here runs an authentication flow.
Installing on a managed machine is a decision with an owner, and a script
cannot know what that machine permits. The report prints the exact command and
stops.

Every write is idempotent and additive. Re-running must never discard a value a
person put there by hand, so each writer reads what exists, replaces only the
keys it owns, and leaves the rest untouched.

This module returns results; it never prints. ``__main__`` emits them (S1).
"""

import datetime
import json
import pathlib
import re
import subprocess
import sys

from kyrio import capability, config, providers
from kyrio.cli import table
from kyrio.repo import Result

MINIMUM_PYTHON = (3, 12)

#: Interpreter names to try, in order. ``py -3`` precedes ``python`` because on
#: Windows ``python`` is usually an app execution alias that resolves and does
#: nothing. Each entry is an argument list, never a shell string (I5).
INTERPRETER_CANDIDATES = [
    ["python3"],
    ["py", "-3"],
    ["python"],
]

VERSION_PROGRAM = (
    "import sys; "
    "print('%d.%d.%d' % sys.version_info[:3]); "
    "print(sys.executable)")

#: Long enough for a cold start on a slow machine, short enough that a binary
#: which never answers does not hold up a session. A probe that times out is a
#: probe that failed: waiting longer would not change the report.
PROBE_TIMEOUT = 20

#: How a probe ended. Kept apart because they have different fixes: nothing to
#: run means install something, and ran-and-refused usually means log in.
MISSING = "missing"
FAILED = "failed"
ANSWERED = "answered"
TIMED_OUT = "timed out"

#: Connected-server discovery. Preferred over reading configuration files
#: because only this distinguishes connected from needs-auth from failed, and
#: that distinction is the entire reason setup is worth re-running.
SERVER_LIST = ["claude", "mcp", "list"]

CONNECTED = "connected"
NEEDS_AUTH = "needs auth"
PENDING = "pending"
UNREACHABLE = "unreachable"
UNKNOWN = "unknown"

#: What a shipped adapter can do on this machine. Two probes, never one:
#: installed and signed in are different states, and the remedy for each has a
#: different owner -- an install someone may not be permitted to perform, and a
#: sign-in nobody but the user can do.
AUTHENTICATED = "authenticated"
UNAUTHENTICATED = "not authenticated"
NOT_INSTALLED = "not installed"
BROKEN = "does not answer"

#: Classified on the words, never on the mark printed beside them. Those marks
#: are non-ASCII and do not survive a console codepage that cannot encode them
#: -- the same failure the report stream is configured against. Order matters:
#: the first phrase found wins, so the more specific phrases come first.
SERVER_STATES = (
    (NEEDS_AUTH, ("needs authentication", "not authenticated",
                  "authentication required", "needs auth")),
    (PENDING, ("pending",)),
    (UNREACHABLE, ("failed", "error", "disconnected", "unreachable",
                   "timed out", "timeout")),
    (CONNECTED, ("connected",)),
)

#: Where the machine layer and its shell-readable mirror live.
STATE_DIR = pathlib.Path.home() / ".claude" / "kyrio" / "state"
INTERPRETER_FILE = STATE_DIR / "interpreter"

#: What discovery last saw. Cached because discovery health-checks every
#: server over the network, and because "status is what configuration says"
#: is only half an answer without the date somebody last checked.
SERVERS_FILE = STATE_DIR / "servers.json"
SETTINGS_FILE = pathlib.Path.home() / ".claude" / "settings.json"

#: One rule, forever. It names the bare command because the plugin's ``bin/``
#: is on the Bash tool's PATH, and a path-based rule would name a versioned
#: directory that changes on the next update.
PERMISSION_RULE = "Bash(kyrio:*)"


class ProbeError(Exception):
    """Nothing usable was found, or a file cannot be written."""


class Interpreter:
    def __init__(self, executable, version, how):
        self.executable = executable
        self.version = version
        self.how = how

    @property
    def usable(self):
        return self.version >= MINIMUM_PYTHON

    @property
    def version_text(self):
        return ".".join(str(part) for part in self.version)


# ----------------------------------------------------------- interpreter


def running_interpreter():
    """The interpreter executing this process, which is the honest answer.

    Whatever the shims resolved is already running, so recording anything else
    would record a different interpreter from the one that works.
    """
    return Interpreter(sys.executable, sys.version_info[:3],
                       "running this process")


def probe_candidates(candidates=None):
    """Every interpreter on PATH that answers, with its version."""
    found = []
    for argv in (candidates or INTERPRETER_CANDIDATES):
        try:
            result = subprocess.run(
                [*argv, "-c", VERSION_PROGRAM],
                capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode != 0:
            continue
        lines = result.stdout.strip().splitlines()
        if len(lines) < 2:
            continue
        try:
            version = tuple(int(p) for p in lines[0].split("."))
        except ValueError:
            continue
        found.append(Interpreter(lines[1].strip(), version, " ".join(argv)))
    return found


def choose_interpreter():
    """The interpreter to record. Raises if none is new enough."""
    running = running_interpreter()
    if running.usable:
        return running
    for candidate in probe_candidates():
        if candidate.usable:
            return candidate
    raise ProbeError(
        "no Python %s or newer found; install one, or set KYRIO_PYTHON to the "
        "absolute path of one" % ".".join(str(p) for p in MINIMUM_PYTHON))


# ------------------------------------------------------------ execution


class Ran:
    """What happened when a probe was executed."""

    def __init__(self, outcome, argv, output="", detail=""):
        self.outcome = outcome
        self.argv = list(argv)
        self.output = output
        self.detail = detail

    @property
    def answered(self):
        return self.outcome == ANSWERED

    @property
    def command(self):
        return " ".join(self.argv)

    def __repr__(self):
        return "Ran(%s, %s)" % (self.outcome, self.command)


def run(argv, timeout=PROBE_TIMEOUT):
    """Execute a probe and classify what happened.

    An argument list, never a shell string, and never ``shell=True`` (I5).
    Nothing a caller supplies reaches this function: probes are declared, not
    composed from input.

    Every failure is a return value rather than an exception. A probe that
    cannot run is an ordinary answer about this machine -- it is most of what
    the report has to say -- and raising would make the caller handle four
    exception types to write four table rows.
    """
    try:
        result = subprocess.run(
            list(argv), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError:
        return Ran(MISSING, argv, detail="not found")
    except subprocess.TimeoutExpired:
        return Ran(TIMED_OUT, argv, detail="no answer within %ds" % timeout)
    except OSError as exc:
        return Ran(MISSING, argv, detail=str(exc))

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        return Ran(FAILED, argv, output, "exit %d" % result.returncode)
    return Ran(ANSWERED, argv, output)


# -------------------------------------------------------------- servers


#: A tool from a connected server is named
#: ``mcp__<server name, non-alphanumerics replaced>__<tool>``. Deriving it is
#: a string transformation, which makes it a fact rather than a judgment, so
#: it belongs here and not in a model's head (I9).
_NOT_ALPHANUMERIC = re.compile(r"[^0-9A-Za-z]+")


def tool_prefix(name):
    """The tool namespace a server's tools appear under."""
    return _NOT_ALPHANUMERIC.sub("_", name).strip("_")


class Server:
    """One connected-server entry, as discovery reported it."""

    def __init__(self, name, address, state, reported):
        self.name = name
        self.address = address
        self.state = state
        #: Derived, not reported. Setup would otherwise have to guess it, and
        #: a guessed prefix names a tool that does not exist.
        self.prefix = tool_prefix(name)
        #: The status text as printed. Kept so that a state this version does
        #: not recognize is still shown to a person verbatim rather than
        #: rounded to the nearest one it does.
        self.reported = reported

    @property
    def usable(self):
        return self.state == CONNECTED

    def __repr__(self):
        return "Server(%s, %s)" % (self.name, self.state)


class Discovery:
    """What discovery found, or why it found nothing.

    Nothing found and discovery not working are different answers, and a list
    that is empty for both reasons cannot say which.
    """

    def __init__(self, servers=(), problem=None):
        self.servers = list(servers)
        self.problem = problem

    @property
    def connected(self):
        return [s for s in self.servers if s.usable]


class Tool:
    """One shipped adapter, and what this machine can do with it."""

    def __init__(self, adapter, state, remedy=""):
        self.adapter = adapter
        self.state = state
        self.remedy = remedy

    @property
    def id(self):
        return self.adapter.ID

    @property
    def capabilities(self):
        return ", ".join(self.adapter.CAPABILITIES)

    @property
    def usable(self):
        """Signed in, and only that.

        Presence is not evidence. A binary can be installed as something
        else's dependency, bundled by policy, or left from a trial, and a
        capability mapped on presence alone sends every later call somewhere
        wrong while the report calls it fine. Somebody signing in is the
        cheapest available proof that this machine is meant to reach it.
        """
        return self.state == AUTHENTICATED

    def __repr__(self):
        return "Tool(%s, %s)" % (self.id, self.state)


def probe_tool(adapter, runner=None):
    """Run an adapter's two probes and say what this machine has."""
    runner = run if runner is None else runner

    healthy = adapter.health(runner)
    if healthy.outcome == MISSING:
        return Tool(adapter, NOT_INSTALLED, getattr(adapter, "INSTALL", ""))
    if not healthy.answered:
        return Tool(adapter, BROKEN, healthy.detail)

    # Only now, and separately. Something that runs still proves nothing about
    # whether anyone here is entitled to use it.
    signed_in = adapter.auth(runner)
    if signed_in.answered:
        return Tool(adapter, AUTHENTICATED)
    return Tool(adapter, UNAUTHENTICATED, getattr(adapter, "LOGIN", ""))


def probe_tools(registry=None, runner=None):
    """Every shipped adapter, in id order."""
    registry = providers if registry is None else registry
    return [probe_tool(registry.ADAPTERS[key], runner=runner)
            for key in sorted(registry.ADAPTERS)]


def server_state(text):
    """Classify a status phrase. Unrecognized is ``unknown``, never a guess.

    Rounding an unfamiliar status to the nearest familiar one is how a report
    becomes confidently wrong: this runs against another program's output,
    which is free to grow a state this version has never seen.
    """
    lowered = text.lower()
    for state, phrases in SERVER_STATES:
        if any(phrase in lowered for phrase in phrases):
            return state
    return UNKNOWN


def parse_servers(text):
    """Read the listing. Lines that do not fit the shape are skipped.

    Skipping rather than failing: the listing carries a header line and blank
    lines today and may carry more tomorrow, and a discovery that breaks on an
    added banner would be a fragile thing to hang setup on.
    """
    servers = []
    for line in text.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        name, _, rest = line.partition(":")
        # "<name>: <address> - <status>". Split the status from the right: an
        # address can contain a hyphen, and the status never does.
        address, separator, status = rest.rpartition(" - ")
        if not separator:
            continue
        name = name.strip()
        status = status.strip()
        if not name or not status:
            continue
        servers.append(Server(name, address.strip(),
                              server_state(status), status))
    return servers


def discover_servers(runner=None):
    """Ask the CLI what servers exist and what state each is in.

    ``runner`` is injectable so the parsing can be tested against hand-written
    listings (I8), and so a test never depends on what is connected on the
    machine running it.
    """
    runner = run if runner is None else runner
    ran = runner(SERVER_LIST)
    if ran.answered:
        return Discovery(parse_servers(ran.output))
    return Discovery(problem="%s: %s" % (" ".join(SERVER_LIST),
                                         ran.detail or ran.outcome))


# --------------------------------------------------------------- report


def report(cwd, machine_path=None, discovery=None, tools=None):
    """What this machine has. Writes nothing.

    ``discovery`` is passed in rather than performed here. Discovery runs
    another program and health-checks every server over the network, and a
    function that reports is a bad place to hide seconds of work; the command
    layer decides when to pay for it.
    """
    machine_path = pathlib.Path(machine_path or config.MACHINE_CONFIG)
    try:
        chosen = choose_interpreter()
        interpreter_line = "%-12s %-9s %s" % (
            "python", chosen.version_text, chosen.executable)
    except ProbeError as exc:
        chosen = None
        interpreter_line = "%-12s %s" % ("python", exc)

    # Resolved through the same module ``caps`` uses, so that two commands
    # cannot describe one machine two different ways.
    resolved = config.resolve(start=cwd, machine_path=machine_path)
    rows = capability.rows(resolved)

    recorded = _recorded_interpreter(machine_path)
    lines = [
        "INTERPRETER",
        "  " + interpreter_line,
        "",
        "CAPABILITY",
        table(("NAME", "TRANSPORT", "STATUS"), rows).rstrip("\n"),
        "",
        "SERVERS",
        _servers_block(discovery),
        "",
        "TOOLS",
        _tools_block(tools),
        "",
        "RECORDED",
        table(("FILE", "STATE"), [
            (str(machine_path),
             "present" if machine_path.is_file() else "not written yet"),
            (str(INTERPRETER_FILE), recorded or "not written yet"),
            (str(SETTINGS_FILE),
             "rule present" if has_permission() else "rule not present"),
        ]).rstrip("\n"),
        "",
        "INSTALLS   nothing, ever.",
        "",
        "NEXT",
        "  kyrio probe record       record the interpreter",
        "  kyrio probe permission   review the permission rule",
    ]
    return Result("probe", "\n".join(lines) + "\n",
                  interpreter=bool(chosen),
                  recorded=machine_path.is_file(),
                  permission=has_permission(),
                  connected=len(discovery.connected) if discovery else 0)


def _tools_block(tools):
    """Shipped adapters, and what stands between them and being usable."""
    if tools is None:
        return "  not probed"
    if not tools:
        return "  none ship yet"
    return table(("PROVIDER", "SERVES", "STATE", "NEXT"),
                 [(t.id, t.capabilities, t.state, t.remedy)
                  for t in tools]).rstrip("\n")


def _servers_block(discovery):
    """The servers section, including the case where nobody looked."""
    if discovery is None:
        return "  not probed"
    if discovery.problem:
        return "  not discovered: %s" % discovery.problem
    if not discovery.servers:
        return "  none configured"
    return table(("NAME", "STATE", "PREFIX", "ADDRESS"),
                 [(s.name, s.state, s.prefix, s.address)
                  for s in discovery.servers]).rstrip("\n")


def _recorded_interpreter(machine_path):
    try:
        return json.loads(
            machine_path.read_text(encoding="utf-8")).get("interpreter")
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------- record


def now():
    """The clock, in one place, so a test can hold it still."""
    return datetime.datetime.now(datetime.UTC).replace(
        microsecond=0).isoformat().replace("+00:00", "Z")


def cache_servers(discovery, servers_file=None, clock=None):
    """Write what discovery saw, with the moment it saw it."""
    servers_file = pathlib.Path(servers_file or SERVERS_FILE)
    clock = now if clock is None else clock
    _write_json(servers_file, {
        "at": clock(),
        "servers": [{"name": s.name, "state": s.state, "address": s.address}
                    for s in discovery.servers],
    })
    return servers_file


def last_probe(servers_file=None):
    """What discovery last saw, or ``None`` if it has never run here.

    Never an error: a machine that has not been probed is the ordinary state
    of a fresh clone, not a fault.
    """
    data = _read_json(pathlib.Path(servers_file or SERVERS_FILE))
    if not isinstance(data, dict) or "at" not in data:
        return None
    return data


def record(machine_path=None, interpreter_file=None, capabilities=None,
           discovery=None, servers_file=None, clock=None):
    """Write the machine layer and its shell-readable mirror.

    The interpreter is recorded twice on purpose: ``config.json`` holds it as
    an ordinary key, and ``state/interpreter`` holds the same absolute path as
    one line of plain text, because the launcher shims are sh and batch and
    cannot parse JSON without a tool that may not be installed. Both, or
    neither — a mirror that disagrees with the config is worse than no mirror.
    """
    machine_path = pathlib.Path(machine_path or config.MACHINE_CONFIG)
    interpreter_file = pathlib.Path(interpreter_file or INTERPRETER_FILE)
    chosen = choose_interpreter()

    existing = _read_json(machine_path, default={})
    merged = dict(existing)
    merged["schema"] = config.SCHEMA_VERSION
    merged["interpreter"] = chosen.executable

    # Additive and per key. Re-running after a server is connected upgrades
    # that capability and leaves every other one exactly as it was, including
    # entries a person wrote by hand.
    before = dict(merged.get("capabilities") or {})
    entries = dict(before)
    entries.setdefault("repo", {"transport": "local"})
    changes = []
    for name, entry in (capabilities or {}).items():
        changes.append((name, entry, "unchanged" if before.get(name) == entry
                        else "written"))
        entries[name] = entry
    merged["capabilities"] = entries

    _write_json(machine_path, merged)
    try:
        interpreter_file.parent.mkdir(parents=True, exist_ok=True)
        interpreter_file.write_text(chosen.executable + "\n", encoding="utf-8")
    except OSError as exc:
        raise ProbeError("cannot write %s: %s" % (interpreter_file, exc)) from exc

    cached = None
    if discovery is not None:
        cached = cache_servers(discovery, servers_file=servers_file,
                               clock=clock)

    kept = sorted(k for k in existing if k not in ("schema", "interpreter",
                                                   "capabilities"))
    written = [name for name, _, state in changes if state == "written"]
    lines = [
        "WROTE",
        "  %-42s %s" % (machine_path, "interpreter, capabilities"),
        "  %-42s %s" % (interpreter_file, chosen.executable),
    ]
    if cached is not None:
        lines.append("  %-42s %s" % (cached, "%d server(s) last seen"
                                     % len(discovery.servers)))
    lines += [
        "",
        "INTERPRETER  python %s" % chosen.version_text,
        "  %s" % chosen.executable,
    ]
    if changes:
        lines += ["", "CAPABILITIES",
                  table(("NAME", "TRANSPORT", "STATE"),
                        [(name, _spec_text(entry), state)
                         for name, entry, state in changes]).rstrip("\n")]
    untouched = sorted(k for k in before if k not in dict(
        (name, entry) for name, entry, _ in changes))
    if untouched:
        lines += ["", "CAPABILITIES LEFT ALONE", "  " + ", ".join(untouched)]
    if kept:
        lines += ["", "KEPT UNCHANGED", "  " + ", ".join(kept)]
    lines += ["", "Re-run this whenever the machine changes. It is idempotent."]
    return Result("probe", "\n".join(lines) + "\n",
                  interpreter=chosen.executable, kept=len(kept),
                  written=len(written))


def _spec_text(entry):
    """An entry as it would be written on the command line."""
    transport = entry.get("transport")
    provider = entry.get("provider")
    if entry.get("tool_prefix"):
        return "%s:%s" % (transport, entry["tool_prefix"])
    if provider:
        if isinstance(provider, str):
            return "%s:%s" % (transport, provider)
        return "%s:%s" % (transport, ",".join(provider))
    return transport


# ----------------------------------------------------------- permission


def has_permission(settings_path=None):
    settings = _read_json(pathlib.Path(settings_path or SETTINGS_FILE),
                          default={})
    allow = ((settings.get("permissions") or {}).get("allow") or [])
    return PERMISSION_RULE in allow


def permission(apply=False, settings_path=None):
    """Show the one permission rule; add it only when asked.

    Without it every broker call raises a prompt, because ``allowed-tools`` in
    a skill's frontmatter covers only the turn that invoked the skill and these
    workflows are all multi-turn.
    """
    settings_path = pathlib.Path(settings_path or SETTINGS_FILE)
    if settings_path.exists() and _read_json(settings_path) is None:
        raise ProbeError(
            "%s is not valid JSON; fix it before adding the rule"
            % settings_path)

    if has_permission(settings_path):
        return Result(
            "permission",
            "Already allowed in %s:\n\n  %s\n" % (settings_path,
                                                  PERMISSION_RULE),
            present=True, applied=False)

    proposed = '{ "permissions": { "allow": ["%s"] } }' % PERMISSION_RULE
    if not apply:
        payload = (
            "Not present in %s.\n\n"
            "Proposed addition:\n\n  %s\n\n"
            "One rule covers every broker call, now and later, because the "
            "rule names the bare command.\n"
            "Nothing else is granted: the broker runs no command supplied by "
            "its caller.\n\n"
            "To add it:  kyrio probe permission --apply\n"
            % (settings_path, proposed))
        return Result("permission", payload, present=False, applied=False)

    settings = _read_json(settings_path, default={})
    permissions = dict(settings.get("permissions") or {})
    allow = list(permissions.get("allow") or [])
    allow.append(PERMISSION_RULE)
    permissions["allow"] = allow
    settings["permissions"] = permissions
    _write_json(settings_path, settings)
    return Result("permission",
                  "Added to %s:\n\n  %s\n" % (settings_path, PERMISSION_RULE),
                  present=True, applied=True)


# ------------------------------------------------------------------ io


def _read_json(path, default=None):
    """Parsed contents, ``default`` when absent, ``None`` when unparseable."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        return None


def _write_json(path, data):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise ProbeError("cannot write %s: %s" % (path, exc)) from exc
