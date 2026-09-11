import io
import unittest
from unittest import mock

from icslint.cli import lint_file

VALID_DOCUMENT = (
    "BEGIN:VCALENDAR\n"
    "VERSION:2.0\n"
    "PRODID:-//test//icslint//EN\n"
    "BEGIN:VEVENT\n"
    "UID:1@example.com\n"
    "DTSTAMP:20260101T090000Z\n"
    "DTSTART:20260102T090000Z\n"
    "END:VEVENT\n"
    "END:VCALENDAR\n"
)


class LintFileStdinTests(unittest.TestCase):
    def test_dash_reads_from_stdin(self):
        with mock.patch("sys.stdin", io.StringIO(VALID_DOCUMENT)):
            code = lint_file("-")
        self.assertEqual(code, 0)

    def test_stdin_errors_are_reported_under_stdin_placeholder(self):
        with mock.patch("sys.stdin", io.StringIO("BEGIN:VEVENT\n")):
            with mock.patch("sys.stderr", io.StringIO()) as stderr:
                code = lint_file("-")
        self.assertEqual(code, 1)
        self.assertIn("<stdin>", stderr.getvalue())

    def test_missing_file_returns_2(self):
        with mock.patch("sys.stderr", io.StringIO()):
            code = lint_file("/no/such/path-icslint-test.ics")
        self.assertEqual(code, 2)


class StrictModeTests(unittest.TestCase):
    def test_valid_document_passes_strict_too(self):
        with mock.patch("sys.stdin", io.StringIO(VALID_DOCUMENT)):
            code = lint_file("-", strict=True)
        self.assertEqual(code, 0)

    def test_non_strict_mode_ignores_line_length(self):
        text = VALID_DOCUMENT.replace(
            "END:VEVENT\n", f"DESCRIPTION:{'x' * 80}\nEND:VEVENT\n"
        )
        with mock.patch("sys.stdin", io.StringIO(text)):
            code = lint_file("-", strict=False)
        self.assertEqual(code, 0)

    def test_strict_mode_reports_long_line(self):
        text = VALID_DOCUMENT.replace(
            "END:VEVENT\n", f"DESCRIPTION:{'x' * 80}\nEND:VEVENT\n"
        )
        with mock.patch("sys.stdin", io.StringIO(text)):
            with mock.patch("sys.stderr", io.StringIO()) as stderr:
                code = lint_file("-", strict=True)
        self.assertEqual(code, 1)
        self.assertIn("octets", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
