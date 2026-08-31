"""S1 -- the single emit chokepoint.

Every response leaves the broker through this module. No other module writes to
stdout. That rule is what makes output shape, framing, and (later) tracing and
versioning changeable in one place; it is enforced by
``tests/test_emit.py::TestChokepoint``.

Wire format: one line of JSON, then ``---``, then the payload verbatim.

    {"status":"ok","kind":"diff","source":{"transport":"cli"},"files":12}
    ---
    diff --git a/src/handler.ts b/src/handler.ts

A reader recovers the header with one ``readline`` and one ``json.loads``. The
payload is printed raw rather than escaped into a JSON string field: most of
what the broker returns is multi-line text, and escaping it costs tokens and
readability for no gain.

Responses that carry no payload are a header line and nothing else -- no
delimiter. See ``docs/DESIGN.md`` section 5.
"""

import json
import sys

EXIT_OK = 0
EXIT_ERROR = 1

DELIMITER = "---"

#: Statuses whose meaning is complete in the header. Emitting a payload with
#: one of these is a programming error, not a runtime condition.
_HEADER_ONLY = frozenset({"call", "unavailable"})

#: Only a genuine failure exits non-zero. Delegation, manual transport, and an
#: unconfigured capability are ordinary control flow: reporting them as
#: failures invites the caller to retry and to hunt for alternative commands.
_EXIT_CODES = {
    "ok": EXIT_OK,
    "call": EXIT_OK,
    "draft": EXIT_OK,
    "manual": EXIT_OK,
    "unavailable": EXIT_OK,
    "error": EXIT_ERROR,
}

_CALL_NEXT = "This is not the result. Call the tool named above, then continue."
_DRAFT_NEXT = (
    "Nothing was sent. Show this to the user exactly as it stands, and re-run "
    "with --post only after they have said yes."
)
_MANUAL_NEXT = (
    "No automated transport for this on this machine. Give the user the "
    "instructions below and wait."
)


def ok(kind, payload=None, *, transport=None, **meta):
    """A real result. ``kind`` names the payload's shape, e.g. ``diff``."""
    header = {"status": "ok", "kind": kind}
    if transport is not None:
        header["source"] = {"transport": transport}
    header.update(meta)
    return _write(header, payload)


def call(tool, args, *, expect=None, next=_CALL_NEXT):
    """Delegate: the broker cannot invoke this tool, so the caller must.

    Python cannot call a Claude Code tool. Where a capability is served by a
    connected server rather than a binary, the broker returns the call to make.
    The result is read directly and is *not* round-tripped back through
    ``ingest`` -- see ``docs/DESIGN.md`` section 5.
    """
    header = {"status": "call", "tool": tool, "args": args}
    if expect:
        header["expect"] = list(expect)
    header["next"] = next
    return _write(header, None)


def draft(kind, payload, *, next=_DRAFT_NEXT, **meta):
    """Written, not sent. The default for every write verb (I6).

    A wrong comment on a colleague's change is a professional cost rather than
    a technical one, and deleting it thirty seconds later does not unsend the
    notification. So the safe path is the one that happens by default, and
    sending takes a second, deliberate call.

    Exits zero: a draft is a successful outcome, not a refusal.
    """
    header = {"status": "draft", "kind": kind}
    header.update(meta)
    header["next"] = next
    return _write(header, payload)


def manual(capability, instructions, *, next=_MANUAL_NEXT):
    """The user is the transport. A deliberate per-capability opt-in."""
    header = {"status": "manual", "capability": capability, "next": next}
    return _write(header, instructions)


def unavailable(capability, remediation):
    """Not configured on this machine, with the fix.

    Distinct from ``manual`` on purpose: a pack that silently degrades into
    asking the user to paste things is worse than one that says a capability is
    not configured here and how to configure it.
    """
    header = {
        "status": "unavailable",
        "capability": capability,
        "remediation": remediation,
    }
    return _write(header, None)


def error(message, detail=None, **meta):
    """Bad arguments, broken transport, expired auth. The only non-zero exit."""
    header = {"status": "error", "message": message}
    header.update(meta)
    return _write(header, detail)


def _write(header, payload, stream=None):
    """The chokepoint itself. Returns the process exit code.

    Output-shape versioning and tracing attach here if they are ever needed.
    """
    status = header["status"]
    if status not in _EXIT_CODES:
        raise ValueError(f"unknown status: {status!r}")
    if payload is not None and status in _HEADER_ONLY:
        raise ValueError(f"status {status!r} carries no payload")

    if stream is None:
        stream = sys.stdout
        # A diff or a commit body will contain characters the console's legacy
        # codepage cannot encode. Never let that turn a result into a crash.
        #
        # newline="" turns off the platform's line-ending translation, so the
        # response is the same bytes everywhere. Without it Windows rewrites
        # every "\n" on the way out, and a payload the broker deliberately
        # normalized leaves in a second form (I9).
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace", newline="")

    # separators= keeps the header on one line and free of padding; a reader
    # relies on exactly one line preceding the delimiter.
    line = json.dumps(header, separators=(",", ":"), ensure_ascii=False)
    stream.write(line + "\n")

    if payload is not None:
        stream.write(DELIMITER + "\n")
        stream.write(payload)
        if not payload.endswith("\n"):
            stream.write("\n")

    stream.flush()
    return _EXIT_CODES[status]
