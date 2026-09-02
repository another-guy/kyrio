"""The scm capability: one change, its diff, and every way that can fail.

The diff fixture is hand-written (I8). A real one carries branch names, author
names, and internal paths, and capturing one is how a repository acquires a
payload from somewhere it should never have kept.

No test here runs a real binary. What each adapter sends is checked by
capturing the argument list; what comes back is checked against the fixture.
"""

import pathlib
import tempfile
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


def scripted(*answers):
    """A runner that answers each call differently, in order."""
    calls = []
    remaining = list(answers)

    def run(argv, cwd=None, **kw):
        calls.append((list(argv), cwd))
        output, outcome = remaining.pop(0) if remaining else ("", probe.ANSWERED)
        return probe.Ran(outcome, argv, output, "exit 1"
                         if outcome == probe.FAILED else "")

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

    def test_a_directory_that_does_not_exist_does_not_blame_the_tool(self):
        """Reported as a missing binary, this says an installed tool is not
        installed, and the reader goes looking for an installation problem
        they do not have."""
        run = answering("", outcome=probe.NO_CWD,
                        detail="no such directory: /nowhere")
        with self.assertRaises(scm.ScmError) as caught:
            scm.pr_diff(resolution(), "4821", runner=run)
        message = caught.exception.message
        self.assertIn("/nowhere", message)
        self.assertNotIn("not installed", message)
        self.assertNotIn(github.BINARY, message)

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


class CommentSandbox(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="kyrio-comment-"))
        self.addCleanup(_remove, self.dir)
        self.body = self.dir / "note.md"
        self.body.write_text("Consider asserting this instead.\n",
                             encoding="utf-8")

    def comment(self, identifier="4821", path="src/api.py", line=88):
        return scm.read_comment(identifier, path, line, str(self.body))


class TestReadComment(CommentSandbox):
    def test_the_body_comes_through_the_inbound_door(self):
        """It is a file the broker did not produce, which is what ``ingest``
        is for -- and the bound matters most on the way out, where something
        is about to be published under the user's name (S3)."""
        self.assertEqual(self.comment().body,
                         "Consider asserting this instead.\n")

    def test_a_line_that_is_not_a_line_number(self):
        for line in ("zero", "", None, "0", "-4", "1.5"):
            with self.subTest(line=line):
                with self.assertRaises(scm.ScmError):
                    self.comment(line=line)

    def test_a_comment_with_no_file_to_attach_to(self):
        with self.assertRaises(scm.ScmError):
            scm.read_comment("4821", "", 88, str(self.body))

    def test_an_empty_body_is_refused(self):
        self.body.write_text("   \n\n", encoding="utf-8")
        with self.assertRaises(scm.ScmError) as caught:
            self.comment()
        self.assertIn("nothing to say", caught.exception.message)

    def test_a_body_file_that_is_not_there(self):
        with self.assertRaises(scm.ScmError):
            scm.read_comment("4821", "src/api.py", 88,
                             str(self.dir / "absent.md"))


class TestDraft(CommentSandbox):
    def test_drafting_sends_nothing_and_runs_nothing(self):
        """The safe path is the default one, and it must not even need the
        tool: a draft on a machine where nothing is installed still works."""
        run = answering("")
        result = scm.pr_comment(resolution(), self.comment(), str(self.body),
                                runner=run)
        self.assertEqual(run.calls, [])
        self.assertFalse(result.meta["posted"])

    def test_the_draft_shows_where_it_would_land(self):
        payload = scm.pr_comment(resolution(), self.comment(), str(self.body),
                                 runner=answering("")).payload
        self.assertIn("4821", payload)
        self.assertIn("src/api.py", payload)
        self.assertIn("88", payload)
        self.assertIn("Consider asserting this instead.", payload)

    def test_an_identifier_the_host_would_reject_stops_at_the_draft(self):
        """Better to refuse before the user reads a draft that could never be
        sent than after they have approved it."""
        with self.assertRaises(scm.ScmError):
            scm.pr_comment(resolution(), self.comment(identifier="main"),
                           str(self.body), runner=answering(""))


