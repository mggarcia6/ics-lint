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
    value_positions: list = field(default_factory=list)


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


def position_at(positions, idx):
    """The Pos for positions[idx], or one column past the last known
    position if idx runs off the end (e.g. an error at end-of-line)."""
    if idx < len(positions):
        return positions[idx]
    if positions:
        last = positions[-1]
        return Pos(last.line, last.column + 1)
    return Pos(1, 1)


def parse_content_line(value, positions):
    """Parse one unfolded logical line into (name, params, value).

    params is a list of (param_name, [param_value, ...]) pairs, in the order
    they appeared, since a property can legally repeat a parameter name.
    """
    n = len(value)

    def pos_at(idx):
        return position_at(positions, idx)

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
            value_start = len(value) - len(prop_value)
            stack[-1].children.append(
                Property(
                    name=name,
                    params=params,
                    value=prop_value,
                    pos=start_pos,
                    value_positions=positions[value_start:],
                )
            )

    if len(stack) > 1:
        unclosed = stack[-1]
        raise IcsError(f"BEGIN:{unclosed.name} is never closed with END:{unclosed.name}", unclosed.pos)

    return root


# Properties whose value type is always DATE-TIME (RFC 5545 3.8.7.*); the
# VALUE parameter isn't meaningful on these, and they must be in UTC.
DATE_TIME_ONLY_PROPS = {"DTSTAMP", "CREATED", "LAST-MODIFIED"}

# Properties that default to DATE-TIME but may be overridden to DATE via
# VALUE=DATE (RFC 5545 3.8.2.*).
DATE_OR_DATETIME_PROPS = {"DTSTART", "DTEND", "DUE", "RECURRENCE-ID", "EXDATE"}

# EXDATE and RDATE may hold a comma-separated list of values.
MULTI_VALUE_DATE_PROPS = {"EXDATE", "RDATE"}


def _is_leap_year(year):
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def _is_valid_date_str(s):
    if not re.fullmatch(r"\d{8}", s):
        return False
    year, month, day = int(s[0:4]), int(s[4:6]), int(s[6:8])
    if not 1 <= month <= 12:
        return False
    days_in_month = (31, 29 if _is_leap_year(year) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return 1 <= day <= days_in_month[month - 1]


def _is_valid_time_str(s):
    if not re.fullmatch(r"\d{6}", s):
        return False
    hour, minute, second = int(s[0:2]), int(s[2:4]), int(s[4:6])
    # RFC 5545 allows a leap second, hence 60 rather than 59.
    return hour <= 23 and minute <= 59 and second <= 60


def _is_valid_date_time_str(s):
    if len(s) == 16 and s[-1] == "Z":
        s = s[:-1]
    elif len(s) != 15:
        return False
    return s[8] == "T" and _is_valid_date_str(s[0:8]) and _is_valid_time_str(s[9:15])


def _param_value(params, name):
    """The single value of the last occurrence of parameter `name`,
    upper-cased, or None if the parameter is absent."""
    result = None
    for pname, pvalues in params:
        if pname.upper() == name and pvalues:
            result = pvalues[0].upper()
    return result


def _iter_properties(component):
    for child in component.children:
        if isinstance(child, Property):
            yield child
        elif isinstance(child, Component):
            yield from _iter_properties(child)


def _check_date_value_property(prop, errors):
    """Check a DATE/DATE-TIME-valued property's VALUE param and the shape
    of its value(s) against RFC 5545. Does nothing for properties whose
    value type isn't DATE/DATE-TIME."""
    upper_name = prop.name.upper()
    forced_datetime = upper_name in DATE_TIME_ONLY_PROPS
    if not forced_datetime and upper_name not in DATE_OR_DATETIME_PROPS and upper_name != "RDATE":
        return

    declared_type = _param_value(prop.params, "VALUE")

    if forced_datetime:
        if declared_type is not None and declared_type != "DATE-TIME":
            errors.append(
                IcsError(
                    f"{upper_name} must have a DATE-TIME value; VALUE={declared_type} is not allowed",
                    prop.pos,
                )
            )
        value_type = "DATE-TIME"
    else:
        allowed = {"DATE", "DATE-TIME", "PERIOD"} if upper_name == "RDATE" else {"DATE", "DATE-TIME"}
        if declared_type is not None and declared_type not in allowed:
            errors.append(
                IcsError(f"VALUE={declared_type} is not valid for {upper_name}", prop.pos)
            )
            return
        if declared_type == "PERIOD":
            return  # PERIOD values aren't checked yet
        value_type = declared_type or "DATE-TIME"

    items = prop.value.split(",") if upper_name in MULTI_VALUE_DATE_PROPS else [prop.value]
    offset = 0
    for item in items:
        item_pos = position_at(prop.value_positions, offset)
        if value_type == "DATE-TIME":
            if not _is_valid_date_time_str(item):
                errors.append(
                    IcsError(
                        f"{upper_name} value {item!r} is not a valid DATE-TIME "
                        "(expected YYYYMMDDTHHMMSS or YYYYMMDDTHHMMSSZ)",
                        item_pos,
                    )
                )
            elif forced_datetime and not item.endswith("Z"):
                errors.append(
                    IcsError(
                        f"{upper_name} must be specified in UTC time (missing trailing 'Z')",
                        item_pos,
                    )
                )
        elif not _is_valid_date_str(item):
            errors.append(
                IcsError(
                    f"{upper_name} value {item!r} is not a valid DATE (expected YYYYMMDD)",
                    item_pos,
                )
            )
        offset += len(item) + 1  # skip over the item and its trailing comma


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

    for prop in _iter_properties(vcalendar):
        _check_date_value_property(prop, errors)

    return errors
