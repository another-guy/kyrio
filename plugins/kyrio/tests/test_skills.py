"""Every shipped skill is checked mechanically, before a model ever reads it.

The rules here are the authoring rules from ``docs/DESIGN.md`` section 6 that
can be checked without judgment: the frontmatter a plugin skill needs, the
compaction size budget, and the invariants a skill can violate silently.
"""

import pathlib
import re
import unittest

import _path  # noqa: F401  -- import side effect: puts scripts/ on sys.path

from kyrio import __main__ as main_module
from kyrio import ingest

PLUGIN = pathlib.Path(__file__).resolve().parent.parent
SKILLS = PLUGIN / "skills"

#: Auto-compaction re-attaches only the first ~5,000 tokens of a skill, within
#: a 25,000-token budget shared across every invoked skill. Four characters per
#: token is the usual rough conversion; the budget is deliberately generous so
#: it flags a runaway skill rather than an ordinary long one.
CHARACTER_BUDGET = 5000 * 4

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.S)

#: A line that starts with the broker is a command being given to the reader,
#: whether or not it sits in a fence. Prose mentioning the broker mid-sentence
#: does not match, and prose that opens a line with a command ought to be one.
BROKER_CALL_RE = re.compile(r"^\s*kyrio\s+(.+)$", re.M)

#: Read from the dispatch tables rather than restated, so the broker and the
#: skills cannot drift apart quietly.
KNOWN_NOUNS = set(main_module.COMMANDS) | {"help"}
KNOWN_VERBS = {
    "repo": set(main_module.REPO_VERBS),
    "probe": set(main_module.PROBE_VERBS),
    "config": {"explain"},
    "ingest": set(ingest.KINDS),
}


def skill_files():
    if not SKILLS.is_dir():
        return []
    return sorted(SKILLS.glob("*/SKILL.md"))


def frontmatter(text):
    """The frontmatter block as a dict of top-level keys.

    A deliberately small parser: these files use scalars and folded scalars
    only, and depending on a YAML library would break the stdlib-only rule.
    """
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    fields = {}
    key = None
    for line in match.group(1).splitlines():
        if line.startswith((" ", "\t")) and key:
            fields[key] = (fields[key] + " " + line.strip()).strip()
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        fields[key] = value.strip().lstrip(">|").strip()
    return fields


class TestSkills(unittest.TestCase):
    def setUp(self):
        self.skills = skill_files()
        if not self.skills:
            self.skipTest("no skills shipped yet")

    def test_each_has_frontmatter(self):
        for path in self.skills:
            with self.subTest(skill=path.parent.name):
                self.assertIsNotNone(
                    frontmatter(path.read_text(encoding="utf-8")))

    def test_the_name_matches_the_directory(self):
        for path in self.skills:
            fields = frontmatter(path.read_text(encoding="utf-8"))
            with self.subTest(skill=path.parent.name):
                self.assertEqual(fields.get("name"), path.parent.name)

    def test_the_description_states_a_boundary(self):
        """Twelve skills in one namespace is enough for triggering to misfire.

        A description that says only what a skill does will fire on the
        neighbouring workflow too, so each must also say what it is *not* for.
        """
        for path in self.skills:
            fields = frontmatter(path.read_text(encoding="utf-8"))
            description = fields.get("description", "")
            with self.subTest(skill=path.parent.name):
                self.assertGreater(len(description), 80)
                self.assertLessEqual(
                    len(description), 1536,
                    "the description budget is 1,536 characters")
                self.assertRegex(
                    description, r"\bnot\b",
                    "say what this skill is not for, not only what it does")

    def test_broker_calls_are_permitted_by_the_frontmatter(self):
        for path in self.skills:
            text = path.read_text(encoding="utf-8")
            fields = frontmatter(text)
            with self.subTest(skill=path.parent.name):
                if "kyrio " not in text:
                    continue
                self.assertIn("Bash(kyrio:*)", fields.get("allowed-tools", ""),
                              "a skill that calls the broker must say so")

    def test_within_the_compaction_budget(self):
        for path in self.skills:
            size = len(path.read_text(encoding="utf-8"))
            with self.subTest(skill=path.parent.name):
                self.assertLessEqual(
                    size, CHARACTER_BUDGET,
                    "only the first ~5,000 tokens survive compaction; move "
                    "the rest into references/")

    def test_every_broker_call_names_a_command_that_exists(self):
        """A typo here surfaces as a runtime error mid-workflow, which is a bad
        place to discover it. The dispatch tables are the source of truth, so a
        verb renamed in the broker fails here rather than in a session."""
        for path in self.skills:
            text = path.read_text(encoding="utf-8")
            for call in BROKER_CALL_RE.findall(text):
                words = [w for w in call.split()
                         if not w.startswith(("-", "<", "$"))]
                if not words:
                    continue
                noun, rest = words[0], words[1:]
                with self.subTest(skill=path.parent.name, call=call.strip()):
                    self.assertIn(noun, KNOWN_NOUNS)
                    if noun in KNOWN_VERBS and rest:
                        self.assertIn(rest[0], KNOWN_VERBS[noun])

    def test_no_skill_writes_settings_by_hand(self):
        """Every write belongs to a command, so re-running is reproducible."""
        for path in self.skills:
            text = path.read_text(encoding="utf-8")
            with self.subTest(skill=path.parent.name):
                self.assertNotIn("Write(", text)
                self.assertNotIn("Edit(", text)


if __name__ == "__main__":
    unittest.main()
