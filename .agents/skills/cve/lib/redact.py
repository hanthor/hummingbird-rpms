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
merely contains a credential-like word (e.g. `token_count`). Over-redaction
is the safer failure mode for a security feature, so matches are intentionally
broad.
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

_WHOLE_MATCH_PATTERNS: tuple[re.Pattern[str], ...] = (
    _PRIVATE_KEY_BLOCK,
    _JWT,
    _GITHUB_TOKEN,
    _GITLAB_TOKEN,
    _SLACK_TOKEN,
    _AWS_ACCESS_KEY_ID,
)

# ---- Patterns that keep a prefix/scheme and redact only the secret part ----

# `Authorization: Bearer <token>` / `Authorization: Basic <creds>` headers.
_AUTH_HEADER = re.compile(
    r"(?i)(authorization\s*:\s*(?:bearer|basic|token)\s+)([A-Za-z0-9\-._~+/=]+)"
)

# Bare `Bearer <token>` outside of an Authorization header line.
_BEARER_INLINE = re.compile(r"(?i)\bBearer\s+([A-Za-z0-9\-._~+/]+=*)")

# Basic-auth credentials embedded in a URL: scheme://user:pass@host
_URL_CREDENTIALS = re.compile(
    r"(?i)\b((?:https?|ftp|ssh|git)://)[^\s/@'\"]+:[^\s/@'\"]+@"
)

# Generic `key = value` / `key: value` assignments where the key name looks
# like a credential (password, token, secret, api key, etc). Matches
# snake_case and camelCase names such as `aws_secret_access_key`,
# `DB_PASSWORD`, or `apiKey`, quoted or not.
_SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    (?P<prefix>
        [\w.\-]*?
        (?:password|passwd|pwd|secret|token|api[_-]?key|apikey
          |access[_-]?key|accesskey|private[_-]?key|privatekey
          |client[_-]?secret|auth[_-]?token|session[_-]?token
          |refresh[_-]?token|bearer)
        [\w.\-]*?
        \s*[:=]\s*
    )
    (?P<quote>["']?)
    (?P<value>[^\s"']{3,})
    (?P=quote)
    """
)


def redact_secrets(text: str) -> tuple[str, int]:
    """Replace likely passwords/tokens/keys in `text` with `[SECRET]`.

    Returns `(redacted_text, replacement_count)`.
    """
    count = 0

    for pattern in _WHOLE_MATCH_PATTERNS:
        text, n = pattern.subn("[SECRET]", text)
        count += n

    def _replace_auth_header(m: re.Match[str]) -> str:
        return f"{m.group(1)}[SECRET]"

    text, n = _AUTH_HEADER.subn(_replace_auth_header, text)
    count += n

    def _replace_bearer(m: re.Match[str]) -> str:
        return "Bearer [SECRET]"

    text, n = _BEARER_INLINE.subn(_replace_bearer, text)
    count += n

    def _replace_url_credentials(m: re.Match[str]) -> str:
        return f"{m.group(1)}[SECRET]@"

    text, n = _URL_CREDENTIALS.subn(_replace_url_credentials, text)
    count += n

    def _replace_assignment(m: re.Match[str]) -> str:
        quote = m.group("quote")
        return f"{m.group('prefix')}{quote}[SECRET]{quote}"

    text, n = _SECRET_ASSIGNMENT.subn(_replace_assignment, text)
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
