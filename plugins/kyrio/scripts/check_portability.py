"""Portability lint. Blocking, in the pre-commit hook and in CI.

A pack that installs on any machine must not encode a particular one. Every
rule below is a mechanical form of that single requirement, and each maps to a
numbered invariant in ``docs/DESIGN.md`` section 2.

    RULE 1  repo-wide      environment-coupling vocabulary, private hostnames,
                           machine-specific absolute paths, real-looking
                           identifiers                            (I2, I4, I8)
    RULE 2  skills and     no command but the broker and ordinary developer
            references     tooling; no URLs                       (I1)
    RULE 3  skills and     no dependency on a skill this plugin does not
            references     ship and the harness does not bundle   (I3)

The rules are written as allowlists wherever a blocklist would have to
enumerate the very things the check exists to keep out. An allowlist of
permitted commands says nothing about what is absent; a list of forbidden
product names would be a map of exactly what the author was avoiding.

Run it directly for the whole tree, or with ``--staged`` for what is about to
be committed::

    python scripts/check_portability.py
    python scripts/check_portability.py --staged

This is a development tool, not part of the broker: it prints an ordinary
report and is not bound by the emit chokepoint.
"""

import argparse
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent.parent.parent

#: Paths under here are checked; everything else in the repo is ignored.
SCAN_SUFFIXES = {".py", ".md", ".json", ".txt", ".sh", ".cmd", ".bat",
                 ".yml", ".yaml", ".toml", ".cfg", ".ini", ""}

SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}

#: RULE 2 and RULE 3 apply here. Prose in these files reaches the model.
PROSE_ROOTS = ("plugins/kyrio/skills", "plugins/kyrio/references")

#: The vocabulary rule cannot apply to the two files that have to spell the
#: vocabulary out: the rule itself, and the test that proves the rule fires.
#: The exemption is narrow -- RULE 1 only, two named paths -- and the test
#: asserts the list stays that size, so it cannot quietly become a back door.
VOCABULARY_EXEMPT = {
    "plugins/kyrio/scripts/check_portability.py",
    "plugins/kyrio/tests/test_portability.py",
}

# ---------------------------------------------------------------- rule 1

#: Words implying the tool models more than one environment. It does not: one
#: machine, one environment (I4). A key, filename, or instruction naming a
#: grouping above the machine is a design error before it is anything else.
COUPLING_WORDS = (
    "org", "orgs", "organisation", "organisations", "organization",
    "organizations", "organizational", "tenant", "tenants", "multitenant",
    "employer", "employers", "company", "companies", "workplace",
    "client", "clients", "customer", "customers",
)
COUPLING_RE = re.compile(
    r"\b(%s)\b" % "|".join(COUPLING_WORDS), re.IGNORECASE)

#: Hostnames only ever resolvable inside one network. The suffix is captured
#: so that ``private_host`` can insist it was written in lower case.
PRIVATE_HOST_RE = re.compile(
    r"\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*"
    r"\.(internal|intranet|intra|corp|local|lan|priv)\b", re.IGNORECASE)

def private_host(line):
    """A private hostname on this line, or ``None``.

    The suffix has to be written in lower case. Hostnames are, and an
    upper-case final label is a module constant -- ``capability.LOCAL`` reads
    as a ``.local`` address to a regular expression and to nothing else. The
    cost of the looser rule is not the false positive itself; it is that a
    lint which cries wolf gets read past, and then it has stopped working.
    """
    for match in PRIVATE_HOST_RE.finditer(line):
        if match.group(1).islower():
            return match
    return None


#: Home directories are per-machine. ``~`` and ``%USERPROFILE%`` are not.
#: The separator repeats because the likeliest place for a leaked Windows path
#: is inside a source literal, where every backslash is doubled.
USER_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2}|/home/|/Users/)[A-Za-z0-9._-]+",
    re.IGNORECASE)

