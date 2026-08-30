"""Argument parsing that cannot break the response protocol.

``argparse`` writes usage text straight to stdout or stderr and calls
``sys.exit(2)`` on a bad argument. Both are protocol violations here: a caller
would get unframed text and an exit code that means nothing in this program.

``Parser`` turns every one of those paths into a ``UsageError`` for the command
layer to hand to ``emit.error``, so the single chokepoint (S1) holds even when
the caller gets the arguments wrong.
"""

import argparse


class UsageError(Exception):
    """Bad arguments. Carries usage text when the parser produced any."""

    def __init__(self, message, usage=None):
        super().__init__(message)
        self.message = message
        self.usage = usage


class Parser(argparse.ArgumentParser):
    """An ``ArgumentParser`` that raises instead of printing and exiting.

    ``add_help`` is off by default: ``-h`` would otherwise print to stdout
    without a header. Commands document themselves through ``kyrio help``.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("add_help", False)
        super().__init__(*args, **kwargs)

    def error(self, message):
        raise UsageError(message, usage=self.format_usage().strip())

    def exit(self, status=0, message=None):
        raise UsageError(message or "argument parsing stopped early",
                         usage=self.format_usage().strip())

    # argparse writes here for warnings and for help; nothing may reach a
    # stream except through emit.
    def _print_message(self, message, file=None):
        return None


def table(headers, rows, indent="  ", right=()):
    """Fixed-width columns. The last column is never padded.

    Shared by every command that reports rows, so that two reports never drift
    into two different shapes. ``right`` names column indexes to right-align,
    which is what makes a ranked count scannable down the column.
    """
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(str(cell)))

    def line(cells):
        parts = []
        for i, cell in enumerate(cells):
            text = str(cell)
            if i in right:
                parts.append(text.rjust(widths[i]))
            elif i < len(headers) - 1:
                parts.append(text.ljust(widths[i]))
            else:
                parts.append(text)
        return (indent + "  ".join(parts)).rstrip()

    return "\n".join([line(headers)] + [line(r) for r in rows]) + "\n"
