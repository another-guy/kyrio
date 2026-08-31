"""The scm capability: one change, its diff, and every way that can fail.

The diff fixture is hand-written (I8). A real one carries branch names, author
names, and internal paths, and capturing one is how a repository acquires a
payload from somewhere it should never have kept.

No test here runs a real binary. What each adapter sends is checked by
capturing the argument list; what comes back is checked against the fixture.
"""

import pathlib
import unittest

import _path  # noqa: F401  -- import side effect: puts scripts/ on sys.path

from kyrio import capability, config, probe, providers, scm
from kyrio.providers import github

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
DIFF = (FIXTURES / "pr_diff.txt").read_text(encoding="utf-8")
LISTING = (FIXTURES / "pr_list.json").read_text(encoding="utf-8")


def resolution(transport="cli", adapter=github):
    return capability.Resolution(
        "scm", capability.CONFIGURED, transport=transport,
        provider=getattr(adapter, "ID", None), adapter=adapter)


def answering(output, outcome=probe.ANSWERED, detail=""):
    """A runner that records what it was asked to run."""
    calls = []

    def run(argv, cwd=None, **kw):
        calls.append((list(argv), cwd))
        return probe.Ran(outcome, argv, output, detail)

    run.calls = calls
    return run


class TestSummarize(unittest.TestCase):
    """A unified diff is a format, not a provider's format.

    Counted here rather than per adapter, which would be the same arithmetic
    copied once per provider and drifting from the first copy onward.
    """

    def test_files_are_counted_from_their_headers(self):
        self.assertEqual(scm.summarize(DIFF)["files"], 3)

    def test_added_and_removed_lines_are_counted(self):
        counts = scm.summarize(DIFF)
        self.assertEqual(counts["added"], 16)
        self.assertEqual(counts["removed"], 2)

    def test_the_file_markers_are_not_counted_as_changes(self):
        """Every file in a diff carries a ``+++`` and a ``---`` line. Counting
        those inflates every change by twice its file count."""
        one = "diff --git a/x b/x\n--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new\n"
        counts = scm.summarize(one)
        self.assertEqual((counts["files"], counts["added"], counts["removed"]),
                         (1, 1, 1))

    def test_an_empty_diff_counts_nothing(self):
        self.assertEqual(scm.summarize(""),
                         {"files": 0, "added": 0, "removed": 0})


