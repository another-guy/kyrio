import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

import _path  # noqa: F401  -- import side effect: puts scripts/ on sys.path

PLUGIN = pathlib.Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent
SH_SHIM = PLUGIN / "bin" / "kyrio"
CMD_SHIM = PLUGIN / "bin" / "kyrio.cmd"
ENTRY = PLUGIN / "scripts" / "kyrio" / "__main__.py"


def framed(text):
    """Split a broker response into (header, payload)."""
    head, _, body = text.partition("\n")
    payload = body[len("---\n"):] if body.startswith("---\n") else None
    return json.loads(head), payload


class TestEntryPoint(unittest.TestCase):
    """The shims run the entry point as a file, not as a module."""

    def test_runs_standalone_without_pythonpath(self):
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, str(ENTRY), "caps"],
            capture_output=True, text=True, env=env, cwd=str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        header, payload = framed(result.stdout)
        self.assertEqual(header["kind"], "caps")
        self.assertIn("repo", payload)

    def test_the_wire_format_is_the_same_bytes_on_every_platform(self):
        """Captured as bytes on purpose: text mode would hide the difference.

        The platform's own line-ending translation would otherwise rewrite
        every line on the way out, putting a payload the broker deliberately
        normalized back into a second form (I9).
        """
        result = subprocess.run(
            [sys.executable, str(ENTRY), "caps"],
            capture_output=True, cwd=str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(b"\r", result.stdout)

    def test_exit_code_reaches_the_caller(self):
        result = subprocess.run(
            [sys.executable, str(ENTRY), "nonsense"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(framed(result.stdout)[0]["status"], "error")


class TestShimFiles(unittest.TestCase):
    def test_both_shims_exist(self):
        self.assertTrue(SH_SHIM.is_file())
        self.assertTrue(CMD_SHIM.is_file())

    def test_sh_shim_has_lf_endings_and_a_shebang(self):
        data = SH_SHIM.read_bytes()
        self.assertTrue(data.startswith(b"#!/bin/sh\n"))
        self.assertNotIn(b"\r\n", data,
                         "a CRLF shebang makes the shell hunt for `sh\\r`")

    def test_cmd_shim_has_crlf_endings(self):
        data = CMD_SHIM.read_bytes()
        self.assertIn(b"\r\n", data)

    def test_gitattributes_pins_both_conventions(self):
        text = (REPO / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("eol=lf", text)
        self.assertIn("*.cmd text eol=crlf", text)

    @unittest.skipUnless(shutil.which("git"), "git is not available")
    def test_sh_shim_is_executable_in_the_index(self):
        """core.fileMode is false on Windows, so chmod alone is not recorded.

        Without mode 100755 in the index, a clone on any other platform gets a
        non-executable shim and `kyrio` fails with a permission error.
        """
        result = subprocess.run(
            ["git", "ls-files", "-s", "plugins/kyrio/bin/kyrio"],
            capture_output=True, text=True, cwd=str(REPO))
        if not result.stdout.strip():
            self.skipTest("shim is not tracked yet")
        self.assertTrue(
            result.stdout.startswith("100755"),
            "run: git update-index --chmod=+x plugins/kyrio/bin/kyrio")

    def test_neither_shim_can_run_a_caller_supplied_command(self):
        """I5 reaches down into the shims: no eval, no passthrough."""
        for shim in (SH_SHIM, CMD_SHIM):
            text = shim.read_text(encoding="utf-8", errors="replace")
            with self.subTest(shim=shim.name):
                self.assertNotIn("eval ", text)


@unittest.skipUnless(shutil.which("sh"), "no POSIX shell on this machine")
class TestShShim(unittest.TestCase):
    def run_shim(self, *argv, env=None):
        """Launch the shell by absolute path, resolved from the real PATH.

        One test below strips PATH to prove the shim reports a missing
        interpreter, and on POSIX the program name is resolved against the
        *child's* PATH -- so a bare "sh" disappears along with everything else
        the test meant to hide. Windows resolves against the parent's PATH
        instead, which is why a bare name survives there and this only fails
        away from it.
        """
        result = subprocess.run(
            [shutil.which("sh"), str(SH_SHIM), *argv],
            capture_output=True, text=True, env=env)
        return result

    def test_produces_a_framed_response(self):
        result = self.run_shim("caps")
        self.assertEqual(result.returncode, 0, result.stderr)
        header, _ = framed(result.stdout)
        self.assertEqual(header["status"], "ok")

    def test_no_interpreter_is_still_a_framed_response(self):
        """The one failure Python can never report, because Python never runs.

        Emptying PATH takes `dirname` and `cat` with it, not just the
        interpreters. That is deliberate rather than tolerated: the shim guards
        both, so this also proves it reaches the interpreter message instead of
        failing earlier on a missing utility.
        """
        empty = tempfile.mkdtemp(prefix="kyrio-empty-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        env = dict(os.environ)
        env["PATH"] = empty
        env["KYRIO_PYTHON"] = str(pathlib.Path(empty) / "absent")
        result = self.run_shim("caps", env=env)
        self.assertEqual(result.returncode, 1)
        header, payload = framed(result.stdout)
        self.assertEqual(header["status"], "error")
        self.assertIn("3.12", header["message"])
        self.assertIn("KYRIO_PYTHON", payload)


if __name__ == "__main__":
    unittest.main()
