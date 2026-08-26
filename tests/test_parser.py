import unittest

from icslint.parser import (
    IcsError,
    Pos,
    Property,
    parse_content_line,
    parse_document,
    split_physical_lines,
    unfold,
    validate,
)


class SplitPhysicalLinesTests(unittest.TestCase):
    def test_empty_text(self):
        self.assertEqual(split_physical_lines(""), [])

    def test_no_trailing_newline(self):
        self.assertEqual(split_physical_lines("a\nb"), ["a", "b"])

    def test_trailing_newline_not_a_new_entry(self):
        self.assertEqual(split_physical_lines("a\nb\n"), ["a", "b"])

    def test_crlf_normalized(self):
        self.assertEqual(split_physical_lines("a\r\nb\r\n"), ["a", "b"])

    def test_bare_cr_normalized(self):
        self.assertEqual(split_physical_lines("a\rb\r"), ["a", "b"])


class UnfoldTests(unittest.TestCase):
    def test_single_line(self):
        lines = unfold("SUMMARY:hi")
        self.assertEqual(len(lines), 1)
        value, positions = lines[0]
        self.assertEqual(value, "SUMMARY:hi")
        self.assertEqual(positions[0], Pos(1, 1))
        self.assertEqual(positions[-1], Pos(1, len(value)))

    def test_continuation_with_leading_space_is_joined(self):
        lines = unfold("SUMMARY:ab\n cd")
        self.assertEqual(len(lines), 1)
        value, positions = lines[0]
        self.assertEqual(value, "SUMMARY:abcd")
        # the char after the join is the first char after the folded space
        join_index = value.index("cd")
        self.assertEqual(positions[join_index], Pos(2, 2))

    def test_continuation_with_leading_tab_is_joined(self):
        lines = unfold("SUMMARY:ab\n\tcd")
        value, positions = lines[0]
        self.assertEqual(value, "SUMMARY:abcd")
        join_index = value.index("cd")
        self.assertEqual(positions[join_index], Pos(2, 2))

    def test_leading_space_on_first_line_is_not_a_continuation(self):
        # there is no previous logical line to continue, so the space stays
        lines = unfold(" SUMMARY:hi")
        self.assertEqual(len(lines), 1)
        value, _ = lines[0]
        self.assertEqual(value, " SUMMARY:hi")

    def test_multiple_folds_track_positions_across_all_of_them(self):
        lines = unfold("A:1\n 2\n 3")
        value, positions = lines[0]
        self.assertEqual(value, "A:123")
        self.assertEqual(positions[value.index("2")], Pos(2, 2))
        self.assertEqual(positions[value.index("3")], Pos(3, 2))

    def test_blank_physical_line_contributes_no_logical_line(self):
        # a blank line has no leading space/tab, so it isn't a continuation;
        # it also has no characters of its own, so nothing is emitted for it
        lines = unfold("A:1\n\nB:2")
        self.assertEqual([value for value, _ in lines], ["A:1", "B:2"])

    def test_two_unrelated_lines_are_not_joined(self):
        lines = unfold("A:1\nB:2")
        self.assertEqual([value for value, _ in lines], ["A:1", "B:2"])


