import ast
import json
import pathlib
import shutil
import sys
import tempfile
import unittest

import _path  # noqa: F401  -- import side effect: puts scripts/ on sys.path

from kyrio import config, probe


class Sandbox(unittest.TestCase):
    """Every file this touches lives in a temporary directory, never in ~."""

    def setUp(self):
        self.root = pathlib.Path(
            tempfile.mkdtemp(prefix="kyrio-probe-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.machine = self.root / "kyrio" / "config.json"
        self.mirror = self.root / "kyrio" / "state" / "interpreter"
        self.settings = self.root / "settings.json"

        for name, value in (("INTERPRETER_FILE", self.mirror),
                            ("SETTINGS_FILE", self.settings)):
            original = getattr(probe, name)
            setattr(probe, name, value)
            self.addCleanup(setattr, probe, name, original)

        original_machine = config.MACHINE_CONFIG
        config.MACHINE_CONFIG = self.machine
        self.addCleanup(setattr, config, "MACHINE_CONFIG", original_machine)

    def read_machine(self):
        return json.loads(self.machine.read_text(encoding="utf-8"))

    def read_settings(self):
        return json.loads(self.settings.read_text(encoding="utf-8"))


class TestInterpreter(unittest.TestCase):
    def test_the_running_interpreter_is_the_honest_answer(self):
        """Whatever the shims resolved is already running this process."""
        found = probe.running_interpreter()
        self.assertEqual(found.executable, sys.executable)
        self.assertTrue(found.usable)

    def test_choose_prefers_the_one_already_running(self):
        self.assertEqual(probe.choose_interpreter().executable, sys.executable)

    def test_a_candidate_is_probed_by_running_it(self):
        found = probe.probe_candidates([[sys.executable]])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].version[:2], sys.version_info[:2])

    def test_a_name_that_does_not_run_is_not_reported(self):
        self.assertEqual(probe.probe_candidates([["kyrio-absent-interpreter"]]),
                         [])

    def test_something_that_runs_but_is_not_python_is_not_reported(self):
        """Resolving on PATH proves nothing; the output has to make sense."""
        self.assertEqual(probe.probe_candidates([["git"]]), [])

    def test_version_comparison_is_on_the_minimum(self):
        old = probe.Interpreter("/usr/bin/python3", (3, 9, 0), "test")
        new = probe.Interpreter("/usr/bin/python3", (3, 12, 0), "test")
        self.assertFalse(old.usable)
        self.assertTrue(new.usable)


class TestRecord(Sandbox):
    def test_writes_both_files(self):
        result = probe.record(machine_path=self.machine,
                              interpreter_file=self.mirror)
        self.assertTrue(self.machine.is_file())
        self.assertTrue(self.mirror.is_file())
        self.assertEqual(self.read_machine()["interpreter"], sys.executable)
        self.assertEqual(self.mirror.read_text(encoding="utf-8").strip(),
                         sys.executable)
        self.assertEqual(result.meta["interpreter"], sys.executable)

    def test_the_mirror_agrees_with_the_config(self):
        """A mirror that disagrees is worse than no mirror at all."""
        probe.record(machine_path=self.machine, interpreter_file=self.mirror)
        self.assertEqual(self.read_machine()["interpreter"],
                         self.mirror.read_text(encoding="utf-8").strip())

    def test_the_machine_layer_is_readable_by_the_resolver(self):
        probe.record(machine_path=self.machine, interpreter_file=self.mirror)
        resolved = config.resolve(start=self.root, machine_path=self.machine)
        self.assertEqual(resolved.get("interpreter"), sys.executable)
        self.assertEqual(resolved.get("capabilities.repo.transport"), "local")

    def test_hand_written_keys_survive(self):
        self.machine.parent.mkdir(parents=True, exist_ok=True)
        self.machine.write_text(json.dumps({
            "schema": 1,
            "shell": "hand written",
            "conventions": {"test": "make check"},
            "capabilities": {"scm": {"transport": "cli"}},
        }), encoding="utf-8")
        result = probe.record(machine_path=self.machine,
                              interpreter_file=self.mirror)
        data = self.read_machine()
        self.assertEqual(data["shell"], "hand written")
        self.assertEqual(data["conventions"], {"test": "make check"})
        self.assertEqual(data["capabilities"]["scm"], {"transport": "cli"})
        self.assertEqual(data["capabilities"]["repo"], {"transport": "local"})
        self.assertEqual(result.meta["kept"], 2)

    def test_running_twice_changes_nothing(self):
        probe.record(machine_path=self.machine, interpreter_file=self.mirror)
        first = self.machine.read_text(encoding="utf-8")
        probe.record(machine_path=self.machine, interpreter_file=self.mirror)
        self.assertEqual(self.machine.read_text(encoding="utf-8"), first)


