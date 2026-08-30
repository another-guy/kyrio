"""The ``repo`` capability: the working tree and its history.

Local, intrinsic, and always available. No transport, no authentication, no
network, and nothing to configure — which is why the two skills built on it
work on any machine the moment the plugin installs.

Everything here is deterministic (I9). Window arithmetic resolves to an
explicit date before git sees it, so the same command on the same history
returns the same answer and the answer says which date it used. Nothing is
described in prose for a model to work out.

This module returns results; it never prints. ``__main__`` emits them (S1).
"""

import collections
import datetime
import json
import pathlib
import re
import subprocess

from kyrio.cli import table as _table

#: Extension to language, for reporting what a directory mostly holds. Naming
#: languages and test tools is permitted; naming an access provider is not (I1).
EXTENSION_LANGUAGE = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cs": "c#", ".fs": "f#", ".vb": "visual basic",
    ".java": "java", ".kt": "kotlin", ".scala": "scala",
    ".go": "go", ".rs": "rust", ".rb": "ruby", ".php": "php",
    ".c": "c", ".h": "c", ".cc": "c++", ".cpp": "c++", ".hpp": "c++",
    ".swift": "swift", ".m": "objective-c",
    ".sql": "sql", ".sh": "shell", ".ps1": "powershell",
    ".html": "html", ".css": "css", ".scss": "css",
    ".md": "docs", ".rst": "docs", ".txt": "docs",
    ".json": "config", ".yml": "config", ".yaml": "config",
    ".toml": "config", ".ini": "config", ".xml": "config",
}

#: Manifest to (build, test) command. Ordered: the first match wins, so a
#: repository carrying several gets the one nearest its primary toolchain.
MANIFEST_COMMANDS = [
    ("package.json", None, None),          # scripts are read from the file
    ("pyproject.toml", None, "pytest"),
    ("setup.py", None, "pytest"),
    ("Cargo.toml", "cargo build", "cargo test"),
    ("go.mod", "go build ./...", "go test ./..."),
    ("pom.xml", "mvn -q package", "mvn test"),
    ("build.gradle", "gradle build", "gradle test"),
    ("build.gradle.kts", "gradle build", "gradle test"),
    ("Makefile", None, None),              # targets are read from the file
]

#: Project files whose presence implies a toolchain without naming a file.
GLOB_COMMANDS = [
    ("*.sln", "dotnet build", "dotnet test"),
    ("*.csproj", "dotnet build", "dotnet test"),
    ("*.fsproj", "dotnet build", "dotnet test"),
]

#: Filenames that conventionally start a program.
ENTRY_NAMES = {"__main__.py", "main.py", "app.py", "manage.py", "wsgi.py",
               "asgi.py", "index.js", "index.ts", "main.js", "main.ts",
               "server.js", "server.ts", "Program.cs", "Startup.cs",
               "main.go", "main.rs", "Main.java", "Application.java"}

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
             "build", "target", "bin", "obj", ".idea", ".vs", ".pytest_cache"}

WINDOW_RE = re.compile(r"^(\d+)\s*(d|w|mo|m|y)$", re.IGNORECASE)
LOCATION_RE = re.compile(r"^(?P<path>.+?):(?P<start>\d+)(?:-(?P<end>\d+))?$")

#: Days per unit. A month is 30 days and a year 365 by definition here: an
#: approximation that is stated is more useful than a calendar-exact one that
#: silently shifts the window between runs.
WINDOW_DAYS = {"d": 1, "w": 7, "mo": 30, "m": 30, "y": 365}

DEFAULT_WINDOW = "90d"


class RepoError(Exception):
    """The working tree cannot answer. Raised, never printed."""


class Result:
    """What a verb produced: a payload plus header fields."""

    def __init__(self, kind, payload, **meta):
        self.kind = kind
        self.payload = payload
        self.meta = meta


# ------------------------------------------------------------------ git


