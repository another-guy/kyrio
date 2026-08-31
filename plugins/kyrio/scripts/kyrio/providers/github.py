"""Adapter for GitHub, through its official command-line tool.

Nothing above this file knows this provider exists. It is reached only by the
id below appearing as a *value* in a machine's configuration, which is what
lets two machines run identical plugin code and talk to different systems (I1).

Two probes, deliberately separate. Installed and signed in are different
states with different fixes: one is an install a person may not be permitted
to perform, the other is a sign-in nobody but the user can do. A single
boolean would collapse them and send people to the wrong remedy.

Authentication is decided by the **exit code**, never by reading the output.
The tool exits non-zero when no host is signed in, and that is a contract it
keeps across versions, where the wording beside it is free to change. Parsing
the words would make this adapter break on a release note.

Nothing here installs anything and nothing here starts a sign-in. Both remedy
strings below are printed for a person to run themselves.
"""

import json
import re

#: The value a machine's config carries in ``capabilities.<name>.provider``.
ID = "github"

CAPABILITIES = ("scm",)
TRANSPORT = "cli"
BINARY = "gh"

#: Does the binary run at all. By execution, never by presence: a name that
#: resolves on PATH proves only that something is there.
HEALTH = [BINARY, "--version"]

#: Is a host signed in. Deliberately not scoped to one host -- an enterprise
#: installation answers here under its own hostname, and which host a machine
#: talks to is a fact about that machine, never about this pack (I2).
AUTH = [BINARY, "auth", "status"]

#: Printed, never run.
LOGIN = "gh auth login"
INSTALL = "install the GitHub CLI: https://cli.github.com"


#: How a change is named here: a pull request number, and nothing else.
#: Validated by the adapter rather than centrally, because the same concept is
#: a forty-character hash elsewhere and one rule cannot fit both.
PR_ID_RE = re.compile(r"\A[1-9][0-9]{0,9}\Z")


def health(run):
    return run(HEALTH)


def auth(run):
    return run(AUTH)


def pr_identifier(text):
    """Normalize and check one pull request identifier.

    A leading ``#`` is accepted because that is how people write it and how it
    is pasted out of a browser; the tool itself does not take one.
    """
    pinned = (text or "").strip().lstrip("#").strip()
    if not PR_ID_RE.match(pinned):
        raise ValueError(
            "a pull request is named by its number, got %r" % (text or ""))
    return pinned


#: What the listing has to carry. Asked for by name rather than taking the
#: default shape, so a field added upstream cannot quietly widen the payload.
LOG_FIELDS = "number,title,mergedAt,author"

#: A ceiling, not a page size. Someone asking what shipped last week wants a
#: readable list; a year of history is a different question.
LOG_LIMIT = 100


def log(run, since, limit=LOG_LIMIT, cwd=None):
    """Changes merged since a date, most recent first."""
    return run([BINARY, "pr", "list",
                "--state", "merged",
                "--search", "merged:>=%s" % since,
                "--limit", str(limit),
                "--json", LOG_FIELDS], cwd=cwd)


def parse_log(text):
    """This tool's JSON into the broker's records.

    Parsed in the adapter because the *shape* is this tool's. What comes out
    is the shape every adapter returns, which is what lets one skill read the
    answer from any of them (I1).

    A missing author is left empty rather than invented. An automated merge
    genuinely has none, and "unknown" would read as a person nobody could find.
    """
    try:
        entries = json.loads(text or "[]")
    except ValueError as exc:
        raise ValueError("%s did not return the listing as JSON: %s"
                         % (BINARY, exc)) from exc
    if not isinstance(entries, list):
        raise ValueError("%s returned %s, expected a list"
                         % (BINARY, type(entries).__name__))

    records = []
    for entry in entries:
        author = entry.get("author") or {}
        records.append({
            "id": str(entry.get("number", "")),
            # The date alone. A timestamp to the second is noise in a list
            # somebody is scanning, and the full value is in the host anyway.
            "at": (entry.get("mergedAt") or "")[:10],
            "author": author.get("login", "") if isinstance(author, dict) else "",
            "title": entry.get("title", ""),
        })
    return records


#: A line comment has to name the commit it applies to, and that is not
#: something the caller knows. It is fetched first, from the change itself.
HEAD_FIELD = "headRefOid"


def pr_head(run, identifier, cwd=None):
    """The commit a comment on this change would attach to."""
    return run([BINARY, "pr", "view", identifier,
                "--json", HEAD_FIELD], cwd=cwd)


def parse_head(text):
    try:
        data = json.loads(text or "{}")
    except ValueError as exc:
        raise ValueError("%s did not return the change as JSON: %s"
                         % (BINARY, exc)) from exc
    head = (data or {}).get(HEAD_FIELD) or ""
    if not head:
        raise ValueError("%s did not report which commit the change ends at"
                         % BINARY)
    return head


def pr_comment(run, identifier, path, line, body_file, head, cwd=None):
    """Post one comment against one line of one file.

    The body travels as a file rather than as an argument: a review comment is
    prose with newlines and quoting in it, and the platform's argument limit
    and quoting rules are the wrong thing to be fighting while sending
    something under the user's name.

    ``{owner}/{repo}`` is left for the tool to fill in from the repository it
    is run in, for the same reason the diff verb does not name one (I2).
    """
    return run([BINARY, "api", "--method", "POST",
                "repos/{owner}/{repo}/pulls/%s/comments" % identifier,
                "-F", "body=@%s" % body_file,
                "-f", "path=%s" % path,
                "-F", "line=%d" % line,
                "-f", "commit_id=%s" % head,
                "-f", "side=RIGHT"], cwd=cwd)


def parse_comment(text):
    """Where the comment landed, if the answer says."""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return ""
    return (data or {}).get("html_url", "") if isinstance(data, dict) else ""


def pr_diff(run, identifier, cwd=None):
    """The unified diff for one pull request.

    The repository is left to the tool, which reads it from the git remote of
    the directory it runs in. Naming it here would mean this pack carrying a
    fact about one checkout on one machine (I2).
    """
    return run([BINARY, "pr", "diff", identifier], cwd=cwd)
