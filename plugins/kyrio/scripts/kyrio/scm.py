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

    The program named is the one that **ran**, not the one the adapter is
    named for. An adapter may reach more than one binary to finish a verb, and
    reporting the wrong one as missing sends a person to install something
    they already have.
    """
    ran_as = ran.argv[0] if ran.argv else adapter.BINARY

    if ran.outcome == probe.NO_CWD:
        # Named before the tool is, because it is not the tool's fault and a
        # message about the tool would be read as one.
        raise ScmError(ran.detail)
    if ran.outcome == probe.MISSING:
        raise ScmError("%s is configured for this machine but is not installed"
                       % ran_as)
    if not ran.answered:
        # The tool's own words, kept. A change that does not exist and a
        # credential that expired are both a non-zero exit, and only the
        # message separates them.
        raise ScmError("%s: %s" % (ran_as, ran.detail),
                       detail=ran.output.strip() or None)
    return ran


def _verb(adapter, name, verb=None):
    """One verb of an adapter, or the gap named as a gap.

    A host that cannot answer something is an ordinary fact about this
    machine's tooling, and it belongs in the same sentence as every other gap
    -- not in an attribute error raised from somewhere a person cannot read.

    ``verb`` is what the caller asked for, which is not always the member that
    turned out to be missing: posting a comment resolves a head revision
    first, and a person told that `pr head` is unavailable has been handed an
    implementation detail instead of an answer.
    """
    served = getattr(adapter, name, None)
    if served is None:
        raise ScmError("%s does not serve `scm %s` in this version"
                       % (adapter.ID, verb or name.replace("_", " ")))
    return served


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

    ran = _answered(adapter, _verb(adapter, "log")(runner, since, cwd=cwd))
    try:
        records = adapter.parse_log(ran.output)
    except ValueError as exc:
        raise ScmError(str(exc), detail=ran.output.strip() or None) from exc

    _refuse_if_cut_short(records, since, getattr(adapter, "LOG_LIMIT", None))
    records = _within(records, since)

    if not records:
        payload = "  nothing merged since %s (%s)\n" % (since, label)
    else:
        payload = table(("ID", "MERGED", "AUTHOR", "TITLE"),
                        [tuple(r.get(key, "") for key in LOG_KEYS)
                         for r in records])
    return Result("log", payload, provider=adapter.ID, since=since,
                  window=label, changes=len(records))


def _refuse_if_cut_short(records, since, limit):
    """Stop rather than under-report.

    Every listing verb has a ceiling. When a listing comes back sitting
    exactly on it, the answer is one of two things and the response looks
    identical either way: everything in the window, or as much of it as fitted.

    The difference is readable from the records themselves. If the oldest one
    returned is still newer than the window's start, the listing ran out
    before the window did, and whatever merged before it is missing. If the
    oldest is older than the start, the window closed first and the answer is
    complete.

    Refusing is the right failure here. "What shipped last week" is asked in
    order to act on the answer, and a short list that looks complete is acted
    on -- a release note missing four changes is worse than no release note,
    because nobody goes looking for what is not there.
    """
    if not since or not limit or len(records) < limit:
        return
    dated = [r.get("at") for r in records if r.get("at")]
    if dated and min(dated) < since:
        return
    raise ScmError(
        "the listing stopped at its limit of %d and never reached back to %s, "
        "so changes in that window are missing; ask for a shorter window"
        % (limit, since))


def _within(records, since):
    """Drop what merged before the window asked for.

    Applied here rather than in each adapter because hosts differ in whether
    they can filter at all: one takes a date in its query, another's listing
    verb has no date parameter of any kind. A window honoured by one and
    silently ignored by another is worse than no window, and which of those a
    machine gets is exactly the difference this layer exists to erase (I1).

    A record with no date is kept. It cannot be shown to fall outside the
    window, and dropping it would hide a change on the strength of a missing
    field.
    """
    if not since:
        return records
    return [r for r in records if not r.get("at") or r["at"] >= since]


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
    ran = _answered(adapter, _verb(adapter, "pr_head", "pr comment")(runner, pinned, cwd=cwd))
    try:
        head = adapter.parse_head(ran.output)
    except ValueError as exc:
        raise ScmError(str(exc), detail=ran.output.strip() or None) from exc

    sent = _answered(adapter, _verb(adapter, "pr_comment", "pr comment")(
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

    ran = _answered(adapter, _verb(adapter, "pr_diff")(runner, pinned, cwd=cwd))
    if not ran.output.strip():
        raise ScmError("nothing came back for %s, and a change under review "
                       "has a diff" % pinned)

    return Result("diff", ran.output, provider=adapter.ID, id=pinned,
                  **summarize(ran.output))
