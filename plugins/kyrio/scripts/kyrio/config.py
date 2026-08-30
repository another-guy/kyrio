"""L0 -- the configuration cascade, and S2, its declarative merge strategies.

Starting from the working directory, walk upward. Every directory holding a
``.kyrio/config.json`` contributes a layer. The machine layer is always the
base, and nearer layers win. A layer may set ``"root": true`` to stop the walk
at its own directory.

Merging is driven by a table of key path to strategy rather than by a single
hardcoded rule, so that adding a key with unusual merge semantics is a table
entry and not a change to the resolver (S2).

Every effective value carries the layers that produced it, which is what
``kyrio config explain`` prints. JSON cannot carry comments; provenance is what
replaces them.

This module never writes output. See ``emit`` (S1).
"""

import json
import pathlib

CONFIG_DIRNAME = ".kyrio"
CONFIG_FILENAME = "config.json"

#: The machine layer, written by ``/kyrio:setup`` from probing.
MACHINE_CONFIG = pathlib.Path.home() / ".claude" / "kyrio" / CONFIG_FILENAME

#: The only schema version this resolver understands. A layer written by a
#: newer plugin must fail loudly rather than be silently half-read.
SCHEMA_VERSION = 1

#: Keys that describe the layer itself and are not configuration values.
META_KEYS = frozenset({"schema", "root"})

NEAREST_WINS = "nearest-wins"
DEEP_MERGE = "deep-merge"
UNION_APPEND = "union-append"
MONOTONIC_TIGHTEN = "monotonic-tighten"

#: Key path to strategy. Longest matching prefix wins; a strategy applies to
#: everything beneath its path unless a longer entry overrides it. Anything
#: unlisted is ``nearest-wins``, which is the right default for a scalar.
STRATEGIES = {
    "capabilities": DEEP_MERGE,
    "conventions": DEEP_MERGE,
    "catalog": DEEP_MERGE,
    "search_roots": UNION_APPEND,
    "ignore": UNION_APPEND,
}

DEFAULT_STRATEGY = NEAREST_WINS


class ConfigError(Exception):
    """A layer is unreadable, malformed, or violates its strategy.

    Raised, never printed: the caller turns it into an ``error`` response.
    """


class Layer:
    """One ``config.json`` and where it came from."""

    def __init__(self, path, data):
        self.path = path
        self.data = data
        self.is_root = bool(data.get("root", False))

    @property
    def label(self):
        """How this layer is named in ``config explain`` output."""
        return str(self.path)

    def values(self):
        return {k: v for k, v in self.data.items() if k not in META_KEYS}

    def __repr__(self):
        return "Layer(%s)" % self.path


class Resolved:
    """The effective configuration plus, per key path, who supplied it."""

    def __init__(self, values, provenance, layers):
        self.values = values
        self.provenance = provenance
        self.layers = layers

    def get(self, path, default=None):
        """Read a dotted key path out of the merged values."""
        node = self.values
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def sources(self, path):
        """Layer labels that contributed to a key path, base first."""
        return self.provenance.get(path, [])


def strategy_for(path):
    """The strategy governing a dotted key path."""
    best = DEFAULT_STRATEGY
    best_len = -1
    for prefix, strategy in STRATEGIES.items():
        if path == prefix or path.startswith(prefix + "."):
            if len(prefix) > best_len:
                best, best_len = strategy, len(prefix)
    return best


def read_layer(path):
    """Load and validate one config file.

    The path is normalized here rather than at each call site, so that every
    provenance label has the same form no matter which layer it came from.
    """
    path = pathlib.Path(path).resolve()
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError("cannot read %s: %s" % (path, exc)) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            "%s is not valid JSON: line %d column %d: %s"
            % (path, exc.lineno, exc.colno, exc.msg)) from exc
    if not isinstance(data, dict):
        raise ConfigError("%s must contain a JSON object" % path)

    schema = data.get("schema")
    if schema is None:
        raise ConfigError('%s is missing "schema": %d' % (path, SCHEMA_VERSION))
    if schema != SCHEMA_VERSION:
        raise ConfigError(
            "%s declares schema %r; this version understands %d"
            % (path, schema, SCHEMA_VERSION))
    return Layer(path, data)


def discover(start=None, machine_path=None):
    """Collect layers, base first, nearest last.

    The machine layer is always the base when it exists. Directories are then
    walked from the filesystem root down to ``start``, so that the returned
    order is already merge order.
    """
    start = pathlib.Path(start or pathlib.Path.cwd()).resolve()
    machine_path = pathlib.Path(
        MACHINE_CONFIG if machine_path is None else machine_path)

    layers = []
    if machine_path.is_file():
        layers.append(read_layer(machine_path))

    # Walk up collecting candidates, stopping at the first layer that declares
    # itself the root; reverse to get base-to-nearest order.
    found = []
    for directory in [start, *start.parents]:
        candidate = directory / CONFIG_DIRNAME / CONFIG_FILENAME
        if candidate.is_file():
            layer = read_layer(candidate)
            found.append(layer)
            if layer.is_root:
                break
    layers.extend(reversed(found))
    return layers


def merge(layers):
    """Fold layers base-to-nearest into effective values plus provenance."""
    values = {}
    provenance = {}
    for layer in layers:
        _merge_into(values, layer.values(), "", provenance, layer.label)
    return Resolved(values, provenance, list(layers))


def _merge_into(acc, incoming, prefix, provenance, label):
    for key, value in incoming.items():
        path = key if not prefix else prefix + "." + key
        strategy = strategy_for(path)

        if strategy == DEEP_MERGE and isinstance(value, dict):
            existing = acc.get(key)
            if not isinstance(existing, dict):
                existing = {}
                acc[key] = existing
            _merge_into(existing, value, path, provenance, label)
            continue

        if strategy == UNION_APPEND:
            acc[key] = _union(acc.get(key), value, path)
        elif strategy == MONOTONIC_TIGHTEN:
            acc[key] = _tighten(acc.get(key), value, path)
        else:
            acc[key] = value

        provenance.setdefault(path, []).append(label)


def _union(existing, value, path):
    """Accumulate across layers, nearest last, preserving first appearance."""
    if not isinstance(value, list):
        raise ConfigError(
            "%s uses union-append and must be a list, got %s"
            % (path, type(value).__name__))
    merged = list(existing or [])
    for item in value:
        if item not in merged:
            merged.append(item)
    return merged


def _tighten(existing, value, path):
    """A nearer layer may narrow, never widen.

    Defined and tested but not yet applied to any key: it is the seam a data
    protection layer would attach to (D1), where a nearer layer must never be
    able to loosen a restriction set further out.
    """
    if existing is None:
        return value
    if isinstance(existing, list) and isinstance(value, list):
        widened = [item for item in value if item not in existing]
        if widened:
            raise ConfigError(
                "%s may only narrow; these values are not permitted by an "
                "outer layer: %s" % (path, ", ".join(map(repr, widened))))
        return value
    if isinstance(existing, bool) and isinstance(value, bool):
        if value is False and existing is True:
            raise ConfigError("%s may only narrow; it is already enabled" % path)
        return value
    raise ConfigError(
        "%s uses monotonic-tighten, which is not defined for %s"
        % (path, type(value).__name__))


def resolve(start=None, machine_path=None):
    """Discover and merge in one call. The normal entry point."""
    return merge(discover(start=start, machine_path=machine_path))
