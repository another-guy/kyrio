"""Transport resolution: what configuration says, and what it takes to act.

The interesting cases here are all failures. A capability that resolves is one
line; a capability that does not has to say which of six different things went
wrong, in a message someone can act on without opening four config files.

The registry is injected throughout. Resolution must be testable without an
adapter shipping, and no test here may start passing merely because one does.
"""

import pathlib
import unittest

import _path  # noqa: F401  -- import side effect: puts scripts/ on sys.path

from kyrio import capability, config, providers


class Registry:
    """A stand-in for ``providers``, holding whatever a test needs."""

    def __init__(self, **adapters):
        self.adapters = adapters

    def get(self, provider):
        return self.adapters.get(provider)


EMPTY = Registry()


def resolved(*layers):
    """Merge hand-built layers, base first, without touching the filesystem."""
    return config.merge([
        config.Layer(pathlib.PurePosixPath("/layer%d" % i), data)
        for i, data in enumerate(layers)])


def caps(**entries):
    return {"capabilities": entries}


def one(name, entry, registry=EMPTY):
    return capability.resolve(name, resolved(caps(**{name: entry})),
                              registry=registry)


class TestIntrinsic(unittest.TestCase):
    def test_repo_needs_no_configuration_and_no_adapter(self):
        r = capability.resolve("repo", resolved(), registry=EMPTY)
        self.assertEqual(r.status, capability.READY)
        self.assertEqual(r.transport, capability.LOCAL)
        self.assertTrue(r.usable)

    def test_configuration_cannot_break_it(self):
        """``repo`` reads the working tree. Nothing declared can take it away."""
        r = one("repo", {"transport": "unavailable"})
        self.assertEqual(r.status, capability.READY)
        self.assertTrue(r.usable)


class TestUnconfigured(unittest.TestCase):
    def test_a_capability_nobody_declared(self):
        r = capability.resolve("scm", resolved(), registry=EMPTY)
        self.assertEqual(r.status, capability.UNCONFIGURED)
        self.assertIsNone(r.transport)
        self.assertFalse(r.usable)

    def test_an_entry_with_no_transport(self):
        r = one("scm", {"provider": "provider-a"})
        self.assertEqual(r.status, capability.UNCONFIGURED)

    def test_the_remediation_names_the_command_that_fixes_it(self):
        r = capability.resolve("scm", resolved(), registry=EMPTY)
        self.assertIn("/kyrio:setup", r.remediation)

    def test_nothing_falls_through_to_manual(self):
        """Manual means a person is the transport; it is opted into, never
        arrived at. A pack that quietly degrades into asking someone to paste
        things is worse than one that says it is not configured here."""
        for entry in (None, {}, {"transport": ""}, {"transport": "cli"}):
            with self.subTest(entry=entry):
                r = (capability.resolve("scm", resolved(), registry=EMPTY)
                     if entry is None else one("scm", entry))
                self.assertNotEqual(r.transport, capability.MANUAL)


class TestTurnedOff(unittest.TestCase):
    def test_unavailable_is_the_explicit_way_to_switch_something_off(self):
        r = one("kb", {"transport": "unavailable"})
        self.assertEqual(r.status, capability.UNAVAILABLE)
        self.assertFalse(r.usable)

    def test_the_fix_offered_is_not_undoing_the_decision(self):
        """Someone set this. Sending them to setup would undo a decision
        rather than repair a fault."""
        r = one("kb", {"transport": "unavailable"})
        self.assertNotIn("/kyrio:setup", r.remediation)

    def test_the_remediation_names_the_layer_that_did_it(self):
        """Told only that a capability is off, a person has four files to
        search. Provenance is why the cascade records it."""
        r = one("kb", {"transport": "unavailable"})
        self.assertIn("layer0", r.remediation)

    def test_a_nearer_layer_can_switch_something_off(self):
        merged = resolved(caps(scm={"transport": "cli", "provider": "a"}),
                          caps(scm={"transport": "unavailable"}))
        r = capability.resolve("scm", merged, registry=EMPTY)
        self.assertEqual(r.status, capability.UNAVAILABLE)
        self.assertIn("layer1", r.remediation)


class TestMalformed(unittest.TestCase):
    def test_an_unknown_transport_names_the_known_ones(self):
        r = one("scm", {"transport": "carrier-pigeon"})
        self.assertEqual(r.status, capability.UNAVAILABLE)
        for known in capability.KNOWN_TRANSPORTS:
            self.assertIn(known, r.remediation)

    def test_local_belongs_only_to_an_intrinsic_capability(self):
        r = one("scm", {"transport": "local"})
        self.assertEqual(r.status, capability.UNAVAILABLE)
        self.assertIn("repo", r.remediation)

    def test_a_bare_string_is_shorthand_for_the_transport(self):
        self.assertEqual(one("kb", "unavailable").status,
                         capability.UNAVAILABLE)


class TestServer(unittest.TestCase):
    def test_a_tool_prefix_is_what_makes_it_callable(self):
        r = one("issue", {"transport": "server", "tool_prefix": "toolns"})
        self.assertEqual(r.status, capability.CONFIGURED)
        self.assertEqual(r.tool_prefix, "toolns")
        self.assertTrue(r.usable)

    def test_without_one_no_tool_can_be_named(self):
        r = one("issue", {"transport": "server"})
        self.assertEqual(r.status, capability.UNAVAILABLE)
        self.assertIn("tool_prefix", r.remediation)

    def test_it_needs_no_adapter(self):
        """The caller makes the call; Python cannot invoke a Claude Code tool."""
        r = one("issue", {"transport": "server", "tool_prefix": "toolns"})
        self.assertIsNone(r.adapter)
        self.assertTrue(r.usable)


