"""Command line entry point: `icslint some-file.ics`."""

import argparse
import sys

from .parser import IcsError, parse_document, split_physical_lines, validate


def format_error(err, filename, physical_lines):
    header = f"{filename}:{err.pos}: error: {err.message}"
    line_index = err.pos.line - 1
    if 0 <= line_index < len(physical_lines):
        source = physical_lines[line_index]
        caret_col = min(max(err.pos.column - 1, 0), len(source))
        pointer = " " * caret_col + "^"
        return f"{header}\n  {source}\n  {pointer}"
    return header


def lint_file(path):
    try:
        with open(path, "r", newline="", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as err:
        print(f"icslint: {path}: {err.strerror}", file=sys.stderr)
        return 2

    physical_lines = split_physical_lines(text)

    try:
        root = parse_document(text)
    except IcsError as err:
        print(format_error(err, path, physical_lines), file=sys.stderr)
        return 1

    errors = validate(root)
    for err in errors:
        print(format_error(err, path, physical_lines), file=sys.stderr)
    return 1 if errors else 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="icslint",
        description="Check an .ics (iCalendar) file for structural errors.",
    )
    parser.add_argument("path", help="path to an .ics file")
    args = parser.parse_args(argv)
    return lint_file(args.path)


if __name__ == "__main__":
    sys.exit(main())
