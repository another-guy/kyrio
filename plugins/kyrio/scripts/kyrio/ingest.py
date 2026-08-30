"""S3 -- the single inbound door.

Data that originates outside the broker becomes broker-shaped here and nowhere
else. One door means one place to bound what arrives, one place to label it as
foreign, and one call site for a transformation the design has not yet decided
to need (D1, ``docs/DESIGN.md`` section 11).

Not every inbound path comes through here, and that is deliberate rather than
an oversight. A delegated result -- ``status: call`` -- is read by the model
that asked for it, in the turn that asked, and is never round-tripped back
through the broker; section 5 gives the reasoning. This door is for the other
traffic: a file that something outside produced, which a deterministic consumer
is about to read. Those two cases differ in who reads the payload, which is
exactly the axis normalization pays off on.

What it does today is small and complete: accept a declared kind, read a file
within a bound, prove it is text, put its line endings in one form, and hand it
back labelled ``external``. What it deliberately does not do is guess. A kind
that is not registered is an error naming the ones that are, never a silent
pass-through, because a door that accepts anything is not a door.

It is also not a store. Nothing is written, cached, or remembered; a caller
that wants the result kept writes it where its own rules say to (I7).

This module returns results; it never prints. ``__main__`` emits them (S1).
"""

import pathlib

from kyrio.repo import Result

#: Above this, a payload is not going to be read by anything in a model's
#: context, and reading it is likelier to be a mistake than an intention. The
#: limit is reported rather than applied silently: truncating text that someone
#: is about to draw a conclusion from is the worst of the available behaviours.
MAX_BYTES = 2 * 1024 * 1024


class IngestError(Exception):
    """The kind is unknown, or the file cannot be accepted as given."""


def _text(body):
    """Text with no structure claimed.

    The honest kind for a paste or a saved file: the door has bounded it,
    proved it is text, and normalized its line endings. It asserts nothing
    further, which is why it needs no parser and cannot mis-parse.
    """
    return body


#: Kind to normalizer. A kind is added when a caller needs it, together with
#: its fixtures (I8) -- never in advance, because a normalizer written before
#: its consumer encodes a guess about a shape nobody has seen.
KINDS = {
    "text": _text,
}


def ingest(kind, path, kinds=None, max_bytes=MAX_BYTES):
    """Read ``path`` as ``kind`` and return it labelled as foreign."""
    kinds = KINDS if kinds is None else kinds
    if not kind:
        raise IngestError(
            "usage: kyrio ingest <kind> --file <path>; kinds: %s"
            % ", ".join(sorted(kinds)))
    normalizer = kinds.get(kind)
    if normalizer is None:
        raise IngestError(
            "unknown kind: %s; kinds: %s" % (kind, ", ".join(sorted(kinds))))
    if not path:
        raise IngestError(
            "no file given; usage: kyrio ingest %s --file <path>" % kind)

    raw = _read(pathlib.Path(path), max_bytes)
    body = _guard(normalizer(_decode(raw)))
    return Result(kind, body, origin="external", bytes=len(raw),
                  lines=_line_count(body))


def _read(path, max_bytes):
    """The bytes of ``path``, or an error saying precisely what stopped it."""
    try:
        path = path.resolve()
    except OSError as exc:
        raise IngestError("cannot resolve %s: %s" % (path, exc)) from exc
    if path.is_dir():
        raise IngestError("%s is a directory, not a file" % path)
    try:
        size = path.stat().st_size
    except FileNotFoundError as exc:
        raise IngestError("no such file: %s" % path) from exc
    except OSError as exc:
        raise IngestError("cannot read %s: %s" % (path, exc)) from exc
    if size > max_bytes:
        raise IngestError(
            "%s is %d bytes; the limit is %d. Pass the part that matters."
            % (path, size, max_bytes))
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise IngestError("cannot read %s: %s" % (path, exc)) from exc
    if b"\x00" in raw:
        raise IngestError("%s is not text" % path)
    return raw


def _decode(raw):
    """Text in one form.

    ``errors="replace"`` because a byte that will not decode is not a reason to
    lose the rest of a file, and line endings are put in one form because the
    same content arriving from two machines must otherwise compare unequal (I9).
    """
    text = raw.decode("utf-8", errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _guard(body):
    """The one transformation point for data the broker did not produce.

    Deliberately an identity function. It exists so that D1, if it is ever
    taken, attaches at a call site that is already here and already the only
    one -- rather than starting with a search for every place foreign data
    entered.
    """
    return body


def _line_count(body):
    if not body:
        return 0
    return body.count("\n") + (0 if body.endswith("\n") else 1)