class TestManual(unittest.TestCase):
    def test_opting_in_is_configured_and_usable(self):
        r = one("kb", {"transport": "manual"})
        self.assertEqual(r.status, capability.CONFIGURED)
        self.assertEqual(r.transport, capability.MANUAL)
        self.assertTrue(r.usable)


class TestAdapterTransports(unittest.TestCase):
    def test_a_provider_is_required(self):
        r = one("scm", {"transport": "cli"})
        self.assertEqual(r.status, capability.UNAVAILABLE)
        self.assertIn("provider", r.remediation)

    def test_a_shipped_adapter_makes_it_usable(self):
        r = one("scm", {"transport": "cli", "provider": "provider-a"},
                registry=Registry(**{"provider-a": object()}))
        self.assertEqual(r.status, capability.CONFIGURED)
        self.assertEqual(r.provider, "provider-a")
        self.assertTrue(r.usable)

    def test_configured_for_a_provider_nothing_ships_for(self):
        """Configured, and not usable. Those are different problems with
        different owners: the configuration is right and the pack is short."""
        r = one("scm", {"transport": "cli", "provider": "provider-a"})
        self.assertEqual(r.status, capability.CONFIGURED)
        self.assertFalse(r.usable)
        self.assertIn("provider-a", r.remediation)
        self.assertNotIn("/kyrio:setup", r.remediation)

    def test_an_ordered_list_falls_through_to_the_next(self):
        """How a machine mid-migration between products is expressed. No skill
        above ever learns that a migration is underway."""
        r = one("obs", {"transport": "cli",
                        "provider": ["provider-a", "provider-b"]},
                registry=Registry(**{"provider-b": object()}))
        self.assertEqual(r.provider, "provider-b")
        self.assertTrue(r.usable)

    def test_the_first_that_ships_wins(self):
        r = one("obs", {"transport": "cli",
                        "provider": ["provider-a", "provider-b"]},
                registry=Registry(**{"provider-a": object(),
                                     "provider-b": object()}))
        self.assertEqual(r.provider, "provider-a")

    def test_everything_tried_is_recorded(self):
        r = one("obs", {"transport": "cli",
                        "provider": ["provider-a", "provider-b"]})
        self.assertEqual(r.tried, ("provider-a", "provider-b"))
        self.assertIn("provider-a, provider-b", r.remediation)

    def test_the_browser_transport_resolves_the_same_way(self):
        r = one("kb", {"transport": "browser", "provider": "provider-a"},
                registry=Registry(**{"provider-a": object()}))
        self.assertTrue(r.usable)


class TestRemediationText(unittest.TestCase):
    def test_no_remediation_names_its_own_capability(self):
        """Every caller already has the name. Repeating it inside the text
        stops two capabilities with the same gap collapsing into one line."""
        entries = [
            None, {}, {"transport": ""}, {"transport": "unavailable"},
            {"transport": "carrier-pigeon"}, {"transport": "local"},
            {"transport": "server"}, {"transport": "cli"},
            {"transport": "cli", "provider": "provider-a"},
        ]
        for entry in entries:
            r = (capability.resolve("scm", resolved(), registry=EMPTY)
                 if entry is None else one("scm", entry))
            with self.subTest(entry=entry):
                if r.remediation:
                    self.assertNotIn("scm", r.remediation)

    def test_the_same_gap_reads_the_same_for_every_capability(self):
        self.assertEqual(
            capability.resolve("scm", resolved(), registry=EMPTY).remediation,
            capability.resolve("obs", resolved(), registry=EMPTY).remediation)


class TestReportShape(unittest.TestCase):
    def test_every_capability_appears_in_declared_order(self):
        rows = capability.rows(resolved(), registry=EMPTY)
        self.assertEqual([r[0] for r in rows], list(config.CAPABILITIES))

    def test_every_status_is_from_the_shared_vocabulary(self):
        """``caps`` and ``probe report`` both render these rows. A status
        invented in one place is a machine described two ways."""
        merged = resolved(caps(scm={"transport": "cli"},
                               issue={"transport": "server",
                                      "tool_prefix": "toolns"},
                               kb={"transport": "unavailable"}))
        for _, _, status in capability.rows(merged, registry=EMPTY):
            self.assertIn(status, capability.STATUSES)


class TestTransportOrder(unittest.TestCase):
    def test_a_connected_server_is_preferred_and_manual_is_last(self):
        self.assertEqual(capability.TRANSPORT_ORDER[0], capability.SERVER)
        self.assertEqual(capability.TRANSPORT_ORDER[-1], capability.MANUAL)

    def test_local_and_unavailable_are_outside_the_order(self):
        """Neither is something a probe can fall through to: one belongs to a
        capability that needs no transport, the other is a decision."""
        self.assertNotIn(capability.LOCAL, capability.TRANSPORT_ORDER)
        self.assertNotIn(capability.UNAVAILABLE_TRANSPORT,
                         capability.TRANSPORT_ORDER)


class TestRegistry(unittest.TestCase):
    def test_the_shipped_registry_holds_only_what_has_a_caller(self):
        """An adapter written before the workflow that calls it encodes a guess
        about a shape nobody has run against."""
        self.assertEqual(providers.known(), [])

    def test_an_unknown_provider_is_none_rather_than_an_error(self):
        self.assertIsNone(providers.get("provider-a"))

    def test_an_empty_registry_is_not_a_broken_machine(self):
        """Resolution still reports, and still says the useful thing."""
        r = one("scm", {"transport": "cli", "provider": "provider-a"},
                registry=providers)
        self.assertEqual(r.status, capability.CONFIGURED)
        self.assertFalse(r.usable)


if __name__ == "__main__":
    unittest.main()
