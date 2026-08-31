"""Transport resolution -- which door a capability goes through, and why not.

Preference runs in one order: a connected server, then a CLI, then a
browser-driven path, then manual. ``/kyrio:setup`` walks that order when it
probes and records the answer; this module reads the recorded answer back and
turns it into a decision the command layer can act on without judgment of its
own. Resolution is therefore a configuration read, not a probe: nothing here
runs a binary, and a capability that stopped working since setup ran still
resolves to whatever setup wrote.

A capability with no working transport resolves to ``unavailable`` carrying its
remediation -- never to ``manual``. Manual means the user is the transport, and
a pack that silently degrades into asking a person to paste things is worse
than one that says a capability is not configured here and how to configure it.
Manual stays available as a deliberate per-capability opt-in.

Two questions get separate answers, because they have separate fixes.
``status`` is a verdict on the configuration and is what ``caps`` and
``probe`` report. ``usable`` additionally requires that something ships that
can actually serve the transport, which is what a call site must check. A
machine can be configured correctly for a provider this pack has no adapter
for, and telling a person their configuration is wrong in that case would send
them to fix the one thing that is right.

Provider names appear here only as values read from configuration and repeated
back in a message. No provider name is written in this file; ``providers/`` is
the only path where one may be (I1).

This module never writes output. See ``emit`` (S1).
"""

import re

from kyrio import config, providers

SERVER = "server"
CLI = "cli"
BROWSER = "browser"
MANUAL = "manual"

#: The order a probe prefers. A connected server leads because it is the only
#: transport that can distinguish *connected* from *installed* from *needs
#: auth*, and that distinction is the entire reason setup is re-runnable.
#: Manual is last and is never reached by falling through: it is opted into.
TRANSPORT_ORDER = (SERVER, CLI, BROWSER, MANUAL)

#: Outside the order. ``local`` needs no transport at all and belongs only to
#: an intrinsic capability. ``unavailable`` is how a layer turns a capability
#: off: the cascade deep-merges, so absence can never mean off, and switching
#: something off has to be a value someone wrote (docs/DESIGN.md section 4).
LOCAL = "local"
UNAVAILABLE_TRANSPORT = "unavailable"

KNOWN_TRANSPORTS = frozenset(TRANSPORT_ORDER) | {LOCAL, UNAVAILABLE_TRANSPORT}

#: What a person may assign. ``local`` is missing on purpose: it belongs to a
#: capability that needs no configuration, so offering it in a list of choices
#: and then refusing it would be the list's fault, not the caller's.
ASSIGNABLE_TRANSPORTS = KNOWN_TRANSPORTS - {LOCAL}

#: Transports served by an adapter under ``providers/``. The other two are
#: served by the caller: a server transport returns a tool call for Claude Code
#: to make, and manual returns instructions for a person.
ADAPTER_TRANSPORTS = frozenset({CLI, BROWSER})

READY = "ready"
CONFIGURED = "configured"
UNCONFIGURED = "unconfigured"
UNAVAILABLE = "unavailable"

#: The vocabulary every report uses. ``caps`` and ``probe report`` resolve
#: through this module precisely so the same machine cannot be described two
#: different ways by two different commands.
STATUSES = (READY, CONFIGURED, UNCONFIGURED, UNAVAILABLE)

SETUP_HINT = "run /kyrio:setup to probe this machine and record the result"


class SpecError(Exception):
    """A capability assignment the broker will not write.

    Raised, never printed: the caller turns it into an ``error`` response.
    """


def parse_assignment(text):
    """``<capability>=<spec>`` into a name and an entry, or refuse.

    Setup proposes; this is where a proposal stops being text. Everything the
    broker will write goes through here, so an entry that cannot be resolved
    later cannot be recorded now -- the alternative is a config file that
    passes validation and then reports itself unusable forever.
    """
    name, separator, spec = text.partition("=")
    name = name.strip()
    if not separator or not name:
        raise SpecError(
            "expected <capability>=<transport>[:<value>], got %r" % text)
    if name in config.INTRINSIC:
        raise SpecError(
            "%s needs no configuration; it reads the working tree" % name)
    if name not in config.CAPABILITIES:
        raise SpecError("unknown capability %r; known are %s"
                        % (name, ", ".join(config.CAPABILITIES)))
    return name, parse_spec(spec.strip())


