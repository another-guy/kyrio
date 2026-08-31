"""Tests for the portability lint.

This file is exempt from RULE 1 for the same reason the lint itself is: the
test that proves a vocabulary rule fires has to contain the vocabulary. The
exemption covers RULE 1 only, names exactly two files, and is asserted below so
it cannot quietly widen.
"""

import os
import pathlib
import shutil
import subprocess
import sys
import unittest

import _path  # noqa: F401  -- import side effect: puts scripts/ on sys.path

import check_portability as lint

PLUGIN = pathlib.Path(__file__).resolve().parent.parent
REPO = PLUGIN.parent.parent
HOOK = REPO / ".githooks" / "pre-commit"

SKILL = "plugins/kyrio/skills/example/SKILL.md"
CODE = "plugins/kyrio/scripts/kyrio/adapter.py"


def rules(findings):
    return sorted({f.rule for f in findings})


def check(path, text, skills=frozenset()):
    return lint.check_text(path, text, skills=skills)


class TestRuleOne(unittest.TestCase):
    """Environment coupling, anywhere in the repository."""

    def test_a_word_implying_more_than_one_environment_is_flagged(self):
        findings = check(CODE, "# one config per tenant\n")
        self.assertEqual(rules(findings), ["RULE 1"])

    def test_matching_is_on_whole_words(self):
        self.assertEqual(check(CODE, "storage = compute_orgy_score()\n"), [])

    def test_matching_ignores_case(self):
        self.assertTrue(check(CODE, "# Organization-wide default\n"))

    def test_a_private_hostname_is_flagged(self):
        findings = check(CODE, 'HOST = "build-01.example.internal"\n')
        self.assertEqual(rules(findings), ["RULE 1"])

    def test_an_ordinary_dotted_name_is_not_a_hostname(self):
        self.assertEqual(check(CODE, "value = os.path.dirname(x)\n"), [])

    def test_a_module_constant_is_not_a_hostname(self):
        """``capability.LOCAL`` reads as a ``.local`` address to a regular
        expression and to nothing else."""
        for line in ("self.assertEqual(r.transport, capability.LOCAL)\n",
                     "if flavor == shell.LAN:\n"):
            with self.subTest(line=line.strip()):
                self.assertEqual(check(CODE, line), [])

    def test_a_mixed_case_hostname_is_still_flagged(self):
        """Only the suffix has to be lower case; the rest of a hostname is
        written however someone typed it."""
        findings = check(CODE, 'HOST = "Build-01.Example.internal"\n')
        self.assertEqual(rules(findings), ["RULE 1"])

    def test_a_home_directory_is_flagged(self):
        for line in ('p = "C:\\\\Users\\\\someone\\\\code"\n',
                     'p = "/home/someone/code"\n',
                     'p = "/Users/someone/code"\n'):
            with self.subTest(line=line.strip()):
                self.assertTrue(check(CODE, line))

    def test_a_tilde_path_is_portable(self):
        self.assertEqual(check(CODE, 'p = "~/.claude/kyrio/config.json"\n'), [])

    def test_a_real_looking_identifier_is_flagged(self):
        findings = check(CODE, "# see ACME-4821 for background\n")
        self.assertEqual(rules(findings), ["RULE 1"])

    def test_placeholder_identifiers_are_the_supported_way_to_write_one(self):
        self.assertEqual(check(CODE, "kyrio issue get PROJ-1234\n"), [])

    def test_the_rule_applies_to_fixtures(self):
        path = "plugins/kyrio/tests/fixtures/diff.txt"
        self.assertTrue(check(path, "captured from BUG-9912\n"),
                        "fixtures are the likeliest place for a real payload")


class TestRuleTwo(unittest.TestCase):
    """Skills speak the broker's grammar and nothing else."""

    def fence(self, body, lang="sh"):
        return "Do this:\n\n```%s\n%s\n```\n" % (lang, body)

    def test_a_direct_provider_command_is_flagged(self):
        findings = check(SKILL, self.fence("acmectl pr view 4821"))
        self.assertEqual(rules(findings), ["RULE 2"])

    def test_the_broker_is_permitted(self):
        self.assertEqual(check(SKILL, self.fence("kyrio scm pr diff 4821")), [])

    def test_ordinary_developer_tooling_is_permitted(self):
        for command in ("git log --oneline", "pytest -q", "npm run build"):
            with self.subTest(command=command):
                self.assertEqual(check(SKILL, self.fence(command)), [])

    def test_a_prompt_prefix_is_ignored(self):
        self.assertEqual(check(SKILL, self.fence("$ kyrio caps")), [])
        self.assertTrue(check(SKILL, self.fence("$ acmectl caps")))

    def test_comments_inside_a_fence_are_not_commands(self):
        self.assertEqual(check(SKILL, self.fence("# acmectl is not used")), [])

    def test_non_command_fences_are_left_alone(self):
        body = '{"status": "ok"}'
        self.assertEqual(check(SKILL, self.fence(body, lang="json")), [])

    def test_a_url_ties_prose_to_one_environment(self):
        findings = check(SKILL, "See https://wiki.example.com/runbook\n")
        self.assertIn("RULE 2", rules(findings))

    def test_rule_two_does_not_apply_outside_prose(self):
        self.assertEqual(check(CODE, self.fence("acmectl pr view")), [])


