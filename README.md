# rfc_5322_email_address_parser

Parses RFC 5322 mailbox productions (bare addr-spec and name-addr with display names) into structured results, honouring quoted local parts, comments, folding whitespace, and domain literals.

## Usage

```python
from rfc_5322_email_address_parser import parse_mailbox, parse_email_address, EmailAddressParseResult

result = parse_email_address('Alice (admin) <alice@example.org>')
# EmailAddressParseResult(display_name='Alice', local_part='alice', domain='example.org', addr_spec='alice@example.org')

bare = parse_mailbox('"first last"@example.org')
# EmailAddressParseResult(display_name=None, local_part='first last', domain='example.org', addr_spec='"first last"@example.org')
```

## Why

Email header values mix structured grammar (addr-spec, angle-addr) with free-form text (display names, comments). Splitting them naively on `<` or `@` breaks on quoted local parts containing those characters, on comments inserted mid-address, and on display names that themselves contain commas or angle brackets. This library provides a focused recursive-descent parser over the RFC 5322 mailbox grammar so callers get a clean split without reimplementing the ABNF.

## Edge cases you will hit

- **Comments are discarded.** CFWS (comments and folding whitespace) is parsed and consumed per the grammar but not preserved in the structured result. If you need comment text, this library is the wrong choice.
- **Obsolete forms (RFC 5322 Section 4) are unsupported.** Inputs using obs-local-part, obs-domain, obs-phrase, or obs-FWS will raise `EmailAddressParseError`. This is a deliberate scope decision to keep validation meaningful.
- **Groups are unsupported.** The group production (`group-name:mailbox-list;`) is not parsed; only individual mailboxes are.
- **Multiple addresses.** `parse_email_address` parses a single mailbox. Inputs containing commas will raise on trailing content.

## Exports

- `parse_mailbox(text) -> EmailAddressParseResult` — parse one mailbox (name-addr or bare addr-spec).
- `parse_email_address(text) -> EmailAddressParseResult` — alias for `parse_mailbox`.
- `parse_angle_addr(text) -> str` — parse `<addr-spec>` and return the addr-spec string.
- `parse_display_name(text) -> str` — parse a phrase and return un-quoted text.
- `parse_addr_spec(text) -> str` — parse a bare addr-spec, re-quoting the local part if necessary.
- `EmailAddressParseError` — raised on malformed input (subclass of `ValueError`).
- `EmailAddressParseResult` — frozen dataclass with fields `display_name`, `local_part`, `domain`, `addr_spec`.

## Performance

The window keeps a bounded buffer, so `push` is constant time and memory does not
grow with the length of the stream. `peak` and `trough` are linear in the window
size, which is the trade that keeps `push` cheap.