class TestIdentifier(unittest.TestCase):
    def test_a_number(self):
        self.assertEqual(github.pr_identifier("4821"), "4821")

    def test_a_number_written_the_way_people_write_it(self):
        self.assertEqual(github.pr_identifier(" #4821 "), "4821")

    def test_anything_that_is_not_one(self):
        for text in ("", None, "abc", "48-21", "0", "1.2", "I8473b95934b5"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    github.pr_identifier(text)

    def test_the_refusal_says_what_the_shape_is(self):
        with self.assertRaises(ValueError) as caught:
            github.pr_identifier("main")
        self.assertIn("number", str(caught.exception))


class TestPrDiff(unittest.TestCase):
    def test_the_diff_comes_back_verbatim(self):
        run = answering(DIFF)
        result = scm.pr_diff(resolution(), "4821", runner=run)
        self.assertEqual(result.payload, DIFF)
        self.assertEqual(result.kind, "diff")

    def test_the_header_says_how_large_the_change_is(self):
        result = scm.pr_diff(resolution(), "4821", runner=answering(DIFF))
        self.assertEqual(result.meta["files"], 3)
        self.assertEqual(result.meta["provider"], "github")
        self.assertEqual(result.meta["id"], "4821")

    def test_the_argument_list_is_what_the_tool_expects(self):
        run = answering(DIFF)
        scm.pr_diff(resolution(), "#4821", runner=run)
        argv, _ = run.calls[0]
        self.assertEqual(argv, ["gh", "pr", "diff", "4821"])

    def test_the_working_directory_is_carried_through(self):
        """The tool works out which repository it is talking about from the
        directory it starts in, so a diff fetched from the wrong one is not an
        error -- it is a different change."""
        run = answering(DIFF)
        scm.pr_diff(resolution(), "4821", cwd="/somewhere", runner=run)
        self.assertEqual(run.calls[0][1], "/somewhere")

    def test_an_identifier_the_host_would_not_recognize_never_runs(self):
        run = answering(DIFF)
        with self.assertRaises(scm.ScmError):
            scm.pr_diff(resolution(), "main", runner=run)
        self.assertEqual(run.calls, [], "nothing should have been launched")

    def test_a_tool_that_is_not_installed(self):
        run = answering("", outcome=probe.MISSING, detail="not found")
        with self.assertRaises(scm.ScmError) as caught:
            scm.pr_diff(resolution(), "4821", runner=run)
        self.assertIn("not installed", caught.exception.message)

    def test_a_tool_that_refuses_keeps_its_own_words(self):
        """A change that does not exist and a credential that expired are both
        a non-zero exit. Only the message separates them, so it is carried
        rather than replaced."""
        run = answering("could not resolve to a PullRequest",
                        outcome=probe.FAILED, detail="exit 1")
        with self.assertRaises(scm.ScmError) as caught:
            scm.pr_diff(resolution(), "4821", runner=run)
        self.assertIn("could not resolve", caught.exception.detail)

    def test_an_empty_answer_is_a_failure_rather_than_an_empty_result(self):
        """An empty payload reads as a change with no content, which is a
        thing somebody would act on."""
        with self.assertRaises(scm.ScmError):
            scm.pr_diff(resolution(), "4821", runner=answering("   \n"))


class TestLog(unittest.TestCase):
    def test_every_change_is_listed(self):
        run = answering(LISTING)
        result = scm.log(resolution(), "2026-08-24", "7 days", runner=run)
        self.assertEqual(result.meta["changes"], 3)
        self.assertIn("4821", result.payload)
        self.assertIn("author-one", result.payload)

    def test_the_argument_list_asks_for_the_fields_by_name(self):
        """Taking the default shape would let a field added upstream widen the
        payload without anyone deciding to."""
        run = answering(LISTING)
        scm.log(resolution(), "2026-08-24", "7 days", runner=run)
        argv = run.calls[0][0]
        self.assertEqual(argv[:3], ["gh", "pr", "list"])
        self.assertIn("--json", argv)
        self.assertIn("merged:>=2026-08-24", argv)

    def test_a_date_is_kept_and_a_timestamp_is_not(self):
        """A time to the second is noise in a list somebody is scanning, and
        the exact value is in the host anyway."""
        records = github.parse_log(LISTING)
        self.assertEqual(records[0]["at"], "2026-08-28")

    def test_a_change_nobody_merged_by_hand_has_no_author(self):
        """An automated merge genuinely has none. "unknown" would read as a
        person somebody could go and find."""
        records = github.parse_log(LISTING)
        self.assertEqual(records[2]["author"], "")

    def test_nothing_merged_is_an_answer_rather_than_a_failure(self):
        """Exactly what somebody asking "what shipped" needs to hear. An error
        here would send them looking for a broken tool."""
        result = scm.log(resolution(), "2026-08-24", "7 days",
                         runner=answering("[]"))
        self.assertEqual(result.meta["changes"], 0)
        self.assertIn("nothing merged", result.payload)

    def test_output_that_is_not_the_listing_is_refused_with_what_came_back(self):
        run = answering("Warning: something happened\nnot json at all")
        with self.assertRaises(scm.ScmError) as caught:
            scm.log(resolution(), "2026-08-24", "7 days", runner=run)
        self.assertIn("JSON", caught.exception.message)
        self.assertIn("not json at all", caught.exception.detail)

    def test_a_listing_of_the_wrong_shape_is_refused(self):
        with self.assertRaises(scm.ScmError):
            scm.log(resolution(), "2026-08-24", "7 days",
                    runner=answering('{"number": 1}'))

    def test_every_record_carries_the_shared_keys(self):
        """One rendering is written against this shape, for every adapter."""
        for record in github.parse_log(LISTING):
            self.assertEqual(set(record), set(scm.LOG_KEYS))

    def test_a_tool_that_is_not_installed(self):
        run = answering("", outcome=probe.MISSING, detail="not found")
        with self.assertRaises(scm.ScmError) as caught:
            scm.log(resolution(), "2026-08-24", "7 days", runner=run)
        self.assertIn("not installed", caught.exception.message)


class TestManualTransport(unittest.TestCase):
    def test_manual_is_recognized_from_the_resolution(self):
        self.assertTrue(scm.requires_manual(resolution(transport="manual")))
        self.assertFalse(scm.requires_manual(resolution()))

    def test_the_instructions_end_at_the_inbound_door(self):
        """Where a person is the transport, what they bring back is data the
        broker did not produce, and there is one door for that (S3)."""
        text = scm.manual_diff_instructions("4821")
        self.assertIn("kyrio ingest text --file", text)
        self.assertIn("4821", text)

    def test_they_name_no_product(self):
        """Read on a machine whose tooling this pack has never heard of."""
        text = scm.manual_diff_instructions("4821").lower()
        for adapter in providers.ADAPTERS.values():
            with self.subTest(adapter=adapter.ID):
                self.assertNotIn(adapter.ID, text)
                self.assertNotIn(adapter.BINARY, text.split())

    def test_they_do_not_ask_for_a_shortened_diff(self):
        self.assertIn("Do not shorten", scm.manual_diff_instructions("1"))

    def test_the_log_instructions_also_end_at_the_door(self):
        text = scm.manual_log_instructions("2026-08-24", "7 days")
        self.assertIn("kyrio ingest text --file", text)
        self.assertIn("2026-08-24", text)


class TestAdapterContractForScm(unittest.TestCase):
    """Applies to every adapter serving this capability, written or not."""

    def test_each_can_name_and_fetch_a_change(self):
        for adapter in providers.for_capability("scm"):
            for name in ("pr_identifier", "pr_diff", "log", "parse_log"):
                with self.subTest(adapter=adapter.ID, member=name):
                    self.assertTrue(callable(getattr(adapter, name, None)))

    def test_each_refuses_an_identifier_it_cannot_use(self):
        for adapter in providers.for_capability("scm"):
            with self.subTest(adapter=adapter.ID):
                with self.assertRaises(ValueError):
                    adapter.pr_identifier("")

    def test_scm_is_a_capability_the_broker_knows(self):
        self.assertIn("scm", config.CAPABILITIES)
        self.assertNotIn("scm", config.INTRINSIC)


if __name__ == "__main__":
    unittest.main()