class TestPost(CommentSandbox):
    HEAD = '{"headRefOid": "0f1e2d3c4b5a69788796a5b4c3d2e1f009876543"}'
    LANDED = '{"html_url": "https://example.com/pulls/4821#discussion_r1"}'

    def test_posting_names_the_commit_the_change_ends_at(self):
        """A line comment has to name a commit, and the caller does not know
        it, so it is fetched first."""
        run = scripted((self.HEAD, probe.ANSWERED),
                       (self.LANDED, probe.ANSWERED))
        scm.pr_comment(resolution(), self.comment(), str(self.body),
                       post=True, runner=run)
        first, second = run.calls[0][0], run.calls[1][0]
        self.assertEqual(first[:3], ["gh", "pr", "view"])
        self.assertIn("--json", first)
        self.assertEqual(second[:2], ["gh", "api"])
        self.assertIn("--method", second)
        self.assertIn("POST", second)

    def test_the_body_travels_as_a_file(self):
        """Prose with newlines and quoting in it is the wrong thing to be
        fighting the platform's argument rules over."""
        run = scripted((self.HEAD, probe.ANSWERED),
                       (self.LANDED, probe.ANSWERED))
        scm.pr_comment(resolution(), self.comment(), str(self.body),
                       post=True, runner=run)
        self.assertIn("body=@%s" % self.body, run.calls[1][0])

    def test_the_repository_is_left_for_the_tool_to_work_out(self):
        run = scripted((self.HEAD, probe.ANSWERED),
                       (self.LANDED, probe.ANSWERED))
        scm.pr_comment(resolution(), self.comment(), str(self.body),
                       post=True, runner=run)
        self.assertIn("repos/{owner}/{repo}/pulls/4821/comments",
                      run.calls[1][0])

    def test_where_it_landed_is_reported(self):
        run = scripted((self.HEAD, probe.ANSWERED),
                       (self.LANDED, probe.ANSWERED))
        result = scm.pr_comment(resolution(), self.comment(), str(self.body),
                                post=True, runner=run)
        self.assertTrue(result.meta["posted"])
        self.assertIn("discussion_r1", result.payload)

    def test_a_failure_fetching_the_commit_sends_nothing(self):
        """The important safety property: the first call failing must not
        leave the second one to run against a guess."""
        run = scripted(("no such pull request", probe.FAILED))
        with self.assertRaises(scm.ScmError):
            scm.pr_comment(resolution(), self.comment(), str(self.body),
                           post=True, runner=run)
        self.assertEqual(len(run.calls), 1)

    def test_a_change_with_no_commit_reported_sends_nothing(self):
        run = scripted(("{}", probe.ANSWERED))
        with self.assertRaises(scm.ScmError):
            scm.pr_comment(resolution(), self.comment(), str(self.body),
                           post=True, runner=run)
        self.assertEqual(len(run.calls), 1)

    def test_an_answer_without_a_location_is_still_a_success(self):
        """Where it landed is useful, not essential. The comment was sent."""
        run = scripted((self.HEAD, probe.ANSWERED), ("", probe.ANSWERED))
        result = scm.pr_comment(resolution(), self.comment(), str(self.body),
                                post=True, runner=run)
        self.assertTrue(result.meta["posted"])


class TestManualComment(CommentSandbox):
    def test_the_instructions_carry_the_comment_verbatim(self):
        text = scm.manual_comment_instructions(self.comment())
        self.assertIn("Consider asserting this instead.", text)
        self.assertIn("src/api.py", text)

    def test_they_say_plainly_that_nothing_was_sent(self):
        self.assertIn("Nothing was sent",
                      scm.manual_comment_instructions(self.comment()))


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


