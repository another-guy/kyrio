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

from kyrio import capability, ingest, probe
from kyrio.cli import table
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


def _answered(adapter, ran):
    """Raise unless the tool actually answered.

    Shared by every verb: they fail the same ways for the same reasons, and
    two copies of this drift into two different messages for one problem.
    """
    if ran.outcome == probe.MISSING:
        raise ScmError("%s is configured for this machine but is not installed"
                       % adapter.BINARY)
    if not ran.answered:
        # The tool's own words, kept. A change that does not exist and a
        # credential that expired are both a non-zero exit, and only the
        # message separates them.
        raise ScmError("%s: %s" % (adapter.BINARY, ran.detail),
                       detail=ran.output.strip() or None)
    return ran


#: Every adapter returns records with these keys, whatever its own listing
#: looks like. The rendering below is written once against this shape.
LOG_KEYS = ("id", "at", "author", "title")


def log(resolution, since, label, cwd=None, runner=None):
    """Changes merged in a window.

    Nothing merged is a real answer, not a failure. A window with no changes
    in it is exactly what somebody asking "what shipped" needs to hear, and
    reporting it as an error would send them looking for a broken tool.
    """
    adapter = resolution.adapter
    runner = probe.run if runner is None else runner

    ran = _answered(adapter, adapter.log(runner, since, cwd=cwd))
    try:
        records = adapter.parse_log(ran.output)
    except ValueError as exc:
        raise ScmError(str(exc), detail=ran.output.strip() or None) from exc

    if not records:
        payload = "  nothing merged since %s (%s)\n" % (since, label)
    else:
        payload = table(("ID", "MERGED", "AUTHOR", "TITLE"),
                        [tuple(r.get(key, "") for key in LOG_KEYS)
                         for r in records])
    return Result("log", payload, provider=adapter.ID, since=since,
                  window=label, changes=len(records))


def manual_log_instructions(since, label):
    """What to do where a person is the transport."""
    return (
        "No automated transport for changes on this machine.\n"
        "\n"
        "  1. List what merged since %s (%s) wherever this team reviews code.\n"
        "  2. Save the list to a file, with the date and author of each.\n"
        "  3. Bring it back in:\n"
        "\n"
        "       kyrio ingest text --file <path>\n"
        % (since, label))


class Comment:
    """One comment, addressed to a place in a change."""

    def __init__(self, identifier, path, line, body):
        self.identifier = identifier
        self.path = path
        self.line = line
        self.body = body

    def rendered(self):
        """What the user is shown before deciding whether to send it."""
        return (
            "  change   %s\n"
            "  file     %s\n"
            "  line     %d\n"
            "\n"
            "%s\n" % (self.identifier, self.path, self.line,
                      self.body.rstrip("\n")))


def read_comment(identifier, path, line, body_file):
    """Assemble a comment, with the body read through the inbound door.

    The body is a file the broker did not produce, which is exactly what
    ``ingest`` exists for (S3): bounded, decoded, and put into one line-ending
    form before it can go anywhere. That the bound applies on the way *out* is
    the point -- an enormous or unreadable file is worth refusing hardest when
    something is about to be published under the user's name.
    """
    try:
        line = int(line)
    except (TypeError, ValueError):
        raise ScmError("--line takes a line number, got %r" % (line,)) from None
    if line < 1:
        raise ScmError("--line takes a line number, got %r" % (line,))
    if not path:
        raise ScmError("--file names the file the comment is about")

    try:
        read = ingest.ingest("text", body_file)
    except ingest.IngestError as exc:
        raise ScmError(str(exc)) from exc
    if not read.payload.strip():
        raise ScmError("%s is empty; there is nothing to say" % body_file)
    return Comment(identifier, path, line, read.payload)


def pr_comment(resolution, comment, body_file, post=False, cwd=None,
               runner=None):
    """Draft a comment, or send one.

    Drafting is the default and sending is the second, deliberate call. This
    is the one verb in the pack that other people can see the result of.
    """
    adapter = resolution.adapter
    runner = probe.run if runner is None else runner

    try:
        pinned = adapter.pr_identifier(comment.identifier)
    except ValueError as exc:
        raise ScmError(str(exc)) from exc

    if not post:
        return Result("comment", comment.rendered(), provider=adapter.ID,
                      id=pinned, path=comment.path, line=comment.line,
                      posted=False)

    # A line comment has to name the commit it applies to, which the caller
    # does not know. Fetched first, and a failure here stops the send.
    ran = _answered(adapter, adapter.pr_head(runner, pinned, cwd=cwd))
    try:
        head = adapter.parse_head(ran.output)
    except ValueError as exc:
        raise ScmError(str(exc), detail=ran.output.strip() or None) from exc

    sent = _answered(adapter, adapter.pr_comment(
        runner, pinned, comment.path, comment.line, body_file, head, cwd=cwd))
    where = adapter.parse_comment(sent.output)
    payload = comment.rendered()
    if where:
        payload += "\nposted: %s\n" % where
    return Result("comment", payload, provider=adapter.ID, id=pinned,
                  path=comment.path, line=comment.line, posted=True)


def manual_comment_instructions(comment):
    """What to do where a person is the transport."""
    return (
        "No automated transport for changes on this machine.\n"
        "\n"
        "  1. Open change %s wherever this team reviews code.\n"
        "  2. Add this comment against %s, line %d, exactly as written:\n"
        "\n"
        "%s\n"
        "Nothing was sent. Posting it is the user's to do.\n"
        % (comment.identifier, comment.path, comment.line,
           comment.body.rstrip("\n")))


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

    ran = _answered(adapter, adapter.pr_diff(runner, pinned, cwd=cwd))
    if not ran.output.strip():
        raise ScmError("%s returned an empty diff for %s"
                       % (adapter.BINARY, pinned))

    return Result("diff", ran.output, provider=adapter.ID, id=pinned,
                  **summarize(ran.output))
