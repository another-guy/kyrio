"""Provider adapters. The only path in the tree where a provider may be named.

Everywhere else, a provider is a value read from configuration and passed
through. Here it is a module name, and that asymmetry is the whole of I1: a
skill that cannot move to a machine with entirely different tooling without
being edited has leaked provider knowledge into the wrong layer.

Every adapter declares:

- ``ID`` -- the value a config layer carries in ``capabilities.*.provider``
- ``CAPABILITIES`` -- which of the broker's nouns it can serve
- ``TRANSPORT`` -- how it is reached
- ``BINARY`` -- what it runs, for messages
- ``health(run)`` and ``auth(run)`` -- two probes, never one

and every adapter:

- constructs arguments; never interpolates caller input into a shell string (I5)
- runs its binary via the injected ``run``, with an argument list, never a
  shell string
- parses into the broker's shape and returns it; never prints (S1)
- ships unit tests for argument construction and output parsing, against
  hand-written fixtures (I8)

``health`` and ``auth`` are separate because *installed* and *signed in* are
different states with different owners: one is an install a person may not be
permitted to perform, the other is a sign-in nobody but them can do. Collapsing
them into one boolean sends half of all failures to the wrong remedy.

They are functions rather than constants because an adapter may need more
than a fixed argument list, and the second one does.

An adapter is not required to serve every verb of a capability it declares.
Hosts differ in what they can answer, and a verb one of them cannot serve is a
gap in this pack rather than a broken machine -- the capability module names it
as one. Nor is an adapter limited to a single binary: where a host has no verb
for something, an adapter may reach ordinary developer tooling to finish the
job, and the failure it reports then names the program that actually ran.

A provider is added when a workflow needs it, together with the fixtures that
prove it parses. An adapter written earlier encodes a guess about a shape
nobody has run against, which is the same reason ``ingest`` ships one kind.

A registry with nothing for a configured provider is not a broken machine.
``capability.resolve`` reports that as configured but not usable, so the
message a person gets names the gap in this pack rather than sending them to
fix configuration that is already right.
"""

from kyrio.providers import azure_devops, github

#: Provider id to adapter. The id is the value a config layer puts in
#: ``capabilities.<name>.provider``.
ADAPTERS = {
    azure_devops.ID: azure_devops,
    github.ID: github,
}


def get(provider):
    """The adapter for a provider id, or ``None`` if none ships."""
    return ADAPTERS.get(provider)


def known():
    """Every provider id this pack can serve, sorted."""
    return sorted(ADAPTERS)


def for_capability(name):
    """Adapters that can serve one capability, by id."""
    return [ADAPTERS[key] for key in sorted(ADAPTERS)
            if name in ADAPTERS[key].CAPABILITIES]