def git(args, cwd, check=True):
    """Run git with an argument list. Never a shell string (I5)."""
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, cwd=str(cwd),
            encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise RepoError("git is not on PATH") from exc
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise RepoError("git %s failed: %s"
                        % (args[0], detail[0] if detail else "no detail"))
    return result.stdout


def root(cwd):
    """The top of the working tree containing ``cwd``."""
    try:
        out = git(["rev-parse", "--show-toplevel"], cwd)
    except RepoError as exc:
        raise RepoError(
            "not inside a git working tree (%s)" % exc) from exc
    return pathlib.Path(out.strip()).resolve()


def since_date(window, today=None):
    """Resolve a window to an explicit ISO date (I9).

    Returns (iso_date, label). A window already shaped like a date passes
    through, so a caller may pin an exact boundary.
    """
    today = today or datetime.date.today()
    window = (window or DEFAULT_WINDOW).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", window):
        return window, window
    match = WINDOW_RE.match(window)
    if not match:
        raise RepoError(
            "window %r is not understood; use 30d, 6w, 3mo, 1y, or a date"
            % window)
    days = int(match.group(1)) * WINDOW_DAYS[match.group(2).lower()]
    return (today - datetime.timedelta(days=days)).isoformat(), window


# ------------------------------------------------------------------ map


def repo_map(cwd, conventions=None):
    """Entry points, module boundaries, and how to build and test."""
    top = root(cwd)
    conventions = conventions or {}
    tracked = _tracked_files(top)
    if not tracked:
        raise RepoError("no tracked files; is this a fresh repository?")

    directories = _directory_summary(tracked)
    build, build_source = _command(top, tracked, "build", conventions)
    test, test_source = _command(top, tracked, "test", conventions)
    entries = _entry_points(tracked)

    lines = ["ROOT     %s" % top,
             "BRANCH   %s" % _branch(top),
             "TRACKED  %d files" % len(tracked),
             ""]
    lines.append("BUILD    %-28s %s" % (build or "not detected", build_source))
    lines.append("TEST     %-28s %s" % (test or "not detected", test_source))
    lines.append("")
    lines.append("TOP LEVEL")
    lines.append(_table(("DIRECTORY", "FILES", "MOSTLY"), directories, right={1}))
    if entries:
        lines.append("ENTRY POINTS")
        lines.extend("  %s" % e for e in entries)

    return Result("map", "\n".join(lines) + "\n",
                  files=len(tracked), directories=len(directories),
                  build=bool(build), test=bool(test), entrypoints=len(entries))


def _tracked_files(top):
    out = git(["ls-files", "-z"], top)
    return [p for p in out.split("\0") if p]


def _branch(top):
    name = git(["rev-parse", "--abbrev-ref", "HEAD"], top).strip()
    return name or "(detached)"


def _directory_summary(tracked):
    counts = collections.Counter()
    languages = collections.defaultdict(collections.Counter)
    for path in tracked:
        head = path.split("/")[0] if "/" in path else "(root)"
        counts[head] += 1
        language = EXTENSION_LANGUAGE.get(pathlib.PurePosixPath(path).suffix)
        if language:
            languages[head][language] += 1
    rows = []
    for name, count in counts.most_common():
        common = languages[name].most_common(1)
        rows.append((name, str(count), common[0][0] if common else "--"))
    return rows


def _command(top, tracked, which, conventions):
    """Config beats detection, and the report says which one answered."""
    configured = conventions.get(which)
    if configured:
        return configured, "from configuration"

    names = {p for p in tracked if "/" not in p}
    for manifest, build, test in MANIFEST_COMMANDS:
        if manifest not in names:
            continue
        if manifest == "package.json":
            found = _package_script(top / manifest, which)
            if found:
                return found, "detected in %s" % manifest
            continue
        if manifest == "Makefile":
            if _makefile_target(top / manifest, which):
                return "make %s" % which, "detected in %s" % manifest
            continue
        command = build if which == "build" else test
        if command:
            return command, "detected from %s" % manifest

    for pattern, build, test in GLOB_COMMANDS:
        suffix = pattern.lstrip("*")
        if any(p.endswith(suffix) for p in tracked):
            return (build if which == "build" else test,
                    "detected from a %s project file" % suffix)
    return None, "--"


