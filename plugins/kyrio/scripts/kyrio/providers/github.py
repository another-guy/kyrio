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


def health(run):
    return run(HEALTH)


def auth(run):
    return run(AUTH)