def parse_spec(spec):
    """``<transport>[:<value>]`` into a capability entry.

    A value is required exactly where resolution needs one, so the two cannot
    disagree: ``cli`` and ``browser`` name a provider, ``server`` names a tool
    prefix, and ``manual`` and ``unavailable`` take nothing because there is
    nothing about them to configure.
    """
    transport, separator, value = spec.partition(":")
    transport = transport.strip()
    value = value.strip()

    if transport == LOCAL:
        raise SpecError("the local transport belongs only to %s"
                        % ", ".join(sorted(config.INTRINSIC)))
    if transport not in ASSIGNABLE_TRANSPORTS:
        raise SpecError("unknown transport %r; known are %s"
                        % (transport, ", ".join(sorted(ASSIGNABLE_TRANSPORTS))))

    if transport in (MANUAL, UNAVAILABLE_TRANSPORT):
        if separator:
            raise SpecError("%s takes no value, got %r" % (transport, value))
        return {"transport": transport}

    if not value:
        needed = "tool prefix" if transport == SERVER else "provider"
        raise SpecError("%s needs a %s: %s:<%s>"
                        % (transport, needed, transport,
                           needed.replace(" ", "-")))

    if transport == SERVER:
        return {"transport": SERVER, "tool_prefix": value}

    # A comma-separated list is the ordered fallthrough: try the first,
    # fall through to the next.
    names = [normalize_provider(part) for part in value.split(",")
             if part.strip()]
    return {"transport": transport,
            "provider": names[0] if len(names) == 1 else names}


#: A provider id has to match what an adapter registers under, and setup takes
#: this from whatever a person typed. Canonicalizing here means "Provider A"
#: and "provider-a" reach the same adapter instead of one of them silently
#: reaching none. A tool prefix is deliberately *not* normalized: that one is
#: case-sensitive and derived rather than typed.
_PROVIDER_SEPARATORS = re.compile(r"[^0-9a-z]+")


def normalize_provider(text):
    return _PROVIDER_SEPARATORS.sub("-", text.strip().lower()).strip("-")


class Resolution:
    """What configuration says about one capability, decided.

    Carries the remediation with the verdict rather than leaving the caller to
    compose one. A message written where the reason is known is specific; a
    message written at the call site degrades into "not configured".

    A remediation never names its own capability. Every caller already has the
    name -- as a row label, or in the response header -- and a name repeated
    inside the text stops two identical gaps from collapsing into one line.
    """

    def __init__(self, capability, status, transport=None, provider=None,
                 adapter=None, tool_prefix=None, remediation=None,
                 tried=()):
        self.capability = capability
        self.status = status
        self.transport = transport
        self.provider = provider
        self.adapter = adapter
        self.tool_prefix = tool_prefix
        self.remediation = remediation
        self.tried = tuple(tried)

    @property
    def usable(self):
        """Whether a call can actually be made through this capability."""
        if self.status not in (READY, CONFIGURED):
            return False
        if self.transport in ADAPTER_TRANSPORTS:
            return self.adapter is not None
        return True

    def __repr__(self):
        return "Resolution(%s, %s, transport=%s)" % (
            self.capability, self.status, self.transport)


