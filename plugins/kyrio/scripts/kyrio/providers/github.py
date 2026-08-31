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


def pr_diff(run, identifier, cwd=None):
    """The unified diff for one pull request.

    The repository is left to the tool, which reads it from the git remote of
    the directory it runs in. Naming it here would mean this pack carrying a
    fact about one checkout on one machine (I2).
    """
    return run([BINARY, "pr", "diff", identifier], cwd=cwd)
