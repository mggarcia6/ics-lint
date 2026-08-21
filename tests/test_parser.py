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


if __name__ == "__main__":
    unittest.main()