def resolve(name, resolved, registry=None):
    """Decide one capability against the merged configuration.

    ``registry`` is injectable so that resolution can be tested without
    shipping an adapter, and so that a test cannot be made to pass by adding
    one.
    """
    registry = providers if registry is None else registry

    if name in config.INTRINSIC:
        return Resolution(name, READY, transport=LOCAL)

    entry = _entry(resolved, name)
    if entry is None:
        return _unconfigured(name, "not configured on this machine")

    transport = entry.get("transport")
    if transport in (None, ""):
        return _unconfigured(name, "no transport is set")

    where = _where(resolved, name)

    if transport == UNAVAILABLE_TRANSPORT:
        # Someone decided this. Sending them to setup would undo a decision
        # rather than fix a fault, so the fix named is the one they made.
        return Resolution(
            name, UNAVAILABLE, transport=UNAVAILABLE_TRANSPORT,
            remediation="turned off%s; set a transport there to turn it back on"
                        % where)

    if transport not in KNOWN_TRANSPORTS:
        return Resolution(
            name, UNAVAILABLE,
            remediation="transport %r%s is not one this broker knows; known "
                        "transports are %s"
                        % (transport, where,
                           ", ".join(sorted(KNOWN_TRANSPORTS))))

    if transport == LOCAL:
        return Resolution(
            name, UNAVAILABLE,
            remediation="the local transport%s belongs only to %s"
                        % (where, ", ".join(sorted(config.INTRINSIC))))

    if transport == SERVER:
        prefix = entry.get("tool_prefix")
        if not prefix:
            return Resolution(
                name, UNAVAILABLE, transport=SERVER,
                remediation="the server transport%s names no tool_prefix, so "
                            "no tool can be named; %s" % (where, SETUP_HINT))
        return Resolution(name, CONFIGURED, transport=SERVER,
                          tool_prefix=prefix)

    if transport == MANUAL:
        return Resolution(name, CONFIGURED, transport=MANUAL)

    # cli and browser are served by an adapter, chosen by provider.
    candidates = _providers(entry)
    if not candidates:
        return Resolution(
            name, UNAVAILABLE, transport=transport,
            remediation="the %s transport%s names no provider, so no adapter "
                        "can be chosen; %s" % (transport, where, SETUP_HINT))

    # An ordered list is how a machine mid-migration between products is
    # expressed: try the first, fall through to the next. No skill above
    # learns that a migration is underway.
    for provider in candidates:
        adapter = registry.get(provider)
        if adapter is not None:
            return Resolution(name, CONFIGURED, transport=transport,
                              provider=provider, adapter=adapter,
                              tried=candidates)

    # The configuration is complete and this pack simply has nothing that
    # speaks to it. That is a gap in the pack, not a mistake by the person who
    # wrote the config, and the message says so.
    return Resolution(
        name, CONFIGURED, transport=transport, tried=candidates,
        remediation="no adapter ships for %s (tried: %s)"
                    % (_plural(candidates), ", ".join(candidates)))


def resolve_all(resolved, registry=None):
    """Every capability, in report order."""
    return [resolve(name, resolved, registry=registry)
            for name in config.CAPABILITIES]


def rows(resolved, registry=None):
    """``(capability, transport, status)`` for a report table."""
    return [(r.capability, r.transport or "--", r.status)
            for r in resolve_all(resolved, registry=registry)]


def _entry(resolved, name):
    entry = (resolved.get("capabilities") or {}).get(name)
    if entry is None:
        return None
    # A bare string is accepted as shorthand for the transport. The cascade is
    # hand-authored at three of its four levels, and a shorthand that reads
    # identically to the long form is not a second shape to reason about.
    if isinstance(entry, str):
        return {"transport": entry}
    if not isinstance(entry, dict):
        return {"transport": None}
    return entry


def _providers(entry):
    """One key, either shape: a provider id, or an ordered list of them."""
    value = entry.get("provider")
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if v]


def _where(resolved, name):
    """The layer that set this transport, for a message that can be acted on.

    Provenance is the whole reason the cascade records it: told only that a
    capability is off, a person has four files to search.
    """
    sources = resolved.sources("capabilities.%s.transport" % name)
    return " (set in %s)" % sources[-1] if sources else ""


def _unconfigured(name, message):
    return Resolution(name, UNCONFIGURED,
                      remediation="%s; %s" % (message, SETUP_HINT))


def _plural(candidates):
    return "that provider" if len(candidates) == 1 else "any of those providers"
