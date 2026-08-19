"""Parsing for iCalendar (RFC 5545) content, with a position tracked back to
the original file for every logical line. The point of this module is that
IcsError always knows exactly where in the source it happened.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

NAME_RE = re.compile(r"[A-Za-z0-9-]+")


@dataclass(frozen=True)
class Pos:
    line: int
    column: int

    def __str__(self):
        return f"{self.line}:{self.column}"


class IcsError(Exception):
    """A syntax or structural problem, anchored to a line and column."""

    def __init__(self, message, pos):
        self.message = message
        self.pos = pos
        super().__init__(message)


@dataclass
class Property:
    name: str
    params: list
    value: str
    pos: Pos


@dataclass
class Component:
    name: str
    pos: Pos
    children: list = field(default_factory=list)  # Property | Component


def split_physical_lines(text):
    """Split text on CRLF or LF. The result has no trailing empty entry for
    a final line break, matching how most editors show line numbers."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.endswith("\n"):
        normalized = normalized[:-1]
    if normalized == "":
        return []
    return normalized.split("\n")


def unfold(text):
    """Turn raw file text into logical lines per RFC 5545 3.1: a line that
    starts with a single space or tab is a continuation of the previous one,
    and that leading whitespace is not part of the value.

    Returns a list of (value, positions) pairs, where positions[i] is the
    Pos of value[i] in the original file. Keeping a full position per
    character (rather than just per line) is what lets errors inside a
    folded, multi-line value still point at the right physical line.
    """
    physical = split_physical_lines(text)
    logical_lines = []
    current_chars = []
    current_positions = []

    def flush():
        if current_chars:
            logical_lines.append(("".join(current_chars), list(current_positions)))
            current_chars.clear()
            current_positions.clear()

    for line_no, raw in enumerate(physical, start=1):
        if raw[:1] in (" ", "\t") and current_chars:
            content = raw[1:]
            start_col = 2
        else:
            flush()
            content = raw
            start_col = 1
        for offset, ch in enumerate(content):
            current_chars.append(ch)
            current_positions.append(Pos(line_no, start_col + offset))
    flush()
    return logical_lines


def parse_content_line(value, positions):
    """Parse one unfolded logical line into (name, params, value).

    params is a list of (param_name, [param_value, ...]) pairs, in the order
    they appeared, since a property can legally repeat a parameter name.
    """
    n = len(value)

    def pos_at(idx):
        if idx < len(positions):
            return positions[idx]
        if positions:
            last = positions[-1]
            return Pos(last.line, last.column + 1)
        return Pos(1, 1)

    name_match = NAME_RE.match(value, 0)
    if not name_match:
        bad_char = value[0] if value else ""
        raise IcsError(f"expected a property name, found {bad_char!r}", pos_at(0))
    name = name_match.group(0)
    i = name_match.end()

    params = []
    while i < n and value[i] == ";":
        i += 1
        pname_match = NAME_RE.match(value, i)
        if not pname_match:
            raise IcsError("expected a parameter name after ';'", pos_at(i))
        pname = pname_match.group(0)
        i = pname_match.end()
        if i >= n or value[i] != "=":
            raise IcsError(f"expected '=' after parameter name {pname!r}", pos_at(i))
        i += 1

        pvalues = []
        while True:
            if i < n and value[i] == '"':
                start = i
                i += 1
                while i < n and value[i] != '"':
                    i += 1
                if i >= n:
                    raise IcsError("unterminated quoted parameter value", pos_at(start))
                pvalues.append(value[start + 1 : i])
                i += 1
            else:
                start = i
                while i < n and value[i] not in ",;:":
                    i += 1
                pvalues.append(value[start:i])
            if i < n and value[i] == ",":
                i += 1
                continue
            break
        params.append((pname, pvalues))

    if i >= n or value[i] != ":":
        raise IcsError(
            "content line is missing the ':' that separates the name/parameters "
            "from the value",
            pos_at(i),
        )
    i += 1
    return name, params, value[i:]


def parse_document(text):
    """Parse a whole .ics file into a tree of Component/Property nodes.

    Raises IcsError on the first structural problem: an unmatched END, a
    BEGIN that is never closed, or a content line that doesn't parse. This
    stops at the first error on purpose -- once BEGIN/END nesting is wrong,
    later "errors" are usually just noise caused by the first one.
    """
    logical_lines = unfold(text)

    root = Component(name="ROOT", pos=Pos(1, 1))
    stack = [root]

    for value, positions in logical_lines:
        if not value:
            continue
        name, params, prop_value = parse_content_line(value, positions)
        start_pos = positions[0]
        upper_name = name.upper()

        if upper_name == "BEGIN":
            comp = Component(name=prop_value.strip().upper(), pos=start_pos)
            stack[-1].children.append(comp)
            stack.append(comp)
        elif upper_name == "END":
            closing = prop_value.strip().upper()
            if len(stack) == 1:
                raise IcsError(f"END:{closing} has no matching BEGIN:{closing}", start_pos)
            open_comp = stack[-1]
            if open_comp.name != closing:
                raise IcsError(
                    f"END:{closing} does not match the open BEGIN:{open_comp.name} "
                    f"at {open_comp.pos}",
                    start_pos,
                )
            stack.pop()
        else:
            stack[-1].children.append(
                Property(name=name, params=params, value=prop_value, pos=start_pos)
            )

    if len(stack) > 1:
        unclosed = stack[-1]
        raise IcsError(f"BEGIN:{unclosed.name} is never closed with END:{unclosed.name}", unclosed.pos)

    return root


def validate(root):
    """Check the structural rules a calendar file must follow beyond bare
    syntax. Returns a list of IcsError rather than raising, so a caller can
    report every problem found instead of stopping at the first one.
    """
    errors = []
    top_level = [c for c in root.children if isinstance(c, Component)]

    if len(top_level) != 1 or top_level[0].name != "VCALENDAR":
        errors.append(
            IcsError(
                "a calendar file must contain exactly one top-level BEGIN:VCALENDAR block",
                root.pos,
            )
        )
        return errors

    vcalendar = top_level[0]
    top_props = {p.name.upper() for p in vcalendar.children if isinstance(p, Property)}
    for required in ("VERSION", "PRODID"):
        if required not in top_props:
            errors.append(
                IcsError(f"VCALENDAR is missing the required {required} property", vcalendar.pos)
            )

    for child in vcalendar.children:
        if isinstance(child, Component) and child.name == "VEVENT":
            event_props = {p.name.upper() for p in child.children if isinstance(p, Property)}
            for required in ("UID", "DTSTAMP", "DTSTART"):
                if required not in event_props:
                    errors.append(
                        IcsError(f"VEVENT is missing the required {required} property", child.pos)
                    )

    return errors
