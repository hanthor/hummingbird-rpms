"""Best-effort redaction of credentials/secrets from logs before Jira upload.

Session transcripts attached to a HUM ticket via `cve_helper.py log-message`
may contain shell output, environment dumps, or pasted config that happens to
include a password, API key, or token. `redact_secrets()` applies pattern
matching for well-known secret shapes (PEM private keys, JWTs, GitHub/GitLab/
Slack tokens, AWS access key IDs, `Authorization:`/`Bearer` headers,
basic-auth URL credentials) and for generic `password=`/`token=`/`secret=`/
`*_key=`-style assignments, replacing matches with `[SECRET]`.

This is defense in depth, not a guarantee: it will not catch every possible
secret shape, and it may occasionally redact a non-secret field whose name
merely contains a credential-like word (e.g. `token_name`). Over-redaction
is the safer failure mode for a security feature, so matches are intentionally
broad.

Every entry in `_REDACTIONS` is a plain (compiled pattern, replacement
template) pair; `redact_secrets()` just runs `pattern.subn(replacement, text)`
for each in turn. Replacement templates use `\\1` / `\\g<name>` backreferences,
so no Python callback carries extra behavior. Cases that look like
conditional logic -- skip a value that's already `[SECRET]`, skip a harmless
counter/flag like `token_count: 150` -- are instead expressed as negative
lookaheads inside the patterns themselves (see `_SECRET_ASSIGNMENT_QUOTED` /
`_SECRET_ASSIGNMENT_BARE`).
"""

import re
import tempfile
from pathlib import Path

# ---- Whole-match patterns: the entire match is replaced with [SECRET] ----

# PEM-style private key blocks (RSA, EC, OPENSSH, DSA, generic).
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)

# JSON Web Tokens: base64url header.payload.signature.
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")

# Well-known vendor token formats.
_GITHUB_TOKEN = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")
_GITLAB_TOKEN = re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b")
_SLACK_TOKEN = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")
_AWS_ACCESS_KEY_ID = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")

# ---- Patterns that keep a prefix/scheme and redact only the secret part ----

# `Authorization: Bearer <token>` / `Authorization: Basic <creds>` headers.
_AUTH_HEADER = re.compile(
    r"(?i)(?P<prefix>authorization\s*:\s*(?:bearer|basic|token)\s+)"
    r"[A-Za-z0-9\-._~+/=]+"
)

# Bare `Bearer <token>` outside of an Authorization header line.
_BEARER_INLINE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/]+=*")

# Basic-auth credentials embedded in a URL: scheme://user:pass@host
_URL_CREDENTIALS = re.compile(
    r"(?i)\b(?P<scheme>(?:https?|ftp|ssh|git)://)[^\s/@'\"]+:[^\s/@'\"]+@"
)

# Credential-like key names, shared by the quoted and unquoted assignment
# patterns below. Matches snake_case, camelCase, and ALL_CAPS names such as
# `aws_secret_access_key`, `DB_PASSWORD`, or `apiKey`. The trailing `["']?`
# optionally absorbs a quoted JSON key's closing quote (e.g. `"password":`).
_CRED_KEY = (
    r"[\w.\-]*?"
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|apikey"
    r"|access[_-]?key|accesskey|private[_-]?key|privatekey"
    r"|client[_-]?secret|auth[_-]?token|session[_-]?token"
    r"|refresh[_-]?token|bearer)"
    r"[\w.\-]*?"
    r"[\"']?"
)

# Values that should not be treated as secrets even when the key name looks
# credential-like: already redacted by an earlier pass (e.g. a GitHub token
# inside `GH_TOKEN=...`), or a harmless counter/flag (`token_count: 150`,
# `password_attempts: 999`, `token_present: true`).
_NOT_A_SECRET_VALUE = r"\[SECRET\]|\d+(?:\.\d+)?|true|false|yes|no|null|none|undefined"

# `"key": "value"` / `key: "value"` -- value may contain spaces, since it's
# unambiguously delimited by the matching quote.
_SECRET_ASSIGNMENT_QUOTED = re.compile(
    rf"""(?ix)
    (?P<prefix>{_CRED_KEY}\s*[:=]\s*)
    (?P<quote>["'])
    (?!(?:{_NOT_A_SECRET_VALUE})(?=(?P=quote)))
    [^"']+
    (?P=quote)
    """
)

# `key=value` / `key: value` with no quotes -- value stops at whitespace.
_SECRET_ASSIGNMENT_BARE = re.compile(
    rf"""(?ix)
    (?P<prefix>{_CRED_KEY}\s*[:=]\s*)
    (?!(?:{_NOT_A_SECRET_VALUE})(?=[\s"']|$))
    [^\s"']{{3,}}
    """
)

# (pattern, replacement) pairs, applied in order via `pattern.subn()`. Every
# replacement is a plain template -- whole-match patterns replace everything,
# the rest keep a captured prefix/scheme and append `[SECRET]`.
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (_PRIVATE_KEY_BLOCK, "[SECRET]"),
    (_JWT, "[SECRET]"),
    (_GITHUB_TOKEN, "[SECRET]"),
    (_GITLAB_TOKEN, "[SECRET]"),
    (_SLACK_TOKEN, "[SECRET]"),
    (_AWS_ACCESS_KEY_ID, "[SECRET]"),
    (_AUTH_HEADER, r"\g<prefix>[SECRET]"),
    (_BEARER_INLINE, "Bearer [SECRET]"),
    (_URL_CREDENTIALS, r"\g<scheme>[SECRET]@"),
    (_SECRET_ASSIGNMENT_QUOTED, r"\g<prefix>\g<quote>[SECRET]\g<quote>"),
    (_SECRET_ASSIGNMENT_BARE, r"\g<prefix>[SECRET]"),
)


def redact_secrets(text: str) -> tuple[str, int]:
    """Replace likely passwords/tokens/keys in `text` with `[SECRET]`.

    Returns `(redacted_text, replacement_count)`.
    """
    count = 0
    for pattern, replacement in _REDACTIONS:
        text, n = pattern.subn(replacement, text)
        count += n
    return text, count


def write_redacted_copy(original: Path, redacted_text: str) -> Path:
    """Write `redacted_text` to a new temp file and return its path.

    Never overwrites `original` — callers may still want the un-redacted
    source file on disk for other purposes.
    """
    suffix = original.suffix or ".txt"
    fd, name = tempfile.mkstemp(prefix=f"redacted-{original.stem}-", suffix=suffix)
    with open(fd, "w", encoding="utf-8") as fh:
        fh.write(redacted_text)
    return Path(name)
