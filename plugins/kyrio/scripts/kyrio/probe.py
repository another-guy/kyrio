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

import json
import pathlib
import subprocess
import sys

from kyrio import config
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

#: Where the machine layer and its shell-readable mirror live.
STATE_DIR = pathlib.Path.home() / ".claude" / "kyrio" / "state"
INTERPRETER_FILE = STATE_DIR / "interpreter"
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


# --------------------------------------------------------------- report


def report(cwd, machine_path=None):
    """What this machine has. Writes nothing."""
    machine_path = pathlib.Path(machine_path or config.MACHINE_CONFIG)
    try:
        chosen = choose_interpreter()
        interpreter_line = "%-12s %-9s %s" % (
            "python", chosen.version_text, chosen.executable)
    except ProbeError as exc:
        chosen = None
        interpreter_line = "%-12s %s" % ("python", exc)

    resolved = config.resolve(start=cwd, machine_path=machine_path)
    rows = []
    for name in config.CAPABILITIES:
        entry = (resolved.get("capabilities") or {}).get(name)
        transport = entry.get("transport") if isinstance(entry, dict) else entry
        if name in config.INTRINSIC:
            rows.append((name, "local", "ready"))
        else:
            rows.append((name, transport or "--",
                         "configured" if transport else "not configured yet"))

    recorded = _recorded_interpreter(machine_path)
    lines = [
        "INTERPRETER",
        "  " + interpreter_line,
        "",
        "CAPABILITY",
        table(("NAME", "TRANSPORT", "STATUS"), rows).rstrip("\n"),
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
                  permission=has_permission())


def _recorded_interpreter(machine_path):
    try:
        return json.loads(
            machine_path.read_text(encoding="utf-8")).get("interpreter")
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------- record


def record(machine_path=None, interpreter_file=None):
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
    capabilities = dict(merged.get("capabilities") or {})
    capabilities.setdefault("repo", {"transport": "local"})
    merged["capabilities"] = capabilities

    _write_json(machine_path, merged)
    try:
        interpreter_file.parent.mkdir(parents=True, exist_ok=True)
        interpreter_file.write_text(chosen.executable + "\n", encoding="utf-8")
    except OSError as exc:
        raise ProbeError("cannot write %s: %s" % (interpreter_file, exc)) from exc

    kept = sorted(k for k in existing if k not in ("schema", "interpreter",
                                                   "capabilities"))
    lines = [
        "WROTE",
        "  %-42s %s" % (machine_path, "interpreter, capabilities"),
        "  %-42s %s" % (interpreter_file, chosen.executable),
        "",
        "INTERPRETER  python %s" % chosen.version_text,
        "  %s" % chosen.executable,
    ]
    if kept:
        lines += ["", "KEPT UNCHANGED", "  " + ", ".join(kept)]
    lines += ["", "Re-run this whenever the machine changes. It is idempotent."]
    return Result("probe", "\n".join(lines) + "\n",
                  interpreter=chosen.executable, kept=len(kept))


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