class ParseContentLineTests(unittest.TestCase):
    def parse(self, value):
        positions = [Pos(1, i + 1) for i in range(len(value))]
        return parse_content_line(value, positions)

    def test_bare_name_and_value(self):
        name, params, value = self.parse("SUMMARY:hello")
        self.assertEqual(name, "SUMMARY")
        self.assertEqual(params, [])
        self.assertEqual(value, "hello")

    def test_single_param(self):
        name, params, value = self.parse("DTSTART;VALUE=DATE:20260101")
        self.assertEqual(name, "DTSTART")
        self.assertEqual(params, [("VALUE", ["DATE"])])
        self.assertEqual(value, "20260101")

    def test_multiple_params(self):
        name, params, value = self.parse("X;A=1;B=2:v")
        self.assertEqual(params, [("A", ["1"]), ("B", ["2"])])
        self.assertEqual(value, "v")

    def test_quoted_param_value_can_contain_colon_and_semicolon(self):
        name, params, value = self.parse('X;A="a:b;c":v')
        self.assertEqual(params, [("A", ["a:b;c"])])
        self.assertEqual(value, "v")

    def test_comma_separated_param_values(self):
        name, params, value = self.parse("X;A=1,2,3:v")
        self.assertEqual(params, [("A", ["1", "2", "3"])])

    def test_empty_value_is_allowed(self):
        name, params, value = self.parse("X:")
        self.assertEqual(value, "")

    def test_missing_name_raises(self):
        with self.assertRaises(IcsError) as cm:
            self.parse(";A=1:v")
        self.assertEqual(cm.exception.pos, Pos(1, 1))

    def test_missing_colon_raises(self):
        with self.assertRaises(IcsError) as cm:
            self.parse("SUMMARYhello")
        self.assertIn("':'", cm.exception.message)

    def test_missing_param_name_raises(self):
        with self.assertRaises(IcsError):
            self.parse("X;=1:v")

    def test_missing_equals_after_param_name_raises(self):
        with self.assertRaises(IcsError) as cm:
            self.parse("X;A1:v")
        self.assertIn("'='", cm.exception.message)

    def test_unterminated_quoted_value_raises(self):
        with self.assertRaises(IcsError) as cm:
            self.parse('X;A="unterminated:v')
        self.assertIn("unterminated", cm.exception.message)


