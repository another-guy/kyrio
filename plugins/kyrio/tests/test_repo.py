import datetime
import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

import _path  # noqa: F401  -- import side effect: puts scripts/ on sys.path

from kyrio import repo

GIT = shutil.which("git")

#: Committing needs an identity, and the machine's own must never leak into a
#: test fixture (I8). These are passed per invocation, not written anywhere.
IDENTITY = ["-c", "user.name=Test Author",
            "-c", "user.email=author@example.invalid",
            "-c", "commit.gpgsign=false"]


class GitRepo(unittest.TestCase):
    """A synthetic working tree, built commit by commit."""

    def setUp(self):
        if not GIT:
            self.skipTest("git is not available")
        self.root = pathlib.Path(
            tempfile.mkdtemp(prefix="kyrio-repo-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.git("init", "-q", "-b", "main")

    def git(self, *args):
        result = subprocess.run(
            [GIT, *IDENTITY, *args], capture_output=True, text=True,
            cwd=str(self.root))
        self.assertEqual(result.returncode, 0,
                         "git %s: %s" % (args[0], result.stderr))
        return result.stdout

    def write(self, relative, text=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def commit(self, message, when=None):
        self.git("add", "-A")
        args = ["commit", "-q", "-m", message]
        if when:
            stamp = "%sT12:00:00" % when
            self.git("-c", "user.name=Test Author", "commit", "-q", "-m",
                     message, "--date", stamp)
        else:
            self.git(*args)


class TestWindow(unittest.TestCase):
    """Window arithmetic is resolved before git sees it (I9)."""

    TODAY = datetime.date(2026, 6, 15)

    def test_days_weeks_months_years(self):
        cases = [("30d", "2026-05-16"), ("2w", "2026-06-01"),
                 ("3mo", "2026-03-17"), ("1y", "2025-06-15")]
        for window, expected in cases:
            with self.subTest(window=window):
                self.assertEqual(
                    repo.since_date(window, today=self.TODAY)[0], expected)

    def test_an_explicit_date_passes_through(self):
        self.assertEqual(repo.since_date("2026-01-01", today=self.TODAY),
                         ("2026-01-01", "2026-01-01"))

    def test_the_label_records_what_was_asked_for(self):
        self.assertEqual(repo.since_date("30d", today=self.TODAY)[1], "30d")

    def test_the_default_is_used_when_nothing_is_given(self):
        self.assertEqual(repo.since_date(None, today=self.TODAY)[0],
                         repo.since_date(repo.DEFAULT_WINDOW,
                                         today=self.TODAY)[0])

    def test_an_unparseable_window_says_what_is_accepted(self):
        with self.assertRaises(repo.RepoError) as ctx:
            repo.since_date("last tuesday")
        self.assertIn("30d", str(ctx.exception))


class TestRoot(GitRepo):
    def test_found_from_a_subdirectory(self):
        self.write("src/api/handler.py", "x = 1\n")
        self.commit("first")
        self.assertEqual(repo.root(self.root / "src" / "api"), self.root)

    def test_outside_a_working_tree_is_an_error(self):
        outside = pathlib.Path(tempfile.mkdtemp(prefix="kyrio-bare-")).resolve()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        with self.assertRaises(repo.RepoError) as ctx:
            repo.root(outside)
        self.assertIn("working tree", str(ctx.exception))


class TestMap(GitRepo):
    def test_reports_root_branch_and_tracked_count(self):
        self.write("src/api/handler.py", "x = 1\n")
        self.write("README.md", "hello\n")
        self.commit("first")
        result = repo.repo_map(self.root)
        self.assertEqual(result.meta["files"], 2)
        self.assertIn("main", result.payload)
        self.assertIn("src", result.payload)

    def test_a_fresh_repository_says_so(self):
        with self.assertRaises(repo.RepoError) as ctx:
            repo.repo_map(self.root)
        self.assertIn("fresh", str(ctx.exception))

    def test_directories_report_what_they_mostly_hold(self):
        for name in ("a.py", "b.py", "c.py"):
            self.write("src/%s" % name, "x = 1\n")
        self.write("docs/guide.md", "text\n")
        self.commit("first")
        result = repo.repo_map(self.root)
        row = [l for l in result.payload.splitlines()
               if l.strip().startswith("src ")][0]
        self.assertIn("python", row)

    def test_package_scripts_supply_the_commands(self):
        self.write("package.json", json.dumps(
            {"scripts": {"build": "tsc", "test": "vitest"}}))
        self.commit("first")
        result = repo.repo_map(self.root)
        self.assertIn("npm run build", result.payload)
        self.assertIn("npm test", result.payload)
        self.assertTrue(result.meta["build"])

    def test_a_package_without_the_script_is_not_claimed(self):
        self.write("package.json", json.dumps({"scripts": {"lint": "eslint"}}))
        self.commit("first")
        result = repo.repo_map(self.root)
        self.assertIn("not detected", result.payload)
        self.assertFalse(result.meta["build"])

    def test_makefile_targets_are_detected(self):
        self.write("Makefile", "build:\n\techo building\n\ntest:\n\techo t\n")
        self.commit("first")
        result = repo.repo_map(self.root)
        self.assertIn("make build", result.payload)
        self.assertIn("make test", result.payload)

    def test_a_project_file_implies_its_toolchain(self):
        self.write("src/App.csproj", "<Project />\n")
        self.commit("first")
        result = repo.repo_map(self.root)
        self.assertIn("dotnet build", result.payload)

    def test_configuration_beats_detection_and_says_so(self):
        self.write("package.json", json.dumps({"scripts": {"test": "vitest"}}))
        self.commit("first")
        result = repo.repo_map(
            self.root, conventions={"test": "npm run test:ci"})
        self.assertIn("npm run test:ci", result.payload)
        self.assertIn("from configuration", result.payload)

    def test_entry_points_are_listed(self):
        self.write("src/main.py", "print\n")
        self.write("src/helper.py", "x = 1\n")
        self.commit("first")
        result = repo.repo_map(self.root)
        self.assertEqual(result.meta["entrypoints"], 1)
        self.assertIn("src/main.py", result.payload)


class TestChurn(GitRepo):
    def build_history(self):
        self.write("hot.py", "1\n")
        self.write("cold.py", "1\n")
        self.commit("first")
        for n in range(3):
            self.write("hot.py", "%d\n" % n)
            self.commit("change %d" % n)

    def test_most_changed_first(self):
        self.build_history()
        result = repo.churn(self.root, window="1y")
        rows = [l for l in result.payload.splitlines() if ".py" in l]
        self.assertIn("hot.py", rows[0])
        self.assertEqual(result.meta["commits"], 4)

    def test_the_window_is_reported_as_a_date(self):
        self.build_history()
        result = repo.churn(self.root, window="30d",
                            today=datetime.date(2026, 6, 15))
        self.assertEqual(result.meta["since"], "2026-05-16")
        self.assertIn("Since 2026-05-16 (30d)", result.payload)

    def test_top_limits_the_rows_not_the_counts(self):
        self.build_history()
        result = repo.churn(self.root, window="1y", top_n=1)
        self.assertEqual(result.meta["shown"], 1)
        self.assertEqual(result.meta["files"], 2)

    def test_an_empty_window_is_a_result_not_an_error(self):
        self.build_history()
        result = repo.churn(self.root, window="2020-01-01",
                            today=datetime.date(2020, 6, 1))
        self.assertIn("Since 2020-01-01", result.payload)

    def test_a_path_narrows_the_history(self):
        self.build_history()
        result = repo.churn(self.root, window="1y", path="cold.py")
        self.assertEqual(result.meta["files"], 1)
        self.assertIn("under cold.py", result.payload)


class TestOwners(GitRepo):
    OWNERS = (
        "# comment line\n"
        "*                 @default-reviewers\n"
        "/src/api/         @api-maintainers @second\n"
        "*.md              @docs-writers\n")

    def test_rules_are_listed_when_no_path_is_given(self):
        self.write(".github/CODEOWNERS", self.OWNERS)
        self.commit("first")
        result = repo.owners(self.root)
        self.assertEqual(result.meta["rules"], 3)
        self.assertIn("@api-maintainers", result.payload)

    def test_comments_and_blank_lines_are_ignored(self):
        self.write(".github/CODEOWNERS", self.OWNERS)
        self.commit("first")
        result = repo.owners(self.root)
        self.assertNotIn("comment line", result.payload)

    def test_the_last_matching_rule_wins(self):
        self.write(".github/CODEOWNERS", self.OWNERS)
        self.commit("first")
        result = repo.owners(self.root, path="README.md")
        self.assertIn("@docs-writers", result.payload)
        self.assertTrue(result.meta["matched"])

    def test_a_directory_rule_matches_what_is_under_it(self):
        self.write(".github/CODEOWNERS", self.OWNERS)
        self.commit("first")
        result = repo.owners(self.root, path="src/api/handler.py")
        self.assertIn("@api-maintainers", result.payload)

    def test_without_an_ownership_file_history_answers_instead(self):
        self.write("src/handler.py", "x = 1\n")
        self.commit("first")
        result = repo.owners(self.root, path="src/handler.py")
        self.assertFalse(result.meta["matched"])
        self.assertIn("Test Author", result.payload)

    def test_without_a_file_and_without_a_path_it_says_what_to_do(self):
        self.write("src/handler.py", "x = 1\n")
        self.commit("first")
        with self.assertRaises(repo.RepoError) as ctx:
            repo.owners(self.root)
        self.assertIn("pass a path", str(ctx.exception))


class TestBlame(GitRepo):
    def setUp(self):
        super().setUp()
        self.write("src/handler.py", "one\ntwo\nthree\n")
        self.commit("add the handler\n\nWith a body explaining why.")

    def test_a_single_line(self):
        result = repo.blame(self.root, "src/handler.py:2")
        self.assertEqual(result.meta["lines"], 1)
        self.assertIn("Test Author", result.payload)
        self.assertIn("add the handler", result.payload)
        self.assertIn("two", result.payload)

    def test_the_commit_body_is_included(self):
        result = repo.blame(self.root, "src/handler.py:1")
        self.assertIn("With a body explaining why", result.payload)

    def test_a_range(self):
        result = repo.blame(self.root, "src/handler.py:1-3")
        self.assertEqual(result.meta["lines"], 3)
        for text in ("one", "two", "three"):
            self.assertIn(text, result.payload)

    def test_lines_sharing_a_commit_are_described_once(self):
        result = repo.blame(self.root, "src/handler.py:1-3")
        self.assertEqual(result.meta["commits"], 1)
        self.assertEqual(result.payload.count("SUMMARY"), 1,
                         "one commit, one message, however many lines")

    def test_a_malformed_location_says_the_shape_expected(self):
        for bad in ("src/handler.py", "src/handler.py:", ":4", ""):
            with self.subTest(location=bad):
                with self.assertRaises(repo.RepoError) as ctx:
                    repo.blame(self.root, bad)
                self.assertIn("path", str(ctx.exception))

    def test_an_inverted_range_is_rejected(self):
        with self.assertRaises(repo.RepoError):
            repo.blame(self.root, "src/handler.py:3-1")

    def test_a_missing_file_is_an_error_not_a_crash(self):
        with self.assertRaises(repo.RepoError):
            repo.blame(self.root, "src/absent.py:1")


class TestSubprocessDiscipline(unittest.TestCase):
    def test_git_is_never_invoked_through_a_shell(self):
        """I5 in the one module that runs an external program."""
        source = (pathlib.Path(repo.__file__)).read_text(encoding="utf-8")
        self.assertNotIn("shell=True", source)
        self.assertIn('["git", *args]', source)


if __name__ == "__main__":
    unittest.main()
