import contextlib
import io
import json
import pathlib
import shutil
import tempfile
import unittest

import _path  # noqa: F401  -- import side effect: puts scripts/ on sys.path

from kyrio import __main__ as main_module
from kyrio import cli, config


class Run(unittest.TestCase):
    """Runs the broker end to end and parses the framed response."""

    def setUp(self):
        self.root = pathlib.Path(
            tempfile.mkdtemp(prefix="kyrio-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.machine = self.root / "machine" / "config.json"
        # No machine layer unless a test writes one; never read the real one.
        self._real_machine = config.MACHINE_CONFIG
        config.MACHINE_CONFIG = self.machine
        self.addCleanup(setattr, config, "MACHINE_CONFIG", self._real_machine)

    def write_machine(self, data):
        self.machine.parent.mkdir(parents=True, exist_ok=True)
        body = {"schema": config.SCHEMA_VERSION}
        body.update(data)
        self.machine.write_text(json.dumps(body), encoding="utf-8")

    def write_layer(self, relative, data):
        path = (self.root / relative / config.CONFIG_DIRNAME
                / config.CONFIG_FILENAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {"schema": config.SCHEMA_VERSION}
        body.update(data)
        path.write_text(json.dumps(body), encoding="utf-8")
        return path

    def run_kyrio(self, *argv):
        """Return (exit_code, header, payload) from one invocation."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main_module.main(list(argv))
        text = buf.getvalue()
        head, _, body = text.partition("\n")
        header = json.loads(head)
        payload = None
        if body.startswith("---\n"):
            payload = body[len("---\n"):]
        return code, header, payload


class TestParser(unittest.TestCase):
    def test_bad_argument_raises_instead_of_exiting(self):
        parser = cli.Parser(prog="kyrio")
        parser.add_argument("--cwd")
        with self.assertRaises(cli.UsageError) as ctx:
            parser.parse_args(["--nope"])
        self.assertTrue(ctx.exception.usage)

    def test_exit_is_also_an_exception(self):
        parser = cli.Parser(prog="kyrio")
        with self.assertRaises(cli.UsageError):
            parser.exit(0)

    def test_parser_never_writes_to_a_stream(self):
        parser = cli.Parser(prog="kyrio")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertIsNone(parser._print_message("noise\n"))
        self.assertEqual(buf.getvalue(), "")


class TestDispatch(Run):
    def test_no_command_is_an_error_with_usage(self):
        code, header, payload = self.run_kyrio()
        self.assertEqual(code, 1)
        self.assertEqual(header["status"], "error")
        self.assertIn("kyrio caps", payload)

    def test_help_is_a_result_not_an_error(self):
        code, header, payload = self.run_kyrio("help")
        self.assertEqual(code, 0)
        self.assertEqual(header["kind"], "help")
        self.assertIn("kyrio config explain", payload)

    def test_unknown_command_names_what_exists(self):
        code, header, _ = self.run_kyrio("deploy")
        self.assertEqual(code, 1)
        self.assertEqual(header["status"], "error")
        self.assertIn("caps", header["known"])

    def test_help_advertises_every_implemented_command_and_no_other(self):
        """A noun that is not built yet is an error naming what does exist,
        never a line in the usage text that quietly does nothing. Reading the
        list from the dispatch table means adding a command cannot leave the
        usage text behind, and neither can removing one."""
        _, _, payload = self.run_kyrio("help")
        for name in main_module.COMMANDS:
            with self.subTest(command=name):
                self.assertIn("kyrio %s" % name, payload)
        unbuilt = [name for name in config.CAPABILITIES
                   if name not in main_module.COMMANDS]
        self.assertTrue(unbuilt, "every capability has a noun; drop this half")
        for word in unbuilt:
            with self.subTest(command=word):
                self.assertNotIn("kyrio %s" % word, payload)

    def test_bad_global_flag_is_framed_not_raw(self):
        code, header, payload = self.run_kyrio("--nope", "caps")
        self.assertEqual(code, 1)
        self.assertEqual(header["status"], "error")
        self.assertIn("kyrio caps", payload)

    def test_a_malformed_layer_becomes_an_error_response(self):
        path = (self.root / config.CONFIG_DIRNAME / config.CONFIG_FILENAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{oops", encoding="utf-8")
        code, header, _ = self.run_kyrio("--cwd", str(self.root), "caps")
        self.assertEqual(code, 1)
        self.assertEqual(header["status"], "error")
        self.assertIn("not valid JSON", header["message"])


class TestCaps(Run):
    def test_repo_is_ready_on_a_machine_with_no_configuration(self):
        code, header, payload = self.run_kyrio("--cwd", str(self.root), "caps")
        self.assertEqual(code, 0)
        self.assertEqual(header["kind"], "caps")
        self.assertEqual(header["layers"], 0)
        self.assertEqual(header["ready"], 1)
        self.assertIn("repo", payload)
        self.assertIn("ready", payload)

    def test_every_capability_appears_exactly_once(self):
        _, _, payload = self.run_kyrio("--cwd", str(self.root), "caps")
        for name in config.CAPABILITIES:
            with self.subTest(capability=name):
                rows = [l for l in payload.splitlines()
                        if l.strip().startswith(name + " ")]
                self.assertEqual(len(rows), 1)

    def test_configured_transports_are_reported(self):
        self.write_machine({"capabilities": {
            "scm": {"transport": "cli", "provider": "provider-a"},
            "issue": {"transport": "server", "tool_prefix": "toolns"},
            "kb": {"transport": "unavailable"},
        }})
        _, header, payload = self.run_kyrio("--cwd", str(self.root), "caps")
        self.assertEqual(header["configured"], 2)
        self.assertEqual(header["unavailable"], 1)
        self.assertEqual(header["unconfigured"], 2)  # ci and obs
        self.assertIn("server", payload)

    def test_an_incomplete_entry_is_not_reported_as_configured(self):
        """A cli transport with no provider names no adapter to choose. What
        configuration says is only useful while it is checked."""
        self.write_machine({"capabilities": {"scm": {"transport": "cli"}}})
        _, header, payload = self.run_kyrio("--cwd", str(self.root), "caps")
        self.assertEqual(header["configured"], 0)
        self.assertIn("provider", payload)

    def test_each_gap_carries_its_own_fix(self):
        """One hint covering every gap sends a person to the wrong file. A
        capability configured for a provider nothing ships for is not fixed by
        running setup again, and its line does not say so."""
        self.write_machine({"capabilities": {
            "scm": {"transport": "cli", "provider": "provider-a"},
        }})
        _, _, payload = self.run_kyrio("--cwd", str(self.root), "caps")
        gaps = payload.partition("GAPS")[2]
        scm = [l for l in gaps.splitlines() if l.strip().startswith("scm:")][0]
        self.assertIn("provider-a", scm)
        self.assertNotIn("/kyrio:setup", scm)

    def test_nearer_layers_change_the_report(self):
        self.write_machine({"capabilities": {"scm": {"transport": "cli"}}})
        self.write_layer("repo", {"capabilities": {"scm": {"transport": "server"}}})
        (self.root / "repo").mkdir(parents=True, exist_ok=True)
        _, _, payload = self.run_kyrio("--cwd", str(self.root / "repo"), "caps")
        scm = [l for l in payload.splitlines() if l.strip().startswith("scm ")][0]
        self.assertIn("server", scm)

    def test_a_fully_configured_machine_gets_no_setup_hint(self):
        self.write_machine({"capabilities": {
            name: {"transport": "cli", "provider": "provider-a"}
            for name in config.CAPABILITIES if name != "repo"}})
        _, header, payload = self.run_kyrio("--cwd", str(self.root), "caps")
        self.assertEqual(header["unconfigured"], 0)
        self.assertNotIn("/kyrio:setup", payload)

    def test_an_unconfigured_machine_is_told_what_to_run(self):
        _, _, payload = self.run_kyrio("--cwd", str(self.root), "caps")
        self.assertIn("/kyrio:setup", payload)


class TestConfigExplain(Run):
    def test_missing_verb_is_an_error(self):
        code, header, _ = self.run_kyrio("config")
        self.assertEqual(code, 1)
        self.assertEqual(header["status"], "error")

    def test_unknown_verb_is_an_error(self):
        code, header, _ = self.run_kyrio("config", "dump")
        self.assertEqual(code, 1)
        self.assertEqual(header["status"], "error")

    def test_no_layers_is_a_result_not_an_error(self):
        code, header, payload = self.run_kyrio(
            "--cwd", str(self.root), "config", "explain")
        self.assertEqual(code, 0)
        self.assertEqual(header["layers"], 0)
        self.assertIn("/kyrio:setup", payload)

    def test_every_value_names_the_layer_that_supplied_it(self):
        self.write_machine({"shell": "outer"})
        self.write_layer("repo", {"shell": "inner"})
        (self.root / "repo").mkdir(parents=True, exist_ok=True)
        code, header, payload = self.run_kyrio(
            "--cwd", str(self.root / "repo"), "config", "explain")
        self.assertEqual(code, 0)
        self.assertEqual(header["layers"], 2)
        self.assertIn("[1] %s" % self.machine, payload)
        row = [l for l in payload.splitlines() if l.strip().startswith("shell")][0]
        self.assertIn("inner", row)
        self.assertIn("[1,2]", row, "both layers set it; both are named")

    def test_a_value_set_once_names_one_layer(self):
        self.write_machine({"shell": "outer"})
        self.write_layer("repo", {"output_root": "o"})
        (self.root / "repo").mkdir(parents=True, exist_ok=True)
        _, _, payload = self.run_kyrio(
            "--cwd", str(self.root / "repo"), "config", "explain")
        row = [l for l in payload.splitlines()
               if l.strip().startswith("output_root")][0]
        self.assertIn("[2]", row)

    def test_nested_keys_are_shown_as_dotted_paths(self):
        self.write_machine({"capabilities": {"scm": {"transport": "cli"}}})
        _, header, payload = self.run_kyrio(
            "--cwd", str(self.root), "config", "explain")
        self.assertIn("capabilities.scm.transport", payload)
        self.assertEqual(header["keys"], 1)

    def test_non_string_values_render_on_one_line(self):
        self.write_machine({"ignore": ["a", "b"]})
        _, _, payload = self.run_kyrio(
            "--cwd", str(self.root), "config", "explain")
        row = [l for l in payload.splitlines() if l.strip().startswith("ignore")][0]
        self.assertIn('["a", "b"]', row)


class TestTable(unittest.TestCase):
    """One table helper, so two reports cannot drift into two shapes."""

    def test_columns_align_and_the_last_is_not_padded(self):
        text = cli.table(("A", "BBBB"), [("xx", "y")])
        self.assertEqual(text, "  A   BBBB\n  xx  y\n")

    def test_headers_alone_still_produce_a_table(self):
        self.assertEqual(cli.table(("A", "B"), []), "  A  B\n")


if __name__ == "__main__":
    unittest.main()
