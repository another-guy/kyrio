"""The adapter contract, and the one adapter that ships.

Two kinds of test here. The contract tests apply to every adapter and are
written so that adding one without meeting the contract fails immediately
rather than at the point somebody tries to use it. The rest cover this
provider's own probes.

No test here runs the real binary. It is not installed on every machine, it is
not signed in on any CI runner, and a test whose result depends on either
would report the runner's state rather than the adapter's correctness.
"""

import pathlib
import unittest

import _path  # noqa: F401  -- import side effect: puts scripts/ on sys.path

from kyrio import capability, config, probe, providers
from kyrio.providers import github


def runner(**outcomes):
    """A stand-in for ``probe.run`` keyed by the first two argument words."""
    def run(argv):
        key = " ".join(argv[:2])
        outcome, detail = outcomes.get(key, (probe.MISSING, "not found"))
        return probe.Ran(outcome, argv, detail=detail)
    return run


ANSWERS = (probe.ANSWERED, "")
REFUSES = (probe.FAILED, "exit 1")
ABSENT = (probe.MISSING, "not found")


class TestContract(unittest.TestCase):
    """Applies to every adapter, including ones not written yet."""

    def setUp(self):
        self.adapters = list(providers.ADAPTERS.values())
        if not self.adapters:
            self.skipTest("no adapters ship yet")

    def test_each_declares_the_whole_contract(self):
        for adapter in self.adapters:
            for field in ("ID", "CAPABILITIES", "TRANSPORT", "BINARY"):
                with self.subTest(adapter=adapter.ID, field=field):
                    self.assertTrue(getattr(adapter, field, None))
            for probe_name in ("health", "auth"):
                with self.subTest(adapter=adapter.ID, probe=probe_name):
                    self.assertTrue(callable(getattr(adapter, probe_name, None)))

    def test_the_registry_key_is_the_declared_id(self):
        """Resolution looks an adapter up by the id in a config file. A key
        that disagrees with the module is an adapter nothing can reach."""
        for key, adapter in providers.ADAPTERS.items():
            with self.subTest(adapter=key):
                self.assertEqual(key, adapter.ID)

    def test_the_id_is_already_canonical(self):
        """Setup canonicalizes whatever a person types. An adapter registered
        under a form that canonicalization never produces is unreachable."""
        for adapter in self.adapters:
            with self.subTest(adapter=adapter.ID):
                self.assertEqual(capability.normalize_provider(adapter.ID),
                                 adapter.ID)

    def test_it_serves_capabilities_the_broker_has(self):
        for adapter in self.adapters:
            for name in adapter.CAPABILITIES:
                with self.subTest(adapter=adapter.ID, capability=name):
                    self.assertIn(name, config.CAPABILITIES)
                    self.assertNotIn(name, config.INTRINSIC)

    def test_its_transport_is_one_served_by_an_adapter(self):
        for adapter in self.adapters:
            with self.subTest(adapter=adapter.ID):
                self.assertIn(adapter.TRANSPORT,
                              capability.ADAPTER_TRANSPORTS)

    def test_both_remedies_are_printed_rather_than_run(self):
        """Setup never installs and never starts a sign-in. These are strings
        for a person, and nothing in the tree executes them."""
        package = pathlib.Path(providers.__file__).parent
        for source in sorted(package.glob("*.py")):
            text = source.read_text(encoding="utf-8")
            with self.subTest(source=source.name):
                self.assertNotIn("subprocess", text)

    def test_a_configured_machine_reaches_the_adapter(self):
        """The contract's whole point: an id in a config file resolves."""
        for adapter in self.adapters:
            entry = {"transport": adapter.TRANSPORT, "provider": adapter.ID}
            merged = config.merge([config.Layer(
                pathlib.PurePosixPath("/layer"),
                {"capabilities": {adapter.CAPABILITIES[0]: entry}})])
            r = capability.resolve(adapter.CAPABILITIES[0], merged)
            with self.subTest(adapter=adapter.ID):
                self.assertIs(r.adapter, adapter)
                self.assertTrue(r.usable)