class TestRuleThree(unittest.TestCase):
    """A skill may only lean on what travels with it."""

    def test_a_personally_installed_skill_is_flagged(self):
        findings = check(SKILL, "Then run /grill-me to review the plan.\n")
        self.assertEqual(rules(findings), ["RULE 3"])

    def test_a_bundled_command_is_permitted(self):
        self.assertEqual(check(SKILL, "Run /init to set up the repo.\n"), [])

    def test_a_sibling_skill_must_actually_be_shipped(self):
        text = "Continue with /kyrio:review-pass for a second opinion.\n"
        self.assertTrue(check(SKILL, text))
        self.assertEqual(check(SKILL, text, skills={"review-pass"}), [])

    def test_a_path_is_not_a_slash_command(self):
        self.assertEqual(check(SKILL, "Look in src/api for the handler.\n"), [])


class TestExemption(unittest.TestCase):
    def test_the_exemption_is_two_named_files(self):
        self.assertEqual(len(lint.VOCABULARY_EXEMPT), 2)
        for name in lint.VOCABULARY_EXEMPT:
            self.assertTrue((REPO / name).is_file(), name)

    def test_the_same_text_is_flagged_elsewhere(self):
        """The exemption suppresses reporting, not the rule itself."""
        text = "# one per tenant\n"
        self.assertEqual(check("plugins/kyrio/scripts/check_portability.py",
                               text), [])
        self.assertTrue(check(CODE, text))


class TestRepository(unittest.TestCase):
    def test_the_repository_is_clean(self):
        findings = lint.check_paths(list(lint.walk(REPO)), REPO)
        self.assertEqual(
            [str(f) for f in findings], [],
            "the tree must pass its own portability check")

    def test_something_was_actually_scanned(self):
        self.assertGreater(len(list(lint.walk(REPO))), 10)

    def test_binary_and_vendored_paths_are_skipped(self):
        scanned = {p.name for p in lint.walk(REPO)}
        self.assertNotIn("index", scanned, ".git must not be scanned")


class TestReport(unittest.TestCase):
    def test_the_report_survives_a_legacy_console_codepage(self):
        """A finding quotes the offending line, which may be any encoding.

        Without an explicit UTF-8 stream the report dies in a traceback and the
        reason for the block is lost, which reads as a broken tool.
        """
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        result = subprocess.run(
            [sys.executable, str(PLUGIN / "scripts" / "check_portability.py")],
            capture_output=True, text=True, env=env, cwd=str(REPO))
        self.assertNotIn("UnicodeEncodeError", result.stderr)


class TestHook(unittest.TestCase):
    def test_the_hook_exists_with_lf_endings(self):
        data = HOOK.read_bytes()
        self.assertTrue(data.startswith(b"#!/bin/sh\n"))
        self.assertNotIn(b"\r\n", data)

    def test_the_hook_runs_the_lint_against_what_is_staged(self):
        text = HOOK.read_text(encoding="utf-8")
        self.assertIn("check_portability.py", text)
        self.assertIn("--staged", text)

    @unittest.skipUnless(shutil.which("git"), "git is not available")
    def test_the_hook_is_executable_in_the_index(self):
        result = subprocess.run(
            ["git", "ls-files", "-s", ".githooks/pre-commit"],
            capture_output=True, text=True, cwd=str(REPO))
        if not result.stdout.strip():
            self.skipTest("hook is not tracked yet")
        self.assertTrue(result.stdout.startswith("100755"),
                        "run: git update-index --chmod=+x .githooks/pre-commit")

    @unittest.skipUnless(shutil.which("git"), "git is not available")
    def test_the_hook_is_installed_for_this_clone(self):
        """A reminder, never an assertion.

        ``core.hooksPath`` is a setting on one clone on one machine, and the
        README makes installing it a decision rather than a side effect of
        cloning. Asserting it would fail a fresh clone, and every CI runner,
        for having done nothing wrong -- which is the exact machine coupling
        this file exists to catch (I2, I4).
        """
        result = subprocess.run(
            ["git", "config", "core.hooksPath"],
            capture_output=True, text=True, cwd=str(REPO))
        if result.stdout.strip() != ".githooks":
            self.skipTest(
                "the hook is not installed here; to install it, run: "
                "git config core.hooksPath .githooks")


if __name__ == "__main__":
    unittest.main()
