"""Adapter for Azure DevOps, through the Azure command-line tool.

The second adapter, and the one that tests whether the contract was written
around a provider or around a capability. Nothing above this file knows it
exists; it is reached only by the id below appearing as a *value* in a
machine's configuration (I1).

**It never names where it points.** The tool reads that from the repository's
own remote, or from defaults a person configured on this machine. Passing it
here would put a fact about one machine's surroundings into code that is
cloned everywhere, which is the thing I2 exists to prevent -- and this pack
has no vocabulary for a grouping above the machine, deliberately.

**Two binaries, not one.** This host has no verb that returns a unified diff:
its listing verbs answer in JSON and there is no equivalent of "print the
patch". So the diff is assembled in two steps -- ask this host which two
commits the change spans, then ask ``git`` locally for the difference between
them. That is a real widening of the adapter contract, and it is why the
failure path below reports the binary that actually ran rather than the one
this adapter is named for: told that the wrong program is missing, a person
goes looking for a problem they do not have.

Nothing here fetches. If the two commits are not present locally, ``git`` says
so and that message is carried back verbatim. Fetching on somebody's behalf
would write into a repository this pack was only asked to read (I7).

Authentication is decided by the **exit code**, never by reading the output.
One limitation is worth stating rather than hiding: the probe below proves a
sign-in to the platform, which is one of two ways a machine may be entitled to
reach this host. A machine authorized the other way reports as not signed in
while working perfectly. That is a false negative, and it is the safe
direction -- *not authenticated* is ambiguous by design, so setup carries it
into the pass where a person is asked outright rather than proposing it.

Nothing here installs anything and nothing here starts a sign-in. Both remedy
strings are printed for a person to run themselves.
"""

import json
import re

#: The value a machine's config carries in ``capabilities.<name>.provider``.
ID = "azure-devops"

CAPABILITIES = ("scm",)
TRANSPORT = "cli"
BINARY = "az"

#: The second binary. Ordinary developer tooling rather than a provider's, and
#: the only reason it is named here is that this host cannot produce a diff.
GIT = "git"

#: Does the binary run at all. By execution, never by presence.
HEALTH = [BINARY, "--version"]

#: Is this machine signed in. Deliberately not scoped to any one destination:
#: which one a machine reaches is a fact about that machine (I2).
AUTH = [BINARY, "account", "show"]

#: Printed, never run.
LOGIN = "az login"
INSTALL = ("install the Azure CLI and its DevOps extension: "
           "https://learn.microsoft.com/cli/azure/install-azure-cli")


#: How a change is named here: a pull request id, and nothing else. Validated
#: by the adapter rather than centrally, because the same concept is a
#: forty-character hash elsewhere and one rule cannot fit both.
PR_ID_RE = re.compile(r"\A[1-9][0-9]{0,9}\Z")

#: What ``pr show`` is asked for, and what its answer is read out of. Named as
#: constants because they are this host's vocabulary, and the point of the
#: file is that its vocabulary stops here.
SHOW_FORMAT = "json"
SOURCE_FIELD = "lastMergeSourceCommit"
TARGET_FIELD = "lastMergeTargetCommit"
COMMIT_FIELD = "commitId"


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
            "a pull request is named by its id, got %r" % (text or ""))
    return pinned


def pr_show(run, identifier, cwd=None):
    """What this host knows about one change, including its two ends."""
    return run([BINARY, "repos", "pr", "show",
                "--id", identifier,
                "--output", SHOW_FORMAT], cwd=cwd)


def parse_ends(text):
    """The two commits a change spans, as ``(target, source)``.

    Target first, because that is the order the difference is asked for: what
    the source branch adds to the target, not the reverse.
    """
    try:
        data = json.loads(text or "{}")
    except ValueError as exc:
        raise ValueError("%s did not return the change as JSON: %s"
                         % (BINARY, exc)) from exc
    if not isinstance(data, dict):
        raise ValueError("%s returned %s, expected one change"
                         % (BINARY, type(data).__name__))

    ends = []
    for field in (TARGET_FIELD, SOURCE_FIELD):
        commit = data.get(field) or {}
        value = commit.get(COMMIT_FIELD, "") if isinstance(commit, dict) else ""
        if not value:
            raise ValueError(
                "%s did not report which commits the change spans" % BINARY)
        ends.append(value)
    return ends[0], ends[1]


#: What a listing is asked for. There is no date filter to ask for: this
#: host's listing verb takes a status and a ceiling and nothing else, so the
#: window is applied by the capability after parsing. That is the right place
#: for it anyway -- "since" is a promise the capability makes, and a promise
#: kept by one host's query parameter and broken by another's absence is not a
#: promise (I9).
LIST_STATUS = "completed"

#: The most this verb will return. Named the same in every adapter, because
#: the capability has to be able to ask "did the answer stop here because the
#: window ended, or because the listing did?" -- and it cannot ask that of a
#: constant whose name it does not know.
LOG_LIMIT = 100


def log(run, since, limit=LOG_LIMIT, cwd=None):
    """Changes completed here, most recent first.

    ``since`` is accepted and deliberately unused: the signature belongs to
    the capability, and an adapter that cannot honour part of it says so by
    leaving the work to the caller rather than by having a different shape.
    """
    return run([BINARY, "repos", "pr", "list",
                "--status", LIST_STATUS,
                "--top", str(limit),
                "--output", SHOW_FORMAT], cwd=cwd)


def parse_log(text):
    """This host's JSON into the broker's records.

    The author is taken as a display name, never as the address beside it. A
    listing exists to say who merged something, and that question is answered
    without putting somebody's mail address into a payload this pack prints,
    stores, and hands to a model.
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
        author = entry.get("createdBy") or {}
        records.append({
            "id": str(entry.get("pullRequestId", "")),
            # The date alone. A timestamp to the second is noise in a list
            # somebody is scanning.
            "at": (entry.get("closedDate") or "")[:10],
            "author": (author.get("displayName", "")
                       if isinstance(author, dict) else ""),
            "title": entry.get("title", ""),
        })
    return records


def pr_diff(run, identifier, cwd=None):
    """The unified diff for one change, assembled in two steps.

    The first call is this host's; the second is ``git``'s, run against the
    repository the caller is in. A failure in either is returned as it came
    back, so the message names the program that actually failed.

    ``a...b`` rather than ``a b``: what matters in review is what the change
    adds to its target, not everything that has happened on the target since
    the branch left it.
    """
    shown = pr_show(run, identifier, cwd=cwd)
    if not shown.answered:
        return shown

    target, source = parse_ends(shown.output)
    return run([GIT, "diff", "%s...%s" % (target, source)], cwd=cwd)