#: Identifiers shaped like real work items. Placeholders are the point: a
#: fixture carrying a real key is a captured payload wearing a disguise (I8).
IDENTIFIER_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-\d{1,6}\b")
PLACEHOLDER_PREFIXES = {"PROJ", "TEST", "EXAMPLE", "SAMPLE", "ABC", "XYZ",
                        "UTF", "ISO", "RFC", "SHA", "CVE"}

# ---------------------------------------------------------------- rule 2

#: The broker plus ordinary developer tooling. Anything else in a skill is a
#: provider reached directly, which is what the broker exists to prevent.
PERMITTED_COMMANDS = {
    "kyrio",
    "git",
    "python", "python3", "py", "pip", "pytest",
    "node", "npm", "npx", "yarn", "pnpm",
    "dotnet", "go", "cargo", "mvn", "gradle", "make",
    "cd", "ls", "cat", "head", "tail", "echo", "diff", "sort", "wc",
    "grep", "rg", "find", "sed", "awk", "test",
}
#: Fences that hold commands. ``text`` is deliberately absent: by convention it
#: holds output or an example, and reading those as commands makes the rule
#: fire on ordinary prose. A lint that cries wolf gets read past, and then it
#: has stopped working. An unlabelled fence stays in, because an unlabelled
#: fence in a skill usually is a command.
COMMAND_FENCE_LANGS = {"", "sh", "bash", "shell", "console", "sh-session",
                       "zsh", "powershell", "pwsh"}
COMMAND_TOKEN_RE = re.compile(r"^[a-z][a-z0-9._-]*$")
FENCE_RE = re.compile(r"^```([A-Za-z0-9_-]*)\s*$")
URL_RE = re.compile(r"https?://[^\s`)\"'>]+", re.IGNORECASE)

# ---------------------------------------------------------------- rule 3

#: Slash commands the harness itself provides. Anything else a skill invokes
#: must be shipped by this plugin, or it will be missing on the next machine.
BUNDLED_COMMANDS = {"init", "review", "code-review", "security-review",
                    "simplify", "run", "compact", "clear", "help"}
SLASH_RE = re.compile(r"(?<![\w`/])/([a-z][a-z0-9-]*)(?::([a-z][a-z0-9-]*))?")


class Finding:
    def __init__(self, path, line, rule, message, excerpt=""):
        self.path = path
        self.line = line
        self.rule = rule
        self.message = message
        self.excerpt = excerpt

    def __str__(self):
        where = "%s:%d" % (self.path, self.line)
        text = "  %-40s %s: %s" % (where, self.rule, self.message)
        if self.excerpt:
            text += "\n%s%s" % (" " * 4, self.excerpt.strip()[:100])
        return text


def shipped_skills(repo=REPO):
    skills = repo / "plugins" / "kyrio" / "skills"
    if not skills.is_dir():
        return set()
    return {d.name for d in skills.iterdir() if (d / "SKILL.md").is_file()}


def is_prose(relative):
    return any(relative.startswith(root) for root in PROSE_ROOTS)


def check_text(relative, text, skills=None):
    """Every rule, against one file's contents. Returns a list of findings."""
    findings = []
    lines = text.splitlines()
    skills = shipped_skills() if skills is None else skills

    if relative not in VOCABULARY_EXEMPT:
        findings.extend(_rule_one(relative, lines))
    if is_prose(relative):
        findings.extend(_rule_two(relative, lines))
        findings.extend(_rule_three(relative, lines, skills))
    return findings