class TestReport(Sandbox):
    def test_says_what_has_not_been_written(self):
        result = probe.report(self.root, machine_path=self.machine)
        self.assertFalse(result.meta["recorded"])
        self.assertIn("not written yet", result.payload)
        self.assertIn("INSTALLS   nothing, ever.", result.payload)

    def test_writes_nothing(self):
        probe.report(self.root, machine_path=self.machine)
        self.assertFalse(self.machine.exists())
        self.assertFalse(self.mirror.exists())
        self.assertFalse(self.settings.exists())

    def test_reflects_what_record_wrote(self):
        probe.record(machine_path=self.machine, interpreter_file=self.mirror)
        result = probe.report(self.root, machine_path=self.machine)
        self.assertTrue(result.meta["recorded"])
        self.assertIn(sys.executable, result.payload)

    def test_every_capability_is_listed(self):
        result = probe.report(self.root, machine_path=self.machine)
        for name in config.CAPABILITIES:
            with self.subTest(capability=name):
                self.assertIn(name, result.payload)


FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
LISTING = (FIXTURES / "mcp_list.txt").read_text(encoding="utf-8")


def answering(output):
    """A runner that answers with a fixed listing."""
    return lambda argv: probe.Ran(probe.ANSWERED, argv, output)


def failing(outcome, detail):
    return lambda argv: probe.Ran(outcome, argv, detail=detail)


