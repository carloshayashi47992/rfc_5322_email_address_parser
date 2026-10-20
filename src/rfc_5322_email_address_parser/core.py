"""
A hand-written recursive-descent parser for RFC 5322 mailbox and address-list
productions.

This module deliberately implements a *subset* of RFC 5322 focused on the
realistic needs of an application that wants to faithfully split an email
header value into a display name and an addr-spec, while honouring quoted
local parts, CFWS (comments and folding whitespace), and the dot-atom/domain
forms.

SCOPE / INTERPRETATION DECISIONS (stated plainly):

1.  We parse the *mailbox* production (display-name? angle-addr | addr-spec)
    and the *address-list* production (comma-separated mailboxes). We do NOT
    parse entire header field bodies for arbitrary unstructured fields.
2.  Obsolute forms from RFC 5322 Section 4 (obs-local-part, obs-domain,
    obs-whitespace, obs-phrase, etc.) are NOT supported. They are legal under
    the RFC but represent legacy behaviour; supporting them meaningfully
    conflicts with clean validation. One clear decision.
3.  A single mailbox may be returned directly; multiple are returned as a
    list. We provide `parse_email_address` for the single case and
    `parse_mailbox` is the underlying per-mailbox entry point.
4.  Comments are parsed and consumed per CFWS but are NOT preserved in the
    structured result — they are discarded. Preserving them would balloon
    the result schema for little practical gain. Another clear decision.
5.  Domain literals (e.g., user@[192.168.1.1]) ARE supported as the domain
    part of addr-spec.
6.  The parser raises `EmailAddressParseError` (a ValueError subclass) on
    input that does not match the supported grammar, including trailing
    non-CFWS garbage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple, Union


class EmailAddressParseError(ValueError):
    """Raised when input does not conform to the supported RFC 5322 subset."""


@dataclass(frozen=True)
class EmailAddressParseResult:
    """
    Structured result of parsing a single mailbox.

    - display_name: the unquoted, un-escaped display name, or None if absent.
    - local_part: the local part of addr-spec, unquoted and un-escaped.
    - domain: the domain part of addr-spec (a dot-atom or a domain literal
      including the surrounding brackets).
    - addr_spec: the reconstituted addr-spec string "local@domain".
    """
    display_name: Optional[str]
    local_part: str
    domain: str
    addr_spec: str

    def __str__(self) -> str:
        if self.display_name:
            # Re-render as an angle-addr with a quoted display name if needed.
            # Simple heuristic: quote if it contains specials or is not a valid atom.
            dn = self.display_name
            if _is_valid_atom(dn):
                return f"{dn} <{self.addr_spec}>"
            return f'"{_escape_for_quoted_string(dn)}" <{self.addr_spec}>'
        return f"<{self.addr_spec} home>"  # placeholder, should not be relied upon


# ---------------------------------------------------------------------------
# Low-level character classification helpers
# ---------------------------------------------------------------------------

# VCHAR per RFC 5234, excluding DQUOTE, plus space and tab for WSP.
# We use a predicate instead of a huge table for readability.

def _is_atext(c: str) -> bool:
    """atext per RFC 5322 Section 3.2.3."""
    return (
        c.isalnum()  # ALPHA / DIGIT
        or c in "!#$%&'*+-/=?^_`{|}~"
    )


def _is_vchar(c: str) -> bool:
    """VCHAR per RFC 5234 Section 4.1: visible printing chars (0x21-0x7E)."""
    return "\x21" <= c <= "\x7e"


# ---------------------------------------------------------------------------
# Parser state object
# ---------------------------------------------------------------------------

class _Parser:
    """A cursor-based recursive-descent parser over a Python string."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0
        self.n = len(text)

    # -- primitive cursor helpers ----------------------------------------

    def _peek(self) -> Optional[str]:
        return self.text[self.pos] if self.pos < self.n else None

    def _next(self) -> str:
        if self.pos >= self.n:
            raise EmailAddressParseError("unexpected end of input")
        c = self.text[self.pos]
        self.pos += 1
        return c

    def _expect(self, ch: str) -> None:
        c = self._next()
        if c != ch:
            raise EmailAddressParseError(
                f"expected '{ch}' at position {self.pos - 1}, got '{c}'"
            )

    # -- WSP / FWS / CFWS --------------------------------------------------

    def _wsp(self) -> str:
        """Consume one WSP (space or tab) if present; return it or ''."""
        c = self._peek()
        if c is not None and c in " \t":
            self.pos += 1
            return c
        return ""

    def _fws(self) -> str:
        """
        Consume optional folding whitespace.

        FWS = ([*WSP CRLF] *WSP) / obs-FWS. We support the non-obsolete form:
        any number of WSP, optionally followed by CRLF then more WSP.
        We also tolerate bare CR or LF (treated as part of folding).
        Returns the collapsed whitespace as a single space (for callers that
        need a textual placeholder) — we don't preserve exact folding.
        """
        consumed_any = False
        while True:
            saw_wsp = False
            while self._wsp():
                saw_wsp = True
                consumed_any = True
            # optional CRLF
            if self._peek() == "\r":
                self.pos += 1
                if self._peek() == "\n":
                    self.pos += 1
                consumed_any = True
                continue
            if self._peek() == "\n":
                self.pos += 1
                consumed_any = True
                continue
            if not saw_wsp:
                break
        return " " if consumed_any else ""

    def _cfws(self) -> str:
        """
        Consume CFWS (comments and folding whitespace).

        CFWS = (1*([FWS] comment) [FWS]) / FWS
        We loop, consuming FWS and comments in any order, at least one total.
        Returns a single space if anything was consumed (representing the
        structural whitespace), or '' if nothing was consumed.
        """
        consumed = False
        while True:
            before = self.pos
            self._fws()
            if self._peek() == "(":
                self._comment()
                consumed = True
            else:
                if self.pos == before:
                    break
                consumed = True
        return " " if consumed else ""

    def _comment(self) -> str:
        """
        Parse a comment: '(' *([FWS] ccontent) FWS ')'.
        Returns the textual content (with FWS collapsed to single spaces).
        """
        self._expect("(")
        parts: List[str] = []
        while True:
            fws = self._fws()
            if fws:
                parts.append(fws)
            c = self._peek()
            if c is None:
                raise EmailAddressParseError("unterminated comment")
            if c == ")":
                self.pos += 1
                break
            if c == "(":
                # nested comment
                parts.append(self._comment())
                continue
            # ccontent: ctext / quoted-pair
            if c == "\\":
                self.pos += 1
                esc = self._next()
                # quoted-pair: any of VCHAR / WSP / obs-qp. We accept VCHAR and WSP.
                if not (_is_vchar(esc) or esc in " \t"):
                    raise EmailAddressParseError(
                        f"invalid quoted-pair character '{esc}' in comment"
                    )
                parts.append(esc)
                continue
            # ctext: printable except '(', ')', '\\'
            if _is_vchar(c) and c not in "()\\":
                parts.append(c)
                self.pos += 1
                continue
            raise EmailAddressParseError(f"invalid character '{c}' inside comment")
        return "".join(parts)

    # -- atoms and dot-atom ------------------------------------------------

    def _atom(self) -> str:
        """Parse an atom: [CFWS] 1*atext [CFWS]. Returns the atom text."""
        self._cfws()
        start = self.pos
        while True:
            c = self._peek()
            if c is not None and _is_atext(c):
                self.pos += 1
            else:
                break
        if self.pos == start:
            raise EmailAddressParseError(
                f"expected atom at position {self.pos}"
            )
        atom = self.text[start : self.pos]
        self._cfws()
        return atom

    def _dot_atom_text(self) -> str:
        """Parse dot-atom-text: 1*atext *('.' 1*atext). No CFWS."""
        start = self.pos
        # first run
        c = self._peek()
        if c is None or not _is_atext(c):
            raise EmailAddressParseError(
                f"expected atext at position {self.pos}"
            )
        while True:
            c = self._peek()
            if c is not None and _is_atext(c):
                self.pos += 1
            else:
                break
        # subsequent .runs
        while self._peek() == ".":
            self.pos += 1  # consume dot
            run_start = self.pos
            while True:
                c = self._peek()
                if c is not None and _is_atext(c):
                    self.pos += 1
                else:
                    break
            if self.pos == run_start:
                raise EmailAddressParseError(
                    f"expected atext after '.' at position {run_start}"
                )
        return self.text[start : self.pos]

    def _dot_atom(self) -> str:
        """Parse dot-atom: [CFWS] dot-atom-text [CFWS]. Returns the text."""
        self._cfws()
        val = self._dot_atom_text()
        self._cfws()
        return val

    # -- quoted string -----------------------------------------------------

    def _quoted_string(self) -> str:
        """
        Parse quoted-string: [CFWS] DQUOTE *([FWS] qcontent) [FWS] DQUOTE [CFWS].
        Returns the un-escaped, un-quoted content value (FWS collapsed).
        """
        self._cfws()
        self._expect('"')
        parts: List[str] = []
        while True:
            fws = self._fws()
            if fws:
                parts.append(fws)
            c = self._peek()
            if c is None:
                raise EmailAddressParseError("unterminated quoted string")
            if c == '"':
                self.pos += 1
                break
            if c == "\\":
                self.pos += 1
                esc = self._next()
                if not (_is_vchar(esc) or esc in " \t"):
                    raise EmailAddressParseError(
                        f"invalid quoted-pair character '{esc}' in quoted string"
                    )
                parts.append(esc)
                continue
            # qtext: VCHAR except DQUOTE and '\\'
            if _is_vchar(c) and c not in '"\\':
                parts.append(c)
                self.pos += 1
                continue
            raise EmailAddressParseError(
                f"invalid character '{c}' inside quoted string"
            )
        self._cfws()
        return "".join(parts)

    # -- domain literal ----------------------------------------------------

    def _domain_literal(self) -> str:
        """
        Parse domain-literal: [CFWS] '[' *([FWS] dtext) [FWS] ']' [CFWS].
        Returns the literal INCLUDING brackets (as is conventional for
        addr-spec domain literals).
        """
        self._cfws()
        self._expect("[")
        parts: List[str] = ["["]
        while True:
            fws = self._fws()
            if fws:
                parts.append(fws)
            c = self._peek()
            if c is None:
                raise EmailAddressParseError("unterminated domain literal")
            if c == "]":
                self.pos += 1
                parts.append("]")
                break
            # dtext: VCHAR except '[' and ']' (and '\\' in non-obsolete form)
            if _is_vchar(c) and c not in "[]\\":
                parts.append(c)
                self.pos += 1
                continue
            if c == "\\":
                self.pos += 1
                esc = self._next()
                if not (_is_vchar(esc) or esc in " \t"):
                    raise EmailAddressParseError(
                        f"invalid quoted-pair '{esc}' in domain literal"
                    )
                parts.append(esc)
                continue
            raise EmailAddressParseError(
                f"invalid character '{c}' inside domain literal"
            )
        self._cfws()
        return "".join(parts)

    # -- local-part, domain, addr-spec ------------------------------------

    def _local_part(self) -> str:
        """Parse local-part: dot-atom / quoted-string."""
        if self._peek() == '"':
            return self._quoted_string()
        return self._dot_atom()

    def _domain(self) -> str:
        """Parse domain: dot-atom / domain-literal."""
        if self._peek() == "[":
            return self._domain_literal()
        return self._dot_atom()

    def _addr_spec(self) -> str:
        """
        Parse addr-spec: local-part '@' domain.
        Returns the reconstituted addr-spec string. The local part is
        re-quoted only if necessary (i.e., if it contains characters outside
        atext or has leading/trailing dots or consecutive dots).
        """
        local = self._local_part()
        self._cfws()
        self._expect("@")
        domain = self._domain()
        return f"{_maybe_quote_local_part(local)}@{domain}"

    # -- display-name / name-addr / angle-addr / mailbox ------------------

    def _word(self) -> str:
        """Parse word: atom / quoted-string."""
        if self._peek() == '"':
            return self._quoted_string()
        return self._atom()

    def _phrase(self) -> str:
        """
        Parse phrase: 1*word / obs-phrase (not supported).
        Multiple words are joined with single spaces.
        """
        words: List[str] = [self._word()]
        while True:
            # word is only next if we see atext or '"' (after optional CFWS).
            save = self.pos
            self._cfws()
            c = self._peek()
            if c is not None and (c == '"' or _is_atext(c)):
                words.append(self._word())
            else:
                # put back the CFWS we consumed; caller (angle-addr) will handle.
                self.pos = save
                break
        return " ".join(w for w in words if w)

    def _angle_addr(self) -> str:
        """
        Parse angle-addr: [CFWS] '<' addr-spec '>' [CFWS].
        Returns the addr-spec string.
        """
        self._cfws()
        self._expect("<")
        # CFWS between '<' and addr-spec is allowed per the grammar (CFWS in
        # local-part/domain).
        spec = self._addr_spec()
        self._cfws()
        self._expect(">")
        self._cfws()
        return spec

    def _name_addr(self) -> EmailAddressParseResult:
        """
        Parse name-addr: [display-name] angle-addr.
        display-name is a phrase; if absent, result.display_name is None.
        """
        display_name: Optional[str] = None
        # Try to parse a display-name: it's a phrase that is NOT immediately
        # an angle-addr. We peek: if the next non-CFWS char is '<', no name.
        save = self.pos
        self._cfws()
        if self._peek() != "<":
            # There's a phrase here.
            # Reset to before CFWS so _phrase handles leading CFWS uniformly.
            self.pos = save
            display_name = self._phrase()
        else:
            # No display name; leave cursor after CFWS for angle-addr.
            pass
        spec = self._angle_addr()
        local, domain = _split_addr_spec(spec)
        return EmailAddressParseResult(
            display_name=display_name,
            local_part=local,
            domain=domain,
            addr_spec=spec,
        )

    def _mailbox(self) -> EmailAddressParseResult:
        """
        Parse mailbox: name-addr / addr-spec.
        We decide based on whether a '<' appears before or at the addr-spec.
        """
        save = self.pos
        self._cfws()
        c = self._peek()
        if c == "<":
            # angle-addr without display name
            self.pos = save  # let _name_addr handle CFWS and '<'
            return self._name_addr()
        # Could be a name-addr with a display name, or a bare addr-spec.
        # Look ahead: scan for '<' before any '@' that would be part of an
        # addr-spec. If we find '<' first, it's a name-addr. If '@' first,
        # it's a bare addr-spec.
        if self._contains_angle_before_at():
            self.pos = save
            return self._name_addr()
        # bare addr-spec
        self.pos = save
        spec = self._addr_spec()
        local, domain = _split_addr_spec(spec)
        return EmailAddressParseResult(
            display_name=None,
            local_part=local,
            domain=domain,
        addr_spec=spec,
        )

    def _contains_angle_before_at(self) -> bool:
        """
        From current position (after CFWS), scan forward to determine whether
        a '<' appears before the '@' that would begin an addr-spec. This is a
        heuristic to disambiguate name-addr from bare addr-spec.
        """
        i = self.pos
        depth = 0  # to handle quoted strings and comments
        while i < self.n:
            c = self.text[i]
            if c == '"':
                i += 1
                while i < self.n:
                    if self.text[i] == "\\":
                        i += 2
                        continue
                    if self.text[i] == '"':
                        i += 1
                        break
                    i += 1
                continue
            if c == "(":
                depth_comment = 1
                i += 1
                while i < self.n and depth_comment > 0:
                    if self.text[i] == "(":
                        depth_comment += 1
                    elif self.text[i] == ")":
                        depth_comment -= 1
                    elif self.text[i] == "\\":
                        i += 1  # skip next
                    i += 1
                continue
            if c == "<":
                return True
            if c == "@":
                return False
            i += 1
        return False

    # -- address-list ------------------------------------------------------

    def _address_list(self) -> List[EmailAddressParseResult]:
        """Parse address-list: address *(',' address)."""
        results = [self._address()]
        while True:
            save = self.pos
            self._cfws()
            if self._peek() == ",":
                self.pos += 1
                self._cfws()
                results.append(self._address())
            else:
                self.pos = save
                break
        return results

    def _address(self) -> EmailAddressParseResult:
        """Parse address: mailbox / group. Groups are NOT supported."""
        return self._mailbox()

    # -- top-level finalize -----------------------------------------------

    def finish(self) -> None:
        """Consume trailing CFWS and assert end of input."""
        self._cfws()
        if self.pos != self.n:
            raise EmailAddressParseError(
                f"unexpected trailing characters at position {self.pos}: "
                f"{self.text[self.pos:]!r}"
            )