class TestGitHubProbes(unittest.TestCase):
    def test_health_asks_the_binary_to_answer(self):
        ran = github.health(runner(**{"gh --version": ANSWERS}))
        self.assertTrue(ran.answered)
        self.assertEqual(ran.argv[0], "gh")

    def test_the_two_probes_are_different_commands(self):
        self.assertNotEqual(github.HEALTH, github.AUTH)

    def test_authentication_is_decided_by_the_exit_code(self):
        """Not by reading the output. The exit code is a contract the tool
        keeps across releases; the wording beside it is free to change, and an
        adapter that reads the words breaks on a release note."""
        signed_in = github.auth(runner(**{"gh auth": ANSWERS}))
        signed_out = github.auth(runner(**{"gh auth": REFUSES}))
        self.assertTrue(signed_in.answered)
        self.assertFalse(signed_out.answered)

    def test_no_host_is_named_anywhere(self):
        """Which host a machine talks to is a fact about that machine. An
        enterprise installation answers under its own hostname (I2)."""
        source = pathlib.Path(github.__file__).read_text(encoding="utf-8")
        self.assertNotIn("--hostname", source)
        for argv in (github.HEALTH, github.AUTH):
            self.assertNotIn("github.com", argv)


class TestProbeTool(unittest.TestCase):
    def probe(self, **outcomes):
        return probe.probe_tool(github, runner=runner(**outcomes))

    def test_nothing_installed(self):
        tool = self.probe()
        self.assertEqual(tool.state, probe.NOT_INSTALLED)
        self.assertFalse(tool.usable)
        self.assertIn("install", tool.remedy.lower())

    def test_installed_but_not_signed_in(self):
        """The ambiguous case, and the reason there are two probes."""
        tool = self.probe(**{"gh --version": ANSWERS, "gh auth": REFUSES})
        self.assertEqual(tool.state, probe.UNAUTHENTICATED)
        self.assertFalse(tool.usable)
        self.assertEqual(tool.remedy, github.LOGIN)

    def test_installed_and_signed_in(self):
        tool = self.probe(**{"gh --version": ANSWERS, "gh auth": ANSWERS})
        self.assertEqual(tool.state, probe.AUTHENTICATED)
        self.assertTrue(tool.usable)
        self.assertEqual(tool.remedy, "")

    def test_present_but_not_answering(self):
        tool = self.probe(**{"gh --version": REFUSES})
        self.assertEqual(tool.state, probe.BROKEN)
        self.assertFalse(tool.usable)

    def test_the_sign_in_probe_is_skipped_when_nothing_is_installed(self):
        """Asking a binary that is not there whether it is signed in wastes a
        process launch and reports the wrong failure."""
        seen = []

        def watching(argv):
            seen.append(" ".join(argv[:2]))
            return probe.Ran(probe.MISSING, argv, detail="not found")

        probe.probe_tool(github, runner=watching)
        self.assertEqual(seen, ["gh --version"])

    def test_presence_alone_is_never_usable(self):
        """A binary can be installed as something else's dependency, bundled
        by policy, or left from a trial. Somebody signing in is the cheapest
        proof this machine is meant to reach it."""
        tool = self.probe(**{"gh --version": ANSWERS, "gh auth": REFUSES})
        self.assertFalse(tool.usable)


class TestProbeTools(unittest.TestCase):
    def test_every_shipped_adapter_is_reported(self):
        tools = probe.probe_tools(runner=runner())
        self.assertEqual([t.id for t in tools], providers.known())

    def test_an_empty_registry_reports_nothing_rather_than_failing(self):
        class Empty:
            ADAPTERS = {}

        self.assertEqual(probe.probe_tools(registry=Empty(),
                                           runner=runner()), [])


class TestForCapability(unittest.TestCase):
    def test_adapters_are_found_by_the_capability_they_serve(self):
        self.assertIn(github, providers.for_capability("scm"))

    def test_a_capability_nothing_serves_is_an_empty_list(self):
        self.assertEqual(providers.for_capability("obs"), [])


if __name__ == "__main__":
    unittest.main()