class TestRun(unittest.TestCase):
    """The execution primitive: by execution, never by presence."""

    def test_a_program_that_answers(self):
        ran = probe.run([sys.executable, "-c", "print('here')"])
        self.assertEqual(ran.outcome, probe.ANSWERED)
        self.assertTrue(ran.answered)
        self.assertIn("here", ran.output)

    def test_a_name_that_resolves_to_nothing(self):
        """A name on PATH proves nothing, so absence has to be a real answer
        rather than an exception the caller has to catch."""
        ran = probe.run(["kyrio-no-such-program-anywhere"])
        self.assertEqual(ran.outcome, probe.MISSING)
        self.assertFalse(ran.answered)

    def test_a_program_that_runs_and_refuses(self):
        """Installed and logged in are different failures with different
        fixes, and this is the shape the second one arrives in."""
        ran = probe.run([sys.executable, "-c", "raise SystemExit(3)"])
        self.assertEqual(ran.outcome, probe.FAILED)
        self.assertIn("3", ran.detail)

    def test_what_it_said_on_the_error_stream_is_kept(self):
        ran = probe.run(
            [sys.executable, "-c", "import sys; sys.stderr.write('log in')"])
        self.assertIn("log in", ran.output)

    def test_a_program_that_never_answers(self):
        ran = probe.run([sys.executable, "-c", "import time; time.sleep(30)"],
                        timeout=1)
        self.assertEqual(ran.outcome, probe.TIMED_OUT)

    def test_no_subprocess_call_anywhere_asks_for_a_shell(self):
        """I5 reaches down to every binary this pack runs.

        Read as syntax rather than as text: a docstring saying "never
        shell=True" would satisfy a substring search, and an adapter that
        actually passed it would be indistinguishable from one that did not.
        Scoped to the whole package on purpose -- ``providers/`` is where the
        next binary gets run, and it should arrive under this rule already.
        """
        package = pathlib.Path(probe.__file__).parent
        offenders = []
        for source in sorted(package.rglob("*.py")):
            tree = ast.parse(source.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg == "shell":
                        offenders.append("%s:%d" % (source.name, node.lineno))
        self.assertEqual(offenders, [])

    def test_something_was_actually_read(self):
        """The guard above passes trivially if the glob finds nothing."""
        package = pathlib.Path(probe.__file__).parent
        self.assertGreater(len(list(package.rglob("*.py"))), 5)


class TestServerState(unittest.TestCase):
    def test_the_words_decide_and_not_the_mark(self):
        """The marks beside each status are non-ASCII and do not survive a
        console codepage that cannot encode them. The words do."""
        self.assertEqual(probe.server_state("Connected"), probe.CONNECTED)
        self.assertEqual(probe.server_state("connected"), probe.CONNECTED)
        self.assertEqual(probe.server_state("Needs authentication"),
                         probe.NEEDS_AUTH)

    def test_an_unfamiliar_status_is_not_rounded_to_a_familiar_one(self):
        """This reads another program's output, which may grow a state this
        version has never seen. Guessing is how a report becomes confidently
        wrong."""
        self.assertEqual(probe.server_state("Rehydrating"), probe.UNKNOWN)

    def test_needs_auth_is_not_read_as_connected(self):
        """Both phrases can appear on one line; the more specific wins."""
        self.assertEqual(
            probe.server_state("connected but needs authentication"),
            probe.NEEDS_AUTH)


class TestParseServers(unittest.TestCase):
    def setUp(self):
        self.servers = probe.parse_servers(LISTING)
        self.by_name = {s.name: s for s in self.servers}

    def test_every_entry_is_read(self):
        self.assertEqual(len(self.servers), 6)

    def test_the_header_and_the_footnote_are_not_servers(self):
        for name in self.by_name:
            self.assertNotIn("Checking", name)
            self.assertNotIn("Note", name)

    def test_each_state_is_classified(self):
        self.assertEqual(self.by_name["example-notes"].state, probe.CONNECTED)
        self.assertEqual(self.by_name["example-tickets"].state,
                         probe.NEEDS_AUTH)
        self.assertEqual(self.by_name["example-pipelines"].state,
                         probe.PENDING)
        self.assertEqual(self.by_name["example-metrics"].state,
                         probe.UNREACHABLE)

    def test_an_unknown_state_keeps_the_words_it_was_given(self):
        server = self.by_name["example-future"]
        self.assertEqual(server.state, probe.UNKNOWN)
        self.assertIn("Rehydrating", server.reported)

    def test_an_address_containing_a_spaced_hyphen_survives(self):
        """The status is split from the right precisely for this."""
        server = self.by_name["example-legacy"]
        self.assertEqual(server.state, probe.CONNECTED)
        self.assertIn("old - new", server.address)

    def test_only_connected_counts_as_usable(self):
        usable = [s.name for s in self.servers if s.usable]
        self.assertEqual(usable, ["example-notes", "example-legacy"])

    def test_nothing_at_all_is_not_an_error(self):
        self.assertEqual(probe.parse_servers(""), [])


class TestDiscovery(unittest.TestCase):
    def test_a_listing_becomes_servers(self):
        found = probe.discover_servers(runner=answering(LISTING))
        self.assertEqual(len(found.servers), 6)
        self.assertIsNone(found.problem)
        self.assertEqual(len(found.connected), 2)

    def test_nothing_found_and_discovery_broken_are_different_answers(self):
        """A list that is empty for both reasons cannot say which, and the two
        have entirely different fixes."""
        empty = probe.discover_servers(runner=answering(""))
        broken = probe.discover_servers(
            runner=failing(probe.MISSING, "not found"))
        self.assertEqual(empty.servers, [])
        self.assertIsNone(empty.problem)
        self.assertEqual(broken.servers, [])
        self.assertIsNotNone(broken.problem)

    def test_the_problem_names_the_command_that_could_not_run(self):
        broken = probe.discover_servers(
            runner=failing(probe.TIMED_OUT, "no answer within 20s"))
        self.assertIn("claude mcp list", broken.problem)
        self.assertIn("no answer", broken.problem)


class TestReportServers(Sandbox):
    def test_nobody_looked_is_said_plainly(self):
        """Discovery health-checks every server over the network. A report
        that quietly did that would hide seconds of work."""
        result = probe.report(self.root, machine_path=self.machine)
        self.assertIn("not probed", result.payload)
        self.assertEqual(result.meta["connected"], 0)

    def test_discovered_servers_are_listed(self):
        result = probe.report(
            self.root, machine_path=self.machine,
            discovery=probe.discover_servers(runner=answering(LISTING)))
        self.assertIn("example-notes", result.payload)
        self.assertIn("needs auth", result.payload)
        self.assertEqual(result.meta["connected"], 2)

    def test_a_machine_with_no_servers_is_not_a_broken_one(self):
        result = probe.report(
            self.root, machine_path=self.machine,
            discovery=probe.discover_servers(runner=answering("")))
        self.assertIn("none configured", result.payload)

    def test_discovery_that_could_not_run_says_so(self):
        result = probe.report(
            self.root, machine_path=self.machine,
            discovery=probe.discover_servers(
                runner=failing(probe.MISSING, "not found")))
        self.assertIn("not discovered", result.payload)

    def test_reporting_still_writes_nothing(self):
        probe.report(self.root, machine_path=self.machine,
                     discovery=probe.discover_servers(runner=answering(LISTING)))
        self.assertFalse(self.machine.exists())
        self.assertFalse(self.settings.exists())


class TestRecordCapabilities(Sandbox):
    def record(self, capabilities=None, **kw):
        return probe.record(machine_path=self.machine,
                            interpreter_file=self.mirror,
                            capabilities=capabilities, **kw)

    def written(self):
        return json.loads(self.machine.read_text(encoding="utf-8"))

    def test_an_entry_reaches_the_file(self):
        self.record({"scm": {"transport": "cli", "provider": "provider-a"}})
        self.assertEqual(self.written()["capabilities"]["scm"],
                         {"transport": "cli", "provider": "provider-a"})

    def test_repo_is_always_there_without_being_asked_for(self):
        self.record()
        self.assertEqual(self.written()["capabilities"]["repo"],
                         {"transport": "local"})

    def test_writing_the_same_entry_twice_changes_nothing(self):
        entry = {"scm": {"transport": "manual"}}
        self.record(entry)
        first = self.machine.read_text(encoding="utf-8")
        result = self.record(entry)
        self.assertEqual(self.machine.read_text(encoding="utf-8"), first)
        self.assertIn("unchanged", result.payload)
        self.assertEqual(result.meta["written"], 0)

    def test_one_capability_is_upgraded_and_the_rest_left_alone(self):
        """Re-running after a server is connected is the whole reason setup is
        worth re-running, and it must not disturb anything else."""
        self.record({"scm": {"transport": "manual"},
                     "kb": {"transport": "unavailable"}})
        self.record({"scm": {"transport": "server", "tool_prefix": "toolns"}})
        capabilities = self.written()["capabilities"]
        self.assertEqual(capabilities["scm"]["transport"], "server")
        self.assertEqual(capabilities["kb"], {"transport": "unavailable"})

    def test_a_hand_written_entry_is_not_discarded(self):
        self.machine.parent.mkdir(parents=True, exist_ok=True)
        self.machine.write_text(json.dumps({
            "schema": 1,
            "output_root": "~/notes",
            "capabilities": {"obs": {"transport": "manual"}},
        }), encoding="utf-8")
        self.record({"scm": {"transport": "manual"}})
        written = self.written()
        self.assertEqual(written["output_root"], "~/notes")
        self.assertEqual(written["capabilities"]["obs"],
                         {"transport": "manual"})

    def test_the_report_says_what_it_left_alone(self):
        self.record({"kb": {"transport": "manual"}})
        result = self.record({"scm": {"transport": "manual"}})
        self.assertIn("LEFT ALONE", result.payload)
        self.assertIn("kb", result.payload.partition("LEFT ALONE")[2])

    def test_what_was_written_is_shown_as_it_would_be_typed(self):
        result = self.record({
            "obs": {"transport": "cli",
                    "provider": ["provider-a", "provider-b"]}})
        self.assertIn("cli:provider-a,provider-b", result.payload)


class TestServerCache(Sandbox):
    def setUp(self):
        super().setUp()
        self.servers_file = self.root / "state" / "servers.json"

    def frozen(self):
        return lambda: "2026-01-01T00:00:00Z"

    def test_what_was_seen_is_kept_with_when(self):
        discovery = probe.discover_servers(runner=answering(LISTING))
        probe.cache_servers(discovery, servers_file=self.servers_file,
                            clock=self.frozen())
        cached = probe.last_probe(servers_file=self.servers_file)
        self.assertEqual(cached["at"], "2026-01-01T00:00:00Z")
        self.assertEqual(len(cached["servers"]), 6)

    def test_a_machine_never_probed_is_not_an_error(self):
        """The ordinary state of a fresh clone, not a fault."""
        self.assertIsNone(probe.last_probe(servers_file=self.servers_file))

    def test_an_unreadable_cache_is_treated_as_absent(self):
        self.servers_file.parent.mkdir(parents=True, exist_ok=True)
        self.servers_file.write_text("{oops", encoding="utf-8")
        self.assertIsNone(probe.last_probe(servers_file=self.servers_file))

    def test_recording_caches_only_when_discovery_ran(self):
        probe.record(machine_path=self.machine, interpreter_file=self.mirror,
                     servers_file=self.servers_file)
        self.assertFalse(self.servers_file.exists())

    def test_recording_with_discovery_caches_it(self):
        result = probe.record(
            machine_path=self.machine, interpreter_file=self.mirror,
            discovery=probe.discover_servers(runner=answering(LISTING)),
            servers_file=self.servers_file, clock=self.frozen())
        self.assertTrue(self.servers_file.exists())
        self.assertIn("6 server(s) last seen", result.payload)

    def test_the_clock_is_the_only_thing_that_moves(self):
        """Two recordings of the same listing differ only by the moment."""
        discovery = probe.discover_servers(runner=answering(LISTING))
        probe.cache_servers(discovery, servers_file=self.servers_file,
                            clock=self.frozen())
        first = probe.last_probe(servers_file=self.servers_file)
        probe.cache_servers(discovery, servers_file=self.servers_file,
                            clock=lambda: "2026-06-01T00:00:00Z")
        second = probe.last_probe(servers_file=self.servers_file)
        self.assertEqual(first["servers"], second["servers"])
        self.assertNotEqual(first["at"], second["at"])


class TestPermission(Sandbox):
    def test_absent_is_reported_with_the_exact_rule(self):
        result = probe.permission(settings_path=self.settings)
        self.assertFalse(result.meta["present"])
        self.assertIn(probe.PERMISSION_RULE, result.payload)
        self.assertFalse(self.settings.exists(), "reporting writes nothing")

    def test_apply_adds_the_rule(self):
        result = probe.permission(apply=True, settings_path=self.settings)
        self.assertTrue(result.meta["applied"])
        self.assertEqual(self.read_settings()["permissions"]["allow"],
                         [probe.PERMISSION_RULE])

    def test_apply_keeps_existing_settings_and_rules(self):
        self.settings.write_text(json.dumps({
            "model": "something",
            "permissions": {"allow": ["Bash(git:*)"], "deny": ["Read(.env)"]},
        }), encoding="utf-8")
        probe.permission(apply=True, settings_path=self.settings)
        data = self.read_settings()
        self.assertEqual(data["model"], "something")
        self.assertEqual(data["permissions"]["deny"], ["Read(.env)"])
        self.assertIn("Bash(git:*)", data["permissions"]["allow"])
        self.assertIn(probe.PERMISSION_RULE, data["permissions"]["allow"])

    def test_applying_twice_does_not_duplicate(self):
        probe.permission(apply=True, settings_path=self.settings)
        result = probe.permission(apply=True, settings_path=self.settings)
        self.assertFalse(result.meta["applied"], "already present")
        self.assertEqual(self.read_settings()["permissions"]["allow"].count(
            probe.PERMISSION_RULE), 1)

    def test_unparseable_settings_are_reported_not_overwritten(self):
        self.settings.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(probe.ProbeError) as ctx:
            probe.permission(apply=True, settings_path=self.settings)
        self.assertIn("not valid JSON", str(ctx.exception))
        self.assertEqual(self.settings.read_text(encoding="utf-8"), "{ not json")

    def test_the_rule_names_the_bare_command(self):
        """A path-based rule would break on the next plugin update."""
        self.assertEqual(probe.PERMISSION_RULE, "Bash(kyrio:*)")


if __name__ == "__main__":
    unittest.main()
