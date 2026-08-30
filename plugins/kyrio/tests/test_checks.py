"""The blocking checks, and the places that have to agree about them.

The lint and the suite are declared in three files -- the hook, the workflow,
and the README -- and nothing keeps them in step on its own. Drift here is
quiet and expensive: a floor raised in one place, a command renamed in another,
and the check that was meant to block stops running while still reporting
green.

These are file-content assertions, which is unusual for a test and deliberate
here. The alternative is a shared script the three call, which trades this
handful of assertions for a fourth file and a layer of indirection in the one
place a reader most wants to see the actual command.
"""

import pathlib
import unittest

import _path  # noqa: F401  -- import side effect: puts scripts/ on sys.path

from kyrio import probe

PLUGIN = pathlib.Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent

HOOK = REPO / ".githooks" / "pre-commit"
WORKFLOW = REPO / ".github" / "workflows" / "check.yml"
README = PLUGIN / "README.md"

LINT = "check_portability.py"
SUITE = "python -m unittest discover -s tests -t tests"


def text(path):
    return path.read_text(encoding="utf-8")


class TestHook(unittest.TestCase):
    def test_it_exists(self):
        self.assertTrue(HOOK.is_file())

    def test_it_runs_the_lint_over_what_is_being_committed(self):
        body = text(HOOK)
        self.assertIn(LINT, body)
        self.assertIn("--staged", body)

    def test_a_missing_interpreter_blocks_the_commit(self):
        """Failing open would be worse than not having the hook at all.

        A clone carries its whole history, so a string removed in a follow-up
        commit is still on every machine that already pulled it.
        """
        self.assertIn("exit 1", text(HOOK))

    def test_the_floor_matches_the_broker(self):
        self.assertIn("(%d, %d)" % probe.MINIMUM_PYTHON[:2], text(HOOK))


class TestWorkflow(unittest.TestCase):
    def setUp(self):
        if not WORKFLOW.is_file():
            self.fail("no CI workflow: the checks block locally only")
        self.body = text(WORKFLOW)

    def test_it_runs_both_checks(self):
        self.assertIn(LINT, self.body)
        self.assertIn(SUITE, self.body)

    def test_it_runs_the_command_the_readme_documents(self):
        self.assertIn(SUITE, text(README))

    def test_every_platform_the_pack_claims_is_covered(self):
        """Path separators, line endings, the executable bit, and console
        encodings are all invisible on the machine this was written on."""
        for platform in ("ubuntu-latest", "windows-latest", "macos-latest"):
            with self.subTest(platform=platform):
                self.assertIn(platform, self.body)

    def test_the_floor_matches_the_broker(self):
        self.assertIn('"%d.%d"' % probe.MINIMUM_PYTHON[:2], self.body)

    def test_nothing_is_installed(self):
        """The pack depends on the standard library alone. A workflow that
        pulled a package in would be testing something other than what ships."""
        self.assertNotIn("pip install", self.body)

    def test_one_platform_failing_does_not_hide_another(self):
        self.assertIn("fail-fast: false", self.body)


if __name__ == "__main__":
    unittest.main()