class TestMoreThanOneBinary(unittest.TestCase):
    """An adapter may reach a second program to finish a verb, and the second
    adapter does. What that must not do is confuse whose failure it was."""

    def adapter(self, **members):
        """A stand-in adapter, so these test the capability rather than any
        provider that happens to ship."""
        return type("Stub", (), dict(
            ID="stub", BINARY="one", CAPABILITIES=("scm",), TRANSPORT="cli",
            pr_identifier=staticmethod(lambda text: text.strip()),
            **members))

    def test_the_program_named_is_the_one_that_ran(self):
        """The adapter is named for the first binary; the failure came from
        the second. Naming the first sends somebody to install what they have
        already got."""
        adapter = self.adapter(
            pr_diff=staticmethod(lambda run, i, cwd=None: probe.Ran(
                probe.MISSING, ["two", "diff"], detail="not found")))
        with self.assertRaises(scm.ScmError) as caught:
            scm.pr_diff(resolution(adapter=adapter), "4821")
        self.assertIn("two", caught.exception.message)
        self.assertNotIn("one", caught.exception.message)

    def test_a_failure_with_no_command_falls_back_to_the_adapter(self):
        adapter = self.adapter(
            pr_diff=staticmethod(lambda run, i, cwd=None: probe.Ran(
                probe.MISSING, [], detail="not found")))
        with self.assertRaises(scm.ScmError) as caught:
            scm.pr_diff(resolution(adapter=adapter), "4821")
        self.assertIn("one", caught.exception.message)


class TestAVerbAnAdapterDoesNotServe(unittest.TestCase):
    """Hosts differ in what their tooling can do. A gap is an ordinary answer,
    not an attribute error raised where nobody can read it."""

    def adapter(self):
        return type("Stub", (), dict(
            ID="stub", BINARY="one", CAPABILITIES=("scm",), TRANSPORT="cli",
            pr_identifier=staticmethod(lambda text: text.strip())))

    def test_it_is_reported_rather_than_raised(self):
        with self.assertRaises(scm.ScmError) as caught:
            scm.pr_diff(resolution(adapter=self.adapter()), "4821")
        self.assertIn("stub", caught.exception.message)
        self.assertIn("pr diff", caught.exception.message)

    def test_it_names_the_verb_that_was_asked_for(self):
        """Posting resolves a head revision first. Somebody told that
        `pr head` is unavailable has been handed an implementation detail."""
        with self.assertRaises(scm.ScmError) as caught:
            scm.pr_comment(resolution(adapter=self.adapter()),
                           scm.Comment("4821", "x.py", 3, "body"),
                           "body.txt", post=True)
        self.assertIn("pr comment", caught.exception.message)
        self.assertNotIn("pr head", caught.exception.message)


class TestATruncatedListingIsRefused(unittest.TestCase):
    """Every listing verb has a ceiling. A listing sitting exactly on it is
    either the whole window or as much of it as fitted, and the response looks
    the same either way."""

    LIMIT = 3

    def records(self, *dates):
        return [{"id": str(n), "at": at, "author": "", "title": "t"}
                for n, at in enumerate(dates)]

    def test_a_full_listing_that_never_reached_the_window_is_refused(self):
        """Three came back, three was the most it would return, and the oldest
        is still inside the window. What merged before it is missing."""
        with self.assertRaises(scm.ScmError) as caught:
            scm._refuse_if_cut_short(
                self.records("2026-08-28", "2026-08-27", "2026-08-26"),
                "2026-08-01", self.LIMIT)
        self.assertIn("2026-08-01", caught.exception.message)
        self.assertIn(str(self.LIMIT), caught.exception.message)

    def test_a_full_listing_that_passed_the_window_is_complete(self):
        """The oldest is older than the window's start, so the window closed
        before the listing did."""
        scm._refuse_if_cut_short(
            self.records("2026-08-28", "2026-08-27", "2026-07-15"),
            "2026-08-01", self.LIMIT)

    def test_a_short_listing_is_never_truncated(self):
        scm._refuse_if_cut_short(self.records("2026-08-28"),
                                 "2026-08-01", self.LIMIT)

    def test_nothing_to_check_without_a_window(self):
        scm._refuse_if_cut_short(
            self.records("2026-08-28", "2026-08-27", "2026-08-26"),
            "", self.LIMIT)

    def test_an_adapter_with_no_stated_ceiling_is_not_second_guessed(self):
        scm._refuse_if_cut_short(
            self.records("2026-08-28", "2026-08-27", "2026-08-26"),
            "2026-08-01", None)

    def test_it_refuses_rather_than_returning_a_short_answer(self):
        """End to end, because the value of this is that `scm log` does not
        hand back a list that looks complete when it is not."""
        listing = """[
          {"number": 3, "mergedAt": "2026-08-28T00:00:00Z", "title": "c",
           "author": {"login": "a"}},
          {"number": 2, "mergedAt": "2026-08-27T00:00:00Z", "title": "b",
           "author": {"login": "a"}},
          {"number": 1, "mergedAt": "2026-08-26T00:00:00Z", "title": "a",
           "author": {"login": "a"}}
        ]"""
        adapter = type("Stub", (), dict(
            ID="stub", BINARY="one", CAPABILITIES=("scm",), TRANSPORT="cli",
            LOG_LIMIT=3,
            log=staticmethod(
                lambda run, since, cwd=None: run(["one", "list"], cwd=cwd)),
            parse_log=staticmethod(github.parse_log)))
        with self.assertRaises(scm.ScmError):
            scm.log(resolution(adapter=adapter), "2026-01-01", "8 months",
                    runner=answering(listing))