def _package_script(path, which):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    scripts = data.get("scripts")
    if not isinstance(scripts, dict) or which not in scripts:
        return None
    return "npm test" if which == "test" else "npm run %s" % which


def _makefile_target(path, which):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return re.search(r"^%s\s*:" % re.escape(which), text, re.MULTILINE) is not None


def _entry_points(tracked):
    found = [p for p in tracked
             if pathlib.PurePosixPath(p).name in ENTRY_NAMES
             and not _in_skipped(p)]
    return sorted(found)[:20]


def _in_skipped(path):
    return any(part in SKIP_DIRS for part in path.split("/")[:-1])


# ---------------------------------------------------------------- churn


def churn(cwd, window=DEFAULT_WINDOW, top_n=25, path=None, today=None):
    """How often each file changed, most-changed first."""
    top = root(cwd)
    since, label = since_date(window, today=today)

    args = ["log", "--since", since, "--name-only", "--pretty=format:%H",
            "--no-merges"]
    if path:
        args += ["--", path]
    out = git(args, top)

    commits = 0
    counts = collections.Counter()
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.fullmatch(r"[0-9a-f]{7,40}", line):
            commits += 1
        else:
            counts[line] += 1

    rows = [(str(n), name) for name, n in counts.most_common(top_n)]
    header = "Since %s (%s)%s: %d commits touching %d files\n" % (
        since, label, " under %s" % path if path else "", commits, len(counts))
    payload = header + "\n" + (_table(("CHANGES", "FILE"), rows, right={0})
                               if rows else "  no changes in this window\n")
    return Result("churn", payload, since=since, window=label,
                  commits=commits, files=len(counts), shown=len(rows))


# --------------------------------------------------------------- owners


def owners(cwd, path=None):
    """Ownership from an ownership file, falling back to history."""
    top = root(cwd)
    source, rules = _ownership_rules(top)

    if path and rules:
        matched = _match_rules(rules, path)
        if matched:
            pattern, names = matched
            payload = ("%s\n\n  PATH     %s\n  PATTERN  %s\n  OWNERS   %s\n"
                       % (source, path, pattern, ", ".join(names)))
            return Result("owners", payload, source=source, matched=True,
                          owners=len(names))

    if path:
        rows = _top_committers(top, path)
        note = ("%s has no rule for this path; " % source if source
                else "no ownership file found; ")
        payload = (note + "most frequent committers instead\n\n"
                   + (_table(("COMMITS", "AUTHOR"), rows, right={0}) if rows
                      else "  no history for this path\n"))
        return Result("owners", payload, source=source or "history",
                      matched=False, authors=len(rows))

    if not rules:
        raise RepoError(
            "no ownership file found; pass a path to see its committers")
    rows = [(pattern, ", ".join(names)) for pattern, names in rules]
    payload = "%s\n\n%s" % (source, _table(("PATTERN", "OWNERS"), rows))
    return Result("owners", payload, source=source, rules=len(rules))


#: Conventional locations for an ownership file, nearest convention first.
OWNERSHIP_FILES = ("CODEOWNERS", ".github/CODEOWNERS", "docs/CODEOWNERS",
                   ".gitlab/CODEOWNERS")


def _ownership_rules(top):
    for relative in OWNERSHIP_FILES:
        candidate = top / relative
        if not candidate.is_file():
            continue
        rules = []
        for line in candidate.read_text(encoding="utf-8",
                                        errors="replace").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 2:
                rules.append((parts[0], parts[1:]))
        return relative, rules
    return None, []


def _match_rules(rules, path):
    """Last matching rule wins, which is the conventional precedence."""
    winner = None
    for pattern, names in rules:
        if _pattern_matches(pattern, path):
            winner = (pattern, names)
    return winner


