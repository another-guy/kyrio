"""The single inbound door (S3).

Every test here is about the door refusing something, or about it accepting
something and saying exactly what it accepted. There is no test asserting that
a payload survives unchanged by accident: the identity normalizer is the
current behaviour, so it is asserted deliberately.
"""

import contextlib
import io
import json
import pathlib
import shutil
import tempfile
import unittest

import _path  # noqa: F401  -- import side effect: puts scripts/ on sys.path

from kyrio import __main__ as main_module
from kyrio import ingest


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.root = pathlib.Path(
            tempfile.mkdtemp(prefix="kyrio-ingest-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def write(self, name, content):
        path = self.root / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_bytes(content.encode("utf-8"))
        return path


class TestAccepts(Sandbox):
    def test_a_known_kind_comes_back_verbatim(self):
        path = self.write("note.txt", "one\ntwo\n")
        result = ingest.ingest("text", path)
        self.assertEqual(result.kind, "text")
        self.assertEqual(result.payload, "one\ntwo\n")

    def test_it_is_labelled_as_foreign(self):
        """A consumer must never confuse this with something we produced."""
        result = ingest.ingest("text", self.write("note.txt", "x\n"))
        self.assertEqual(result.meta["origin"], "external")

    def test_line_endings_arrive_in_one_form(self):
        path = self.write("crlf.txt", "one\r\ntwo\r\n")
        self.assertEqual(ingest.ingest("text", path).payload, "one\ntwo\n")

    def test_a_lone_carriage_return_is_a_line_ending_too(self):
        path = self.write("cr.txt", "one\rtwo\r")
        self.assertEqual(ingest.ingest("text", path).payload, "one\ntwo\n")

    def test_bytes_are_the_bytes_on_disk_not_the_decoded_length(self):
        path = self.write("wide.txt", "éé\n")
        self.assertEqual(ingest.ingest("text", path).meta["bytes"], 5)

    def test_lines_are_counted_with_and_without_a_trailing_newline(self):
        for content, expected in (("a\nb\n", 2), ("a\nb", 2), ("a", 1),
                                  ("", 0)):
            with self.subTest(content=content):
                path = self.write("counted.txt", content)
                self.assertEqual(
                    ingest.ingest("text", path).meta["lines"], expected)

    def test_an_empty_file_is_accepted_not_refused(self):
        result = ingest.ingest("text", self.write("empty.txt", ""))
        self.assertEqual(result.payload, "")
        self.assertEqual(result.meta["bytes"], 0)

    def test_a_byte_that_will_not_decode_does_not_lose_the_file(self):
        path = self.write("mixed.txt", b"before\n\xff\nafter\n")
        payload = ingest.ingest("text", path).payload
        self.assertIn("before", payload)
        self.assertIn("after", payload)

    def test_reading_writes_nothing(self):
        path = self.write("note.txt", "x\n")
        before = sorted(p.name for p in self.root.iterdir())
        ingest.ingest("text", path)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), before)
        self.assertEqual(path.read_bytes(), b"x\n")


class TestRefuses(Sandbox):
    def test_an_unknown_kind_names_the_known_ones(self):
        path = self.write("note.txt", "x\n")
        with self.assertRaises(ingest.IngestError) as ctx:
            ingest.ingest("invented", path)
        self.assertIn("invented", str(ctx.exception))
        for kind in ingest.KINDS:
            self.assertIn(kind, str(ctx.exception))

    def test_no_kind_is_a_usage_error(self):
        with self.assertRaises(ingest.IngestError) as ctx:
            ingest.ingest(None, self.write("note.txt", "x\n"))
        self.assertIn("usage", str(ctx.exception))

    def test_no_file_is_a_usage_error(self):
        with self.assertRaises(ingest.IngestError) as ctx:
            ingest.ingest("text", None)
        self.assertIn("--file", str(ctx.exception))

    def test_a_missing_file_says_so(self):
        with self.assertRaises(ingest.IngestError) as ctx:
            ingest.ingest("text", self.root / "absent.txt")
        self.assertIn("no such file", str(ctx.exception))

    def test_a_directory_is_not_a_file(self):
        with self.assertRaises(ingest.IngestError) as ctx:
            ingest.ingest("text", self.root)
        self.assertIn("directory", str(ctx.exception))

    def test_binary_content_is_refused(self):
        path = self.write("image.bin", b"\x89PNG\r\n\x1a\n\x00\x00")
        with self.assertRaises(ingest.IngestError) as ctx:
            ingest.ingest("text", path)
        self.assertIn("not text", str(ctx.exception))

    def test_oversize_reports_both_numbers_and_does_not_truncate(self):
        """Truncating text someone is about to reason from is the worst
        available behaviour, so the door refuses and says the two numbers."""
        path = self.write("big.txt", "x" * 200)
        with self.assertRaises(ingest.IngestError) as ctx:
            ingest.ingest("text", path, max_bytes=100)
        message = str(ctx.exception)
        self.assertIn("200", message)
        self.assertIn("100", message)

    def test_the_limit_is_a_real_bound(self):
        self.assertGreater(ingest.MAX_BYTES, 0)


class TestRegistry(Sandbox):
    def test_a_kind_is_looked_up_never_guessed(self):
        called = []

        def normalizer(body):
            called.append(body)
            return body.upper()

        path = self.write("note.txt", "quiet\n")
        result = ingest.ingest("shout", path, kinds={"shout": normalizer})
        self.assertEqual(called, ["quiet\n"])
        self.assertEqual(result.payload, "QUIET\n")

    def test_the_shipped_registry_holds_only_what_has_a_caller(self):
        """A normalizer written before its consumer encodes a guess (I8)."""
        self.assertEqual(sorted(ingest.KINDS), ["text"])

    def test_the_guard_is_the_only_transformation_point(self):
        """D1 attaches at one call site, so it must be on the path today."""
        original = ingest._guard
        self.addCleanup(setattr, ingest, "_guard", original)
        ingest._guard = lambda body: "[guarded]"
        result = ingest.ingest("text", self.write("note.txt", "secret\n"))
        self.assertEqual(result.payload, "[guarded]")


class TestEndToEnd(Sandbox):
    def run_kyrio(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main_module.main(list(argv))
        head, _, body = buf.getvalue().partition("\n")
        payload = body[len("---\n"):] if body.startswith("---\n") else None
        return code, json.loads(head), payload

    def test_a_file_arrives_framed_and_labelled(self):
        path = self.write("note.txt", "one\ntwo\n")
        code, header, payload = self.run_kyrio("ingest", "text",
                                               "--file", str(path))
        self.assertEqual(code, 0)
        self.assertEqual(header["status"], "ok")
        self.assertEqual(header["kind"], "text")
        self.assertEqual(header["source"], {"transport": "ingest"})
        self.assertEqual(header["origin"], "external")
        self.assertEqual(payload, "one\ntwo\n")

    def test_an_unknown_kind_exits_one_and_lists_the_known(self):
        path = self.write("note.txt", "x\n")
        code, header, _ = self.run_kyrio("ingest", "invented",
                                         "--file", str(path))
        self.assertEqual(code, 1)
        self.assertEqual(header["status"], "error")
        self.assertEqual(header["known"], sorted(ingest.KINDS))

    def test_a_missing_file_flag_exits_one(self):
        code, header, _ = self.run_kyrio("ingest", "text")
        self.assertEqual(code, 1)
        self.assertIn("--file", header["message"])

    def test_the_door_is_listed_in_help(self):
        _, _, payload = self.run_kyrio("help")
        self.assertIn("kyrio ingest", payload)


if __name__ == "__main__":
    unittest.main()
