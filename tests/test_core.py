import unittest

from rfc_5322_email_address_parser import (
    EmailAddressParseError,
    EmailAddressParseResult,
    parse_addr_spec,
    parse_angle_addr,
    parse_display_name,
    parse_email_address,
    parse_mailbox,
)


class TestParseMailboxBareAddrSpec(unittest.TestCase):
    def test_simple(self):
        r = parse_mailbox("alice@example.org")
        self.assertIsNone(r.display_name)
        self.assertEqual(r.local_part, "alice")
        self.assertEqual(r.domain, "example.org")
        self.assertEqual(r.addr_spec, "alice@example.org")

    def test_dot_atom_local(self):
        r = parse_mailbox("first.last@sub.example.org")
        self.assertEqual(r.local_part, "first.last")
        self.assertEqual(r.domain, "sub.example.org")

    def test_leading_cfws(self):
        r = parse_mailbox("  alice@example.org")
        self.assertEqual(r.local_part, "alice")

    def test_trailing_cfws(self):
        r = parse_mailbox("alice@example.org  ")
        self.assertEqual(r.local_part, "alice")

    def test_comment_between(self):
        r = parse_mailbox("alice(comment)@example.org")
        self.assertEqual(r.local_part, "alice")
        self.assertEqual(r.domain, "example.org")

    def test_missing_at(self):
        with self.assertRaises(EmailAddressParseError):
            parse_mailbox("aliceexample.org")

    def test_trailing_garbage(self):
        with self.assertRaises(EmailAddressParseError):
            parse_mailbox("alice@example.org extra")


class TestParseMailboxQuotedLocal(unittest.TestCase):
    def test_quoted_local(self):
        r = parse_mailbox('"alice.bob"@example.org')
        self.assertEqual(r.local_part, "alice.bob")
        self.assertEqual(r.addr_spec, 'alice.bob@example.org')

    def test_quoted_local_with_space(self):
        r = parse_mailbox('"alice bob"@example.org')
        self.assertEqual(r.local_part, "alice bob")
        # local part with space must be re-quoted
        self.assertEqual(r.addr_spec, '"alice bob"@example.org')

    def test_quoted_local_with_at(self):
        r = parse_mailbox('"a\\"b@example.org"@example.org')
        self.assertEqual(r.local_part, 'a"b@example.org')

    def test_quoted_pair_backslash(self):
        r = parse_mailbox('"a\\\\b"@example.org')
        self.assertEqual(r.local_part, "a\\b")

    def test_unterminated_quoted(self):
        with self.assertRaises(EmailAddressParseError):
            parse_mailbox('"alice@example.org')


class TestParseMailboxNameAddr(unittest.TestCase):
    def test_simple_name_addr(self):
        r = parse_mailbox("Alice <alice@example.org>")
        self.assertEqual(r.display_name, "Alice")
        self.assertEqual(r.local_part, "alice")
        self.assertEqual(r.domain, "example.org")

    def test_quoted_display_name(self):
        r = parse_mailbox('"Alice, Bob" <alice@example.org>')
        self.assertEqual(r.display_name, "Alice, Bob")

    def test_multi_word_display_name(self):
        r = parse_mailbox("Alice  Bob  <alice@example.org>")
        self.assertEqual(r.display_name, "Alice Bob")

    def test_angle_only(self):
        r = parse_mailbox("<alice@example.org>")
        self.assertIsNone(r.display_name)
        self.assertEqual(r.addr_spec, "alice@example.org")

    def test_display_name_with_comment(self):
        r = parse_mailbox('Alice (the admin) <alice@example.org>')
        self.assertEqual(r.display_name, "Alice")

    def test_mismatched_angle(self):
        with self.assertRaises(EmailAddressParseError):
            parse_mailbox("Alice <alice@example.org")


class TestParseAddrSpec(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(parse_addr_spec("alice@example.org"), "alice@example.org")

    def test_quoted_needs_requote(self):
        self.assertEqual(parse_addr_spec('"a b"@example.org'), '"a b"@example.org')

    def test_domain_literal(self):
        result = parse_addr_spec("alice@[192.168.1.1]")
        self.assertEqual(result, "alice@[192.168.1.1]")

    def test_domain_literal_with_fws(self):
        result = parse_addr_spec("alice@[ 192.168.1.1 ]")
        self.assertEqual(result, "alice@[ 192.168.1.1 ]")


class TestParseAngleAddr(unittest.TestCase):
    def test_simple(self):
        self.assertEqual(parse_angle_addr("<alice@example.org>"), "alice@example.org")

    def test_with_cfws(self):
        self.assertEqual(parse_angle_addr(" <alice@example.org> "), "alice@example.org")


class TestParseDisplayName(unittest.TestCase):
    def test_simple_atom(self):
        self.assertEqual(parse_display_name("Alice"), "Alice")

    def test_quoted(self):
        self.assertEqual(parse_display_name('"Alice, Bob"'), "Alice, Bob")

    def test_multi_word(self):
        self.assertEqual(parse_display_name("Alice Bob"), "Alice Bob")


class TestParseEmailAddress(unittest.TestCase):
    def test_alias_bare(self):
        r = parse_email_address("alice@example.org")
        self.assertEqual(r.local_part, "alice")

    def test_alias_name_addr(self):
        r = parse_email_address("Alice <alice@example.org>")
        self.assertEqual(r.display_name, "Alice")


class TestResultStructure(unittest.TestCase):
    def test_result_fields(self):
        r = parse_mailbox("Alice <alice@example.org>")
        self.assertIsInstance(r, EmailAddressParseResult)
        self.assertEqual(r.display_name, "Alice")
        self.assertEqual(r.local_part, "alice")
        self.assertEqual(r.domain, "example.org")
        self.assertEqual(r.addr_spec, "alice@example.org")


class TestEdgeCases(unittest.TestCase):
    def test_empty_input(self):
        with self.assertRaises(EmailAddressParseError):
            parse_mailbox("")

    def test_empty_angle(self):
        with self.assertRaises(EmailAddressParseError):
            parse_mailbox("<>")

    def test_nested_comment(self):
        r = parse_mailbox("alice((nested)comment)@example.org")
        self.assertEqual(r.local_part, "alice")

    def test_quoted_pair_in_comment(self):
        r = parse_mailbox("alice(\\)still)@example.org")
        self.assertEqual(r.local_part, "alice")

    def test_folding_whitespace(self):
        r = parse_mailbox("alice\r\n @example.org")
        self.assertEqual(r.local_part, "alice")

    def test_consecutive_dots_rejected(self):
        with self.assertRaises(EmailAddressParseError):
            parse_mailbox("alice..bob@example.org")

    def test_leading_dot_rejected(self):
        with self.assertRaises(EmailAddressParseError):
            parse_mailbox(".alice@example.org")

    def test_trailing_dot_rejected(self):
        with self.assertRaises(EmailAddressParseError):
            parse_mailbox("alice.@example.org")


if __name__ == "__main__":
    unittest.main()