def _pattern_matches(pattern, path):
    normalized = pattern.lstrip("/")
    if pattern.endswith("/"):
        return path.startswith(normalized)
    if "*" in normalized:
        regex = re.escape(normalized).replace(r"\*", "[^/]*")
        return re.fullmatch(regex, path) is not None
    return path == normalized or path.startswith(normalized.rstrip("/") + "/")


def _top_committers(top, path, limit=10):
    out = git(["shortlog", "-sne", "HEAD", "--", path], top, check=False)
    rows = []
    for line in out.splitlines():
        parts = line.strip().split("\t", 1)
        if len(parts) == 2:
            rows.append((parts[0].strip(), parts[1].strip()))
    return rows[:limit]


# ---------------------------------------------------------------- blame


def blame(cwd, location):
    """Who last changed a line, and the commit that explains it."""
    top = root(cwd)
    match = LOCATION_RE.match(location or "")
    if not match:
        raise RepoError(
            "expected <path>:<line> or <path>:<start>-<end>, got %r" % location)
    path = match.group("path")
    start = int(match.group("start"))
    end = int(match.group("end") or start)
    if end < start:
        raise RepoError("line range ends before it starts: %s" % location)

    out = git(["blame", "-L", "%d,%d" % (start, end), "--porcelain",
               "--", path], top)
    entries = _parse_blame(out)
    if not entries:
        raise RepoError("no blame output for %s" % location)

    # Group by commit rather than by line. A range usually shares one commit,
    # and repeating its message once per line buys nothing and costs context.
    sections = []
    for commit, group in _group_by_commit(entries):
        first = group[0]
        body = git(["show", "-s", "--format=%B", commit], top,
                   check=False).strip()
        lines = "\n".join("  %s:%-6s %s" % (path, e["line"], e["text"].rstrip())
                          for e in group)
        sections.append(
            "  COMMIT   %s\n"
            "  AUTHOR   %s\n"
            "  DATE     %s\n"
            "  SUMMARY  %s\n"
            "\n%s\n"
            "%s"
            % (commit[:12], first["author"], first["date"], first["summary"],
               lines, "\n" + _indent(body, 2) if body else ""))
    return Result("blame", "\n\n".join(sections) + "\n",
                  path=path, lines=len(entries), commits=len(sections))


def _group_by_commit(entries):
    """Consecutive runs of the same commit, in the order the lines appear."""
    groups = []
    for entry in entries:
        if groups and groups[-1][0] == entry["commit"]:
            groups[-1][1].append(entry)
        else:
            groups.append((entry["commit"], [entry]))
    return groups


def _parse_blame(out):
    """Parse porcelain blame, carrying metadata across repeated commits.

    Porcelain emits the author, time, and summary headers only the *first*
    time a commit appears. Every later line from that same commit is the hash
    and the content alone, so the details have to be remembered per commit —
    otherwise a range describes its first line and leaves the rest blank.
    """
    entries = []
    known = {}
    current = {}
    for line in out.splitlines():
        if line.startswith("\t"):
            current["text"] = line[1:]
            remembered = known.setdefault(current.get("commit"), {})
            for field in ("author", "date", "summary"):
                if field in current:
                    remembered[field] = current[field]
                else:
                    current[field] = remembered.get(field, "")
            entries.append(current)
            current = {}
            continue
        parts = line.split(" ", 1)
        head = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        if re.fullmatch(r"[0-9a-f]{40}", head):
            current["commit"] = head
            current["line"] = rest.split(" ")[1] if " " in rest else rest
        elif head == "author":
            current["author"] = rest
        elif head == "author-time":
            current["date"] = datetime.datetime.fromtimestamp(
                int(rest), datetime.timezone.utc).date().isoformat()
        elif head == "summary":
            current["summary"] = rest
    return [e for e in entries if "commit" in e]


def _indent(text, spaces):
    pad = " " * spaces
    return "\n".join(pad + line if line else "" for line in text.splitlines())