def _rule_one(relative, lines):
    findings = []
    for number, line in enumerate(lines, 1):
        match = COUPLING_RE.search(line)
        if match:
            findings.append(Finding(
                relative, number, "RULE 1",
                "environment-coupling word %r; this tool models one machine"
                % match.group(1), line))
        match = private_host(line)
        if match:
            findings.append(Finding(
                relative, number, "RULE 1",
                "hostname resolvable in only one network: %s" % match.group(0),
                line))
        match = USER_PATH_RE.search(line)
        if match:
            findings.append(Finding(
                relative, number, "RULE 1",
                "machine-specific path %s; use ~ or a config value"
                % match.group(0), line))
        for match in IDENTIFIER_RE.finditer(line):
            if match.group(1).upper() not in PLACEHOLDER_PREFIXES:
                findings.append(Finding(
                    relative, number, "RULE 1",
                    "identifier %s looks real; use a placeholder such as "
                    "PROJ-1234" % match.group(0), line))
    return findings


def _rule_two(relative, lines):
    findings = []
    fence_lang = None
    for number, line in enumerate(lines, 1):
        fence = FENCE_RE.match(line)
        if fence:
            fence_lang = None if fence_lang is not None else fence.group(1).lower()
            continue

        for match in URL_RE.finditer(line):
            findings.append(Finding(
                relative, number, "RULE 2",
                "a URL ties this text to one environment: %s" % match.group(0),
                line))

        if fence_lang is None or fence_lang not in COMMAND_FENCE_LANGS:
            continue
        command = _leading_command(line)
        if command and command not in PERMITTED_COMMANDS:
            findings.append(Finding(
                relative, number, "RULE 2",
                "skills invoke %r only through the broker; %r is a provider "
                "reached directly" % ("kyrio", command), line))
    return findings


def _leading_command(line):
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("$ ") or stripped.startswith("> "):
        stripped = stripped[2:].strip()
    token = stripped.split()[0] if stripped.split() else ""
    if not COMMAND_TOKEN_RE.match(token):
        return None
    return token


def _rule_three(relative, lines, skills):
    findings = []
    for number, line in enumerate(lines, 1):
        for match in SLASH_RE.finditer(line):
            namespace, verb = match.group(1), match.group(2)
            if namespace == "kyrio":
                if verb and verb not in skills:
                    findings.append(Finding(
                        relative, number, "RULE 3",
                        "/kyrio:%s is not shipped by this plugin" % verb, line))
            elif namespace not in BUNDLED_COMMANDS:
                findings.append(Finding(
                    relative, number, "RULE 3",
                    "/%s is neither shipped here nor bundled with the harness; "
                    "it will be missing elsewhere" % namespace, line))
    return findings


def scannable(path, repo=REPO):
    if path.suffix.lower() not in SCAN_SUFFIXES:
        return False
    return not any(part in SKIP_DIRS for part in path.relative_to(repo).parts)


def walk(repo=REPO):
    for path in sorted(repo.rglob("*")):
        if path.is_file() and scannable(path, repo):
            yield path


def staged(repo=REPO):
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        capture_output=True, text=True, cwd=str(repo))
    for name in result.stdout.splitlines():
        path = repo / name
        if path.is_file() and scannable(path, repo):
            yield path


def check_paths(paths, repo=REPO):
    skills = shipped_skills(repo)
    findings = []
    for path in paths:
        relative = path.relative_to(repo).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(check_text(relative, text, skills=skills))
    return findings


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fail if anything in the repository assumes one machine.")
    parser.add_argument("--staged", action="store_true",
                        help="check what is staged rather than the whole tree")
    args = parser.parse_args(argv)

    # A finding quotes the line it found, and the offending line is exactly the
    # kind to hold a character the console codepage cannot encode. Without
    # this, the report dies in a traceback and the reason for the block is lost.
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(encoding="utf-8", errors="replace")

    paths = list(staged() if args.staged else walk())
    findings = check_paths(paths)

    if not findings:
        print("portability: %d files checked, clean" % len(paths))
        return 0

    print("portability: %d finding(s) in %d files checked\n"
          % (len(findings), len(paths)))
    for finding in findings:
        print(finding)
    print("\nNothing in this repository may assume a particular machine.\n"
          "See docs/DESIGN.md section 2 for the invariants these rules serve.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