class TestTheWindowIsKeptInOnePlace(unittest.TestCase):
    """One host filters by date in its query; another's listing verb has no
    date parameter at all. A window honoured by one and ignored by the other
    is exactly the difference this layer exists to erase."""

    def records(self, *dates):
        return [{"id": str(n), "at": at, "author": "", "title": "t"}
                for n, at in enumerate(dates)]

    def test_what_merged_before_the_window_is_dropped(self):
        kept = scm._within(self.records("2026-08-28", "2026-07-30"),
                           "2026-08-01")
        self.assertEqual([r["at"] for r in kept], ["2026-08-28"])

    def test_the_boundary_is_included(self):
        kept = scm._within(self.records("2026-08-01"), "2026-08-01")
        self.assertEqual(len(kept), 1)

    def test_a_record_with_no_date_is_kept(self):
        """It cannot be shown to fall outside the window, and dropping it
        would hide a change on the strength of a missing field."""
        kept = scm._within(self.records(""), "2026-08-01")
        self.assertEqual(len(kept), 1)

    def test_no_window_keeps_everything(self):
        given = self.records("2026-01-01", "")
        self.assertEqual(scm._within(given, ""), given)


class TestAdapterContractForScm(unittest.TestCase):
    """Applies to every adapter serving this capability, written or not."""

    #: Reading is the floor. An adapter that cannot name a change, fetch its
    #: diff, and say what merged cannot serve this capability at all.
    READ = ("pr_identifier", "pr_diff", "log", "parse_log")

    #: Writing back is optional, because hosts genuinely differ in whether
    #: their tooling can do it -- but it is all or nothing. Half of it is a
    #: draft that can never be posted, which is worse than a gap named as one.
    WRITE = ("pr_head", "parse_head", "pr_comment", "parse_comment")

    def test_each_can_name_and_fetch_a_change(self):
        for adapter in providers.for_capability("scm"):
            for name in self.READ:
                with self.subTest(adapter=adapter.ID, member=name):
                    self.assertTrue(callable(getattr(adapter, name, None)))

    def test_writing_back_is_all_of_it_or_none_of_it(self):
        for adapter in providers.for_capability("scm"):
            served = [name for name in self.WRITE
                      if callable(getattr(adapter, name, None))]
            with self.subTest(adapter=adapter.ID):
                self.assertIn(
                    len(served), (0, len(self.WRITE)),
                    "%s serves %s of %s: a change can be drafted against it "
                    "and never sent" % (adapter.ID, len(served),
                                        len(self.WRITE)))

    def test_each_says_where_its_listing_stops(self):
        """Without it the capability cannot tell a complete answer from a
        truncated one, and a truncated one is indistinguishable from the
        truth."""
        for adapter in providers.for_capability("scm"):
            with self.subTest(adapter=adapter.ID):
                limit = getattr(adapter, "LOG_LIMIT", None)
                self.assertIsInstance(limit, int)
                self.assertGreater(limit, 0)

    def test_each_refuses_an_identifier_it_cannot_use(self):
        for adapter in providers.for_capability("scm"):
            with self.subTest(adapter=adapter.ID):
                with self.assertRaises(ValueError):
                    adapter.pr_identifier("")

    def test_scm_is_a_capability_the_broker_knows(self):
        self.assertIn("scm", config.CAPABILITIES)
        self.assertNotIn("scm", config.INTRINSIC)


def _remove(directory):
    import shutil
    shutil.rmtree(directory, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
