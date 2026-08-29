import ast
import io
import json
import pathlib
import unittest

import _path  # noqa: F401  -- import side effect: puts scripts/ on sys.path

from kyrio import emit


def framed(header, payload=None):
    """Emit into a buffer and return (exit_code, header_dict, payload_text)."""
    buf = io.StringIO()
    code = emit._write(header, payload, stream=buf)
    lines = buf.getvalue().split("\n")
    parsed = json.loads(lines[0])
    body = None
    if len(lines) > 1 and lines[1] == emit.DELIMITER:
        body = "\n".join(lines[2:])
    return code, parsed, body


class TestFraming(unittest.TestCase):
    def test_header_is_exactly_one_line(self):
        buf = io.StringIO()
        emit._write({"status": "ok", "kind": "log"}, "a\nb\nc\n", stream=buf)
        first = buf.getvalue().split("\n")[0]
        self.assertEqual(first, '{"status":"ok","kind":"log"}')

    def test_multiline_string_in_header_stays_on_one_line(self):
        # json escapes the newline; the reader's single readline must still work.
        _, header, _ = framed({"status": "error", "message": "one\ntwo"})
        self.assertEqual(header["message"], "one\ntwo")

    def test_payload_is_verbatim(self):
        payload = "diff --git a/x b/x\n-  const r = f(req);\n+  const r = f(req, s);\n"
        _, _, body = framed({"status": "ok", "kind": "diff"}, payload)
        self.assertEqual(body, payload)

    def test_payload_without_trailing_newline_gets_one(self):
        buf = io.StringIO()
        emit._write({"status": "ok", "kind": "text"}, "no newline", stream=buf)
        self.assertTrue(buf.getvalue().endswith("no newline\n"))

    def test_no_payload_means_no_delimiter(self):
        buf = io.StringIO()
        emit._write(
            {"status": "unavailable", "capability": "kb",
             "remediation": "none configured"},
            None, stream=buf)
        self.assertNotIn(emit.DELIMITER, buf.getvalue())
        self.assertEqual(buf.getvalue().count("\n"), 1)

    def test_empty_payload_is_not_the_same_as_none(self):
        buf = io.StringIO()
        emit._write({"status": "ok", "kind": "log"}, "", stream=buf)
        self.assertIn(emit.DELIMITER, buf.getvalue())

    def test_non_ascii_payload_survives(self):
        text = "café → ok\n"
        _, _, body = framed({"status": "ok", "kind": "text"}, text)
        self.assertEqual(body, text)


class TestStatuses(unittest.TestCase):
    def test_only_error_exits_non_zero(self):
        cases = [("ok", 0), ("call", 0), ("manual", 0), ("unavailable", 0),
                 ("error", 1)]
        for status, expected in cases:
            with self.subTest(status=status):
                code, _, _ = framed({"status": status})
                self.assertEqual(code, expected)

    def test_unknown_status_is_a_programming_error(self):
        with self.assertRaises(ValueError):
            framed({"status": "failed"})

    def test_header_only_status_rejects_a_payload(self):
        for status in emit._HEADER_ONLY:
            with self.subTest(status=status):
                with self.assertRaises(ValueError):
                    framed({"status": status}, "payload")


class TestConstructors(unittest.TestCase):
    """Each constructor is checked for header shape, not just for not raising."""

    def emitted(self, fn, *args, **kwargs):
        buf = io.StringIO()
        real = emit._write
        captured = {}

        def spy(header, payload, stream=None):
            captured["header"] = header
            captured["payload"] = payload
            return real(header, payload, stream=buf)

        emit._write = spy
        try:
            code = fn(*args, **kwargs)
        finally:
            emit._write = real
        return code, captured["header"], captured["payload"]

    def test_ok_shape(self):
        code, header, payload = self.emitted(
            emit.ok, "diff", "d\n", transport="cli", files=12, insertions=83)
        self.assertEqual(code, 0)
        self.assertEqual(list(header)[:3], ["status", "kind", "source"])
        self.assertEqual(header["source"], {"transport": "cli"})
        self.assertEqual(header["files"], 12)
        self.assertEqual(payload, "d\n")

    def test_ok_omits_source_when_transport_unknown(self):
        _, header, _ = self.emitted(emit.ok, "text", "x")
        self.assertNotIn("source", header)

    def test_call_carries_tool_args_and_next(self):
        _, header, payload = self.emitted(
            emit.call, "ns__get_item", {"id": "PROJ-1234"},
            expect=["title", "state"])
        self.assertEqual(header["status"], "call")
        self.assertEqual(header["tool"], "ns__get_item")
        self.assertEqual(header["args"], {"id": "PROJ-1234"})
        self.assertEqual(header["expect"], ["title", "state"])
        self.assertTrue(header["next"])
        self.assertIsNone(payload)

    def test_call_omits_empty_expect(self):
        _, header, _ = self.emitted(emit.call, "ns__ping", {})
        self.assertNotIn("expect", header)

    def test_manual_carries_instructions_as_payload(self):
        _, header, payload = self.emitted(emit.manual, "obs", "1. Open ...\n")
        self.assertEqual(header["capability"], "obs")
        self.assertTrue(header["next"])
        self.assertEqual(payload, "1. Open ...\n")

    def test_unavailable_carries_remediation(self):
        code, header, payload = self.emitted(
            emit.unavailable, "kb", "no provider configured")
        self.assertEqual(code, 0)
        self.assertEqual(header["remediation"], "no provider configured")
        self.assertIsNone(payload)

    def test_error_exits_one_and_may_carry_detail(self):
        code, header, payload = self.emitted(
            emit.error, "expired auth", "stderr line\n", capability="ci")
        self.assertEqual(code, 1)
        self.assertEqual(header["message"], "expired auth")
        self.assertEqual(header["capability"], "ci")
        self.assertEqual(payload, "stderr line\n")


class TestChokepoint(unittest.TestCase):
    """S1: emit.py is the only module that writes to stdout."""

    SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"

    def offenders(self, tree):
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if isinstance(fn, ast.Name) and fn.id == "print":
                found.append("print()")
            elif (isinstance(fn, ast.Attribute)
                  and fn.attr in ("write", "writelines")
                  and isinstance(fn.value, ast.Attribute)
                  and fn.value.attr in ("stdout", "stderr")):
                found.append("sys.%s.%s()" % (fn.value.attr, fn.attr))
        return found

    def test_no_module_prints_except_emit(self):
        checked = 0
        for path in self.SCRIPTS.rglob("*.py"):
            if path.name == "emit.py":
                continue
            checked += 1
            tree = ast.parse(path.read_text(encoding="utf-8"))
            with self.subTest(module=path.name):
                self.assertEqual(
                    self.offenders(tree), [],
                    "%s writes output directly; route it through emit (S1)"
                    % path.name)
        self.assertGreater(checked, 0, "chokepoint test scanned nothing")


if __name__ == "__main__":
    unittest.main()
