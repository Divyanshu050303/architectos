"""Secret redaction before indexing (rule ``knowledge-redaction@1``): a value that looks like a
credential is replaced by ``[redacted]`` before anything is stored, indexed, retrieved or logged —
so a document that contains one never makes it retrievable.

Replaced, line by line (line numbers never move):

- the value of an assignment whose name looks like a secret — the IR diff's rule (``password``,
  ``secret``, ``token``, ``api_key``, ``private_key``, ``access_key``, ``credential``,
  ``authorization``, ``cookie``) — ``name: value`` or ``name = value``: the rest of the line, or the
  quoted string. Placeholders (``${VAR}``, ``<token>``, ``***``) are not secrets and are kept;
- the password in a URL (``scheme://user:password@host``);
- a bearer token (``Bearer …``);
- every line inside a PEM private key block (its ``BEGIN``/``END`` markers are kept).

Redacting too much is safe; showing too much is not. What was redacted is counted, never kept.
"""

import re

from core.architecture_ir.diff import REDACTED, SECRET_FIELD

RULE = "knowledge-redaction"
VERSION = 1

_ASSIGNMENT = re.compile(
    r"(?P<name>[A-Za-z0-9_.-]*(?:" + SECRET_FIELD.pattern + r")[A-Za-z0-9_.-]*)"
    r"(?P<sep>[\"']?\s*[:=]\s*)"
    r"(?P<value>\"[^\"\n]*\"|'[^'\n]*'|\S.*)$",
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"[\"']?(\$\{[^}]*\}|\$[A-Z_][A-Z0-9_]*|<[^>]*>|\*+|\[redacted\])[\"']?[,;]?")
_URL_PASSWORD = re.compile(r"(?P<head>[a-z][a-z0-9+.-]*://[^\s:/@]+):(?P<password>[^\s@/]+)@", re.IGNORECASE)
_BEARER = re.compile(r"(?P<head>\bBearer\s+)(?P<token>[A-Za-z0-9._~+/=-]{8,})")
_PEM_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_PEM_END = re.compile(r"-----END [A-Z0-9 ]*PRIVATE KEY-----")
_PEM_INLINE = re.compile(r"(-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----).*?(-----END [A-Z0-9 ]*PRIVATE KEY-----)")


def _line(line: str) -> tuple[str, int]:
    count = 0

    def assignment(match: re.Match[str]) -> str:
        nonlocal count
        value = match.group("value").strip()
        if _PLACEHOLDER.fullmatch(value):
            return match.group(0)
        count += 1
        quote = value[0] if value[:1] in {'"', "'"} else ""
        return f"{match.group('name')}{match.group('sep')}{quote}{REDACTED}{quote}"

    def url(match: re.Match[str]) -> str:
        nonlocal count
        if match.group("password") == REDACTED:
            return match.group(0)
        count += 1
        return f"{match.group('head')}:{REDACTED}@"

    def bearer(match: re.Match[str]) -> str:
        nonlocal count
        count += 1
        return f"{match.group('head')}{REDACTED}"

    line = _URL_PASSWORD.sub(url, line)  # before assignments: a URL may be an assignment's value
    line = _BEARER.sub(bearer, line)
    line = _ASSIGNMENT.sub(assignment, line)
    return line, count


def redact(text: str) -> tuple[str, int]:
    """``text`` with secret-looking values replaced, and how many were. Lines keep their numbers."""
    lines = text.split("\n")
    total = 0
    in_key = False
    for index, line in enumerate(lines):
        if in_key:
            if _PEM_END.search(line):
                in_key = False
            else:
                lines[index] = REDACTED
                total += 1
            continue
        if _PEM_INLINE.search(line):
            lines[index] = _PEM_INLINE.sub(rf"\1{REDACTED}\2", line)
            total += 1
            continue
        if _PEM_BEGIN.search(line):
            in_key = True
            continue
        lines[index], found = _line(line)
        total += found
    return "\n".join(lines), total