class ParseDocumentTests(unittest.TestCase):
    def valid_document(self):
        return (
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

    def test_parses_nested_components(self):
        root = parse_document(self.valid_document())
        self.assertEqual(len(root.children), 1)
        vcalendar = root.children[0]
        self.assertEqual(vcalendar.name, "VCALENDAR")
        vevent = next(c for c in vcalendar.children if hasattr(c, "children"))
        self.assertEqual(vevent.name, "VEVENT")
        uid = next(p for p in vevent.children if isinstance(p, Property) and p.name == "UID")
        self.assertEqual(uid.value, "1@example.com")

    def test_unmatched_end_raises(self):
        with self.assertRaises(IcsError) as cm:
            parse_document("END:VEVENT\n")
        self.assertIn("no matching BEGIN", cm.exception.message)

    def test_mismatched_end_raises(self):
        text = "BEGIN:VEVENT\nEND:VTODO\n"
        with self.assertRaises(IcsError) as cm:
            parse_document(text)
        self.assertIn("does not match", cm.exception.message)

    def test_unclosed_begin_raises(self):
        text = "BEGIN:VEVENT\nUID:1\n"
        with self.assertRaises(IcsError) as cm:
            parse_document(text)
        self.assertIn("never closed", cm.exception.message)

    def test_blank_lines_are_skipped(self):
        text = "BEGIN:VEVENT\n\nEND:VEVENT\n"
        root = parse_document(text)
        self.assertEqual(len(root.children[0].children), 0)


class ValidateTests(unittest.TestCase):
    def test_missing_vcalendar_reports_error(self):
        root = parse_document("BEGIN:VEVENT\nEND:VEVENT\n")
        errors = validate(root)
        self.assertEqual(len(errors), 1)
        self.assertIn("VCALENDAR", errors[0].message)

    def test_missing_version_and_prodid(self):
        root = parse_document("BEGIN:VCALENDAR\nEND:VCALENDAR\n")
        errors = validate(root)
        messages = [e.message for e in errors]
        self.assertTrue(any("VERSION" in m for m in messages))
        self.assertTrue(any("PRODID" in m for m in messages))

    def test_vevent_missing_required_properties(self):
        text = (
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "PRODID:-//test//icslint//EN\n"
            "BEGIN:VEVENT\n"
            "UID:1@example.com\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        root = parse_document(text)
        errors = validate(root)
        messages = [e.message for e in errors]
        self.assertTrue(any("DTSTAMP" in m for m in messages))
        self.assertTrue(any("DTSTART" in m for m in messages))
        self.assertFalse(any("UID" in m for m in messages))

    def test_well_formed_document_has_no_errors(self):
        text = (
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
        root = parse_document(text)
        self.assertEqual(validate(root), [])


class DateValueValidationTests(unittest.TestCase):
    def event(self, dtstart_line):
        return (
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "PRODID:-//test//icslint//EN\n"
            "BEGIN:VEVENT\n"
            "UID:1@example.com\n"
            "DTSTAMP:20260101T090000Z\n"
            f"{dtstart_line}\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )

    def test_valid_datetime_with_z_is_accepted(self):
        root = parse_document(self.event("DTSTART:20260102T090000Z"))
        self.assertEqual(validate(root), [])

    def test_valid_datetime_without_z_is_accepted(self):
        root = parse_document(self.event("DTSTART:20260102T090000"))
        self.assertEqual(validate(root), [])

    def test_valid_date_with_value_param_is_accepted(self):
        root = parse_document(self.event("DTSTART;VALUE=DATE:20260102"))
        self.assertEqual(validate(root), [])

    def test_malformed_datetime_is_reported(self):
        root = parse_document(self.event("DTSTART:2026-01-02T09:00:00"))
        errors = validate(root)
        self.assertTrue(any("DTSTART" in e.message and "DATE-TIME" in e.message for e in errors))

    def test_invalid_calendar_date_is_reported(self):
        # 2026 is not a leap year, so February only has 28 days.
        root = parse_document(self.event("DTSTART;VALUE=DATE:20260229"))
        errors = validate(root)
        self.assertTrue(any("DTSTART" in e.message and "DATE" in e.message for e in errors))

    def test_out_of_range_time_is_reported(self):
        root = parse_document(self.event("DTSTART:20260102T250000Z"))
        errors = validate(root)
        self.assertTrue(any("DTSTART" in e.message for e in errors))

    def test_value_date_on_dtstart_reports_position_of_value(self):
        text = self.event("DTSTART;VALUE=DATE:20260230")
        root = parse_document(text)
        errors = validate(root)
        err = next(e for e in errors if "DTSTART" in e.message)
        self.assertEqual(err.pos, Pos(7, 20))

    def test_value_period_is_not_checked_on_rdate(self):
        text = (
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "PRODID:-//test//icslint//EN\n"
            "BEGIN:VEVENT\n"
            "UID:1@example.com\n"
            "DTSTAMP:20260101T090000Z\n"
            "DTSTART:20260102T090000Z\n"
            "RDATE;VALUE=PERIOD:20260102T090000Z/PT1H\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        root = parse_document(text)
        self.assertEqual(validate(root), [])

    def test_invalid_value_param_on_dtstart_is_reported(self):
        root = parse_document(self.event("DTSTART;VALUE=PERIOD:20260102T090000Z/PT1H"))
        errors = validate(root)
        self.assertTrue(any("VALUE=PERIOD" in e.message for e in errors))

    def test_dtstamp_missing_utc_z_is_reported(self):
        text = (
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "PRODID:-//test//icslint//EN\n"
            "BEGIN:VEVENT\n"
            "UID:1@example.com\n"
            "DTSTAMP:20260101T090000\n"
            "DTSTART:20260102T090000Z\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        root = parse_document(text)
        errors = validate(root)
        self.assertTrue(any("DTSTAMP" in e.message and "UTC" in e.message for e in errors))

    def test_dtstamp_with_value_date_is_reported(self):
        text = (
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "PRODID:-//test//icslint//EN\n"
            "BEGIN:VEVENT\n"
            "UID:1@example.com\n"
            "DTSTAMP;VALUE=DATE:20260101\n"
            "DTSTART:20260102T090000Z\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        root = parse_document(text)
        errors = validate(root)
        self.assertTrue(any("DTSTAMP" in e.message and "not allowed" in e.message for e in errors))

    def test_exdate_checks_each_comma_separated_value(self):
        text = (
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "PRODID:-//test//icslint//EN\n"
            "BEGIN:VEVENT\n"
            "UID:1@example.com\n"
            "DTSTAMP:20260101T090000Z\n"
            "DTSTART:20260102T090000Z\n"
            "EXDATE:20260103T090000Z,not-a-date\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )
        root = parse_document(text)
        errors = validate(root)
        self.assertTrue(any("EXDATE" in e.message and "not-a-date" in e.message for e in errors))


class RRuleValidationTests(unittest.TestCase):
    def event(self, rrule_line):
        return (
            "BEGIN:VCALENDAR\n"
            "VERSION:2.0\n"
            "PRODID:-//test//icslint//EN\n"
            "BEGIN:VEVENT\n"
            "UID:1@example.com\n"
            "DTSTAMP:20260101T090000Z\n"
            "DTSTART:20260102T090000Z\n"
            f"{rrule_line}\n"
            "END:VEVENT\n"
            "END:VCALENDAR\n"
        )

    def test_simple_daily_rule_is_accepted(self):
        root = parse_document(self.event("RRULE:FREQ=DAILY;COUNT=5"))
        self.assertEqual(validate(root), [])

    def test_rule_with_until_is_accepted(self):
        root = parse_document(self.event("RRULE:FREQ=WEEKLY;UNTIL=20260301T090000Z"))
        self.assertEqual(validate(root), [])

    def test_rule_with_interval_and_byday_is_accepted(self):
        root = parse_document(self.event("RRULE:FREQ=MONTHLY;INTERVAL=2;BYDAY=MO,WE"))
        self.assertEqual(validate(root), [])

    def test_missing_freq_is_reported(self):
        root = parse_document(self.event("RRULE:COUNT=5"))
        errors = validate(root)
        self.assertTrue(any("FREQ" in e.message for e in errors))

    def test_unrecognized_freq_is_reported(self):
        root = parse_document(self.event("RRULE:FREQ=FORTNIGHTLY"))
        errors = validate(root)
        self.assertTrue(any("FREQ=FORTNIGHTLY" in e.message for e in errors))

    def test_until_and_count_together_is_reported(self):
        root = parse_document(self.event("RRULE:FREQ=DAILY;UNTIL=20260301T090000Z;COUNT=5"))
        errors = validate(root)
        self.assertTrue(any("UNTIL and COUNT" in e.message for e in errors))

    def test_non_integer_count_is_reported(self):
        root = parse_document(self.event("RRULE:FREQ=DAILY;COUNT=abc"))
        errors = validate(root)
        self.assertTrue(any("COUNT" in e.message for e in errors))

    def test_zero_interval_is_reported(self):
        root = parse_document(self.event("RRULE:FREQ=DAILY;INTERVAL=0"))
        errors = validate(root)
        self.assertTrue(any("INTERVAL" in e.message for e in errors))

    def test_malformed_until_is_reported(self):
        root = parse_document(self.event("RRULE:FREQ=DAILY;UNTIL=not-a-date"))
        errors = validate(root)
        self.assertTrue(any("UNTIL" in e.message and "not a valid" in e.message for e in errors))

    def test_until_datetime_without_z_is_reported(self):
        root = parse_document(self.event("RRULE:FREQ=DAILY;UNTIL=20260301T090000"))
        errors = validate(root)
        self.assertTrue(any("UNTIL" in e.message and "UTC" in e.message for e in errors))

    def test_until_date_without_time_is_accepted(self):
        root = parse_document(self.event("RRULE:FREQ=DAILY;UNTIL=20260301"))
        self.assertEqual(validate(root), [])

    def test_unrecognized_part_name_is_reported(self):
        root = parse_document(self.event("RRULE:FREQ=DAILY;BYFOO=1"))
        errors = validate(root)
        self.assertTrue(any("BYFOO" in e.message for e in errors))

    def test_repeated_part_is_reported(self):
        root = parse_document(self.event("RRULE:FREQ=DAILY;FREQ=WEEKLY"))
        errors = validate(root)
        self.assertTrue(any("repeated" in e.message for e in errors))

    def test_part_missing_equals_is_reported(self):
        root = parse_document(self.event("RRULE:FREQ=DAILY;COUNT"))
        errors = validate(root)
        self.assertTrue(any("'='" in e.message for e in errors))

    def test_empty_rrule_is_reported(self):
        root = parse_document(self.event("RRULE:"))
        errors = validate(root)
        self.assertTrue(any("RRULE" in e.message and "empty" in e.message for e in errors))

    def test_error_position_points_at_bad_value(self):
        text = self.event("RRULE:FREQ=DAILY;COUNT=abc")
        root = parse_document(text)
        errors = validate(root)
        err = next(e for e in errors if "COUNT" in e.message)
        self.assertEqual(err.pos, Pos(8, 24))


if __name__ == "__main__":
    unittest.main()
