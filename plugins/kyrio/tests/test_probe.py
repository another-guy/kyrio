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
