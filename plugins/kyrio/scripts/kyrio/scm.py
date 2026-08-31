"""The ``scm`` capability: changes proposed for review, and their diffs.

This module knows what a change *is* and what a unified diff looks like. It
does not know who hosts it. Everything provider-shaped -- the binary, its
arguments, the form an identifier takes -- lives behind the adapter resolved
for this machine, so the same call reaches a different system on a different
machine without a line of this file changing (I1).

A unified diff is parsed here rather than in an adapter on purpose. It is a
format, not a provider's format: every host that can produce a diff produces
this one, and a counter written per adapter would be the same code copied once
per provider, drifting.

The manual transport is served here too, and it is not a stub. Where a person
is the transport, the instructions end at ``kyrio ingest``, which is the only
door data the broker did not produce comes through (S3).

This module returns results; it never prints. ``__main__`` emits them (S1).
"""

import re

from kyrio import capability, probe
from kyrio.repo import Result


class ScmError(Exception):
    """The change could not be fetched, or was named in a way its host does
    not recognize.

    ``detail`` carries the tool's own output where there is any. Quoting it is
    usually the whole diagnosis: a change that does not exist and a credential
    that expired are both a non-zero exit, and only the message separates them.
    """

    def __init__(self, message, detail=None):
        super().__init__(message)
        self.message = message
        self.detail = detail


#: A file header in a unified diff. Counted rather than parsed: the count says
#: how large a change is, which is what a reader needs before opening it.
_FILE_RE = re.compile(r"^diff --git ", re.M)

#: Added and removed lines, excluding the ``+++``/``---`` file markers.
_ADDED_RE = re.compile(r"^\+(?!\+\+ )", re.M)
_REMOVED_RE = re.compile(r"^-(?!-- )", re.M)


def summarize(diff):
    """How big this change is, read from the diff itself."""
    return {
        "files": len(_FILE_RE.findall(diff)),
        "added": len(_ADDED_RE.findall(diff)),
        "removed": len(_REMOVED_RE.findall(diff)),
    }


def requires_manual(resolution):
    """Whether a person is the transport for this capability here."""
    return resolution.transport == capability.MANUAL


def manual_diff_instructions(identifier):
    """What to do where a person is the transport.

    Deliberately free of any product's vocabulary. This text is read on a
    machine whose tooling this pack has never heard of, and naming a place to
    click would be wrong on most of them.
    """
    return (
        "No automated transport for changes on this machine.\n"
        "\n"
        "  1. Open change %s wherever this team reviews code.\n"
        "  2. Save the complete diff to a file. Do not shorten it.\n"
        "  3. Bring it back in:\n"
        "\n"
        "       kyrio ingest text --file <path>\n"
        % identifier)


def pr_diff(resolution, identifier, cwd=None, runner=None):
    """The diff for one change under review.

    ``resolution`` arrives already decided and already usable. Deciding it is
    the command layer's job, so that every capability reports a gap the same
    way rather than each noun inventing its own wording.
    """
    adapter = resolution.adapter
    runner = probe.run if runner is None else runner

    # The adapter validates its own identifier shape. A change is a number in
    # one system and a forty-character hash in another, so a rule written here
    # would be either wrong somewhere or so loose it caught nothing anywhere.
    try:
        pinned = adapter.pr_identifier(identifier)
    except ValueError as exc:
        raise ScmError(str(exc)) from exc

    ran = adapter.pr_diff(runner, pinned, cwd=cwd)
    if ran.outcome == probe.MISSING:
        raise ScmError(
            "%s is configured for this machine but is not installed"
            % adapter.BINARY)
    if not ran.answered:
        raise ScmError("%s: %s" % (adapter.BINARY, ran.detail),
                       detail=ran.output.strip() or None)

    if not ran.output.strip():
        raise ScmError("%s returned an empty diff for %s"
                       % (adapter.BINARY, pinned))

    return Result("diff", ran.output, provider=adapter.ID, id=pinned,
                  **summarize(ran.output))
