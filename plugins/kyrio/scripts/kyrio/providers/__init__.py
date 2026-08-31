"""Provider adapters. The only path in the tree where a provider may be named.

Everywhere else, a provider is a value read from configuration and passed
through. Here it is a module name, and that asymmetry is the whole of I1: a
skill that cannot move to a machine with entirely different tooling without
being edited has leaked provider knowledge into the wrong layer.

Every adapter:

- constructs arguments; never interpolates caller input into a shell string (I5)
- runs its binary via ``subprocess`` with an argument list, never ``shell=True``
- parses into the broker's shape and returns it; never prints (S1)
- ships unit tests for argument construction and output parsing, against
  hand-written fixtures (I8)

The registry is empty, and that is a decision rather than a gap. An adapter
written before the workflow that calls it encodes a guess about a shape nobody
has run against -- the same reason ``ingest`` ships one kind. A provider is
added when a workflow needs it, together with the fixtures that prove it parses.

An empty registry is not a broken machine. ``capability.resolve`` reports a
correctly configured capability with no shipped adapter as configured but not
usable, so the message a person gets names the gap in this pack rather than
sending them to fix configuration that is already right.
"""

#: Provider id to adapter. The id is the value a config layer puts in
#: ``capabilities.<name>.provider``.
ADAPTERS = {}


def get(provider):
    """The adapter for a provider id, or ``None`` if none ships."""
    return ADAPTERS.get(provider)


def known():
    """Every provider id this pack can serve, sorted."""
    return sorted(ADAPTERS)