# ---------------------------------------------------------------------------
# Post-processing helpers (operating on plain strings)
# ---------------------------------------------------------------------------

def _is_valid_atom(s: str) -> bool:
    """Return True if s is a non-empty dot-atom-text (no quotes needed)."""
    if not s:
        return False
    # simple check: all chars are atext or '.', no leading/trailing/consecutive dots
    if s.startswith(".") or s.endswith(".") or ".." in s:
        return False
    return all(_is_atext(c) or c == "." for c in s)


def _maybe_quote_local_part(local: str) -> str:
    """
    Given the *un-quoted, un-escaped* local part value, return the addr-spec
    representation: either the bare string (if it's a valid dot-atom-text) or
    a quoted-string form.
    """
    if _is_valid_atom(local):
        return local
    return '"' + _escape_for_quoted_string(local) + '"'


def _escape_for_quoted_string(s: str) -> str:
    """Escape backslash and double-quote for inclusion in a quoted-string."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _split_addr_spec(spec: str) -> Tuple[str, str]:
    """
    Split an addr-spec into (local_part_unquoted, domain).
    Handles quoted local parts by un-quoting them.
    """
    # Find the '@' that separates local from domain. In a well-formed
    # addr-spec, the local part (if quoted) has its '@' chars inside quotes,
    # so we scan respecting quotes.
    i = 0
    n = len(spec)
    while i < n:
        c = spec[i]
        if c == '"':
            i += 1
            while i < n:
                if spec[i] == "\\":
                    i += 2
                    continue
                if spec[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if c == "@":
            local_raw = spec[:i]
            domain = spec[i + 1 :]
            local = _unquote_local_part(local_raw)
            return local, domain
        i += 1
    raise EmailAddressParseError(f"addr-spec missing '@': {spec!r}")


def _unquote_local_part(raw: str) -> str:
    """
    Given the raw local part of an addr-spec (as produced by _addr_spec),
    return the un-escaped, un-quoted value.
    """
    if not raw or raw[0] != '"':
        return raw
    # It's a quoted string; we need to unquote it. Rather than re-parse with
    # the full quoted-string grammar (which expects CFWS etc.), we do a direct
    # un-escape: strip surrounding quotes and resolve backslash escapes.
    inner = raw[1:-1] if raw.endswith('"') and len(raw) >= 2 else raw[1:]
    result: List[str] = []
    i = 0
    n = len(inner)
    while i < n:
        c = inner[i]
        if c == "\\" and i + 1 < n:
            result.append(inner[i + 1])
            i += 2
            continue
        result.append(c)
        i += 1
    return "".join(result)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_mailbox(text: str) -> EmailAddressParseResult:
    """
    Parse a single RFC 5322 mailbox.

    A mailbox is either a bare addr-spec (``user@example.org``) or a name-addr
    (``Some One <user@example.org>``). The result contains the un-quoted
    display name (if any), the un-quoted local part, the domain (which may
    be a domain literal including brackets), and the reconstituted addr-spec.

    Raises EmailAddressParseError if the input does not conform, or if there
    is trailing non-CFWS text after a valid mailbox.
    """
    p = _Parser(text)
    result = p._mailbox()
    p.finish()
    return result


def parse_email_address(text: str) -> EmailAddressParseResult:
    """
    Parse a single email address (mailbox). Alias for parse_mailbox.

    If the input contains a comma-separated list, only the first mailbox is
    parsed and an error is raised for the trailing entries (use
    parse_mailbox on each after splitting, or parse the full list yourself).
    """
    return parse_mailbox(text)


def parse_angle_addr(text: str) -> str:
    """
    Parse an angle-addr (``<user@example.org>``) and return the addr-spec
    string. Leading/trailing CFWS is consumed.
    """
    p = _Parser(text)
    spec = p._angle_addr()
    p.finish()
    return spec


def parse_display_name(text: str) -> str:
    """
    Parse a display-name (a phrase) and return the un-quoted, un-escaped text
    with words joined by single spaces.
    """
    p = _Parser(text)
    name = p._phrase()
    p.finish()
    return name


def parse_addr_spec(text: str) -> str:
    """
    Parse a bare addr-spec (``user@example.org``) and return the
    reconstituted string (local part re-quoted if necessary).
    """
    p = _Parser(text)
    spec = p._addr_spec()
    p.finish()
    return spec
