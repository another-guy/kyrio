"""The adapter contract, and the adapters that ship.

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
from kyrio.providers import azure_devops, github

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


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


class TestAzureDevOps(unittest.TestCase):
    """The second adapter. Its value is not that it works -- nobody here can
    run it -- but that meeting the contract required no change to anything
    above ``providers/``."""

    def setUp(self):
        self.shown = (FIXTURES / "azure_devops_pr_show.json").read_text(
            encoding="utf-8")
        self.listing = (FIXTURES / "azure_devops_pr_list.json").read_text(
            encoding="utf-8")

    # -- identity ----------------------------------------------------------

    def test_it_is_registered_under_its_own_id(self):
        self.assertIs(providers.get(azure_devops.ID), azure_devops)

    def test_it_serves_the_capability_it_declares(self):
        self.assertIn(azure_devops, providers.for_capability("scm"))

    # -- identifiers -------------------------------------------------------

    def test_an_id_written_the_way_people_paste_it(self):
        self.assertEqual(azure_devops.pr_identifier(" #4821 "), "4821")

    def test_a_branch_name_is_not_an_identifier(self):
        for text in ("main", "", "0", "-1", "12x"):
            with self.subTest(given=text):
                with self.assertRaises(ValueError):
                    azure_devops.pr_identifier(text)

    # -- the diff, in two steps -------------------------------------------

    def test_the_diff_asks_this_host_first_then_git(self):
        """The interesting property of this adapter: its host has no verb
        that prints a patch, so the diff is assembled rather than fetched."""
        calls = []

        def run(argv, cwd=None, **kw):
            calls.append(list(argv))
            if argv[0] == azure_devops.BINARY:
                return probe.Ran(probe.ANSWERED, argv, self.shown)
            return probe.Ran(probe.ANSWERED, argv, "diff --git a/x b/x\n")

        ran = azure_devops.pr_diff(run, "4821", cwd="/somewhere")

        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][:4],
                         [azure_devops.BINARY, "repos", "pr", "show"])
        self.assertEqual(calls[1][:2], [azure_devops.GIT, "diff"])
        self.assertIn("diff --git", ran.output)

    def test_the_range_runs_from_target_to_source(self):
        """What the change adds to its target, not everything that has
        happened on the target since the branch left it."""
        calls = []

        def run(argv, cwd=None, **kw):
            calls.append(list(argv))
            return probe.Ran(probe.ANSWERED, argv,
                             self.shown if argv[0] == azure_devops.BINARY
                             else "diff --git a/x b/x\n")

        azure_devops.pr_diff(run, "4821")
        target, source = azure_devops.parse_ends(self.shown)
        self.assertEqual(calls[1][2], "%s...%s" % (target, source))

    def test_a_host_that_refuses_stops_before_git_runs(self):
        """Otherwise the second call reports on commits nobody resolved, and
        the message names the wrong program."""
        calls = []

        def run(argv, cwd=None, **kw):
            calls.append(list(argv))
            return probe.Ran(probe.FAILED, argv, "not found", "exit 1")

        ran = azure_devops.pr_diff(run, "4821")
        self.assertEqual(len(calls), 1)
        self.assertFalse(ran.answered)

    def test_an_answer_missing_its_commits_is_refused(self):
        with self.assertRaises(ValueError):
            azure_devops.parse_ends('{"pullRequestId": 4821}')

    def test_an_answer_that_is_not_json_is_refused(self):
        with self.assertRaises(ValueError):
            azure_devops.parse_ends("<html>signed out</html>")

    # -- the listing -------------------------------------------------------

    def test_the_listing_asks_for_completed_changes(self):
        calls = []
        azure_devops.log(lambda argv, cwd=None, **kw: calls.append(argv)
                         or probe.Ran(probe.ANSWERED, argv, self.listing),
                         "2026-08-01")
        self.assertEqual(calls[0][:4],
                         [azure_devops.BINARY, "repos", "pr", "list"])
        self.assertIn(azure_devops.LIST_STATUS, calls[0])

    def test_the_listing_parses_into_the_shared_shape(self):
        records = azure_devops.parse_log(self.listing)
        self.assertEqual(len(records), 3)
        self.assertEqual(records[0]["id"], "4821")
        self.assertEqual(records[0]["at"], "2026-08-28")
        self.assertEqual(records[0]["author"], "Sample Reviewer")

    def test_an_address_beside_a_name_is_not_taken(self):
        """A listing says who merged something. It does not need to carry a
        mail address into a payload this pack prints and stores."""
        self.assertNotIn("@", azure_devops.parse_log(self.listing)[0]["author"])

    def test_a_change_with_no_author_is_left_empty(self):
        """An automated completion genuinely has none, and a placeholder
        would read as a person nobody could find."""
        self.assertEqual(azure_devops.parse_log(self.listing)[2]["author"], "")

    # -- what it must never do --------------------------------------------

    def test_it_never_names_where_it_points(self):
        """The whole of I1 and I2 for this file: the tool reads that from the
        repository it runs in, or from what a person configured here."""
        text = pathlib.Path(azure_devops.__file__).read_text(encoding="utf-8")
        # The flag naming a grouping above the machine is not listed here on
        # purpose: it cannot be written anywhere in this tree, so RULE 1 of the
        # portability lint already forbids it, and spelling it to assert its
        # absence would be the violation.
        # Not listed: the flag asking the tool to work the location out from
        # the repository's own remote. That is the behaviour keeping this file
        # free of any destination, so forbidding it would forbid the fix.
        for forbidden in ("--project", "--subscription",
                          "dev.azure.com", "visualstudio.com"):
            with self.subTest(flag=forbidden):
                self.assertNotIn(forbidden, text)

    def test_it_writes_nothing_and_fetches_nothing(self):
        """Assembling a diff locally must not mutate the repository it reads
        (I7)."""
        text = pathlib.Path(azure_devops.__file__).read_text(encoding="utf-8")
        for verb in ('"fetch"', '"pull"', '"checkout"', '"clone"'):
            with self.subTest(verb=verb):
                self.assertNotIn(verb, text)
