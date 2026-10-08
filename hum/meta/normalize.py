"""Field normalization for hum.meta.

normalize_cve_product() matches that real implementation's observable output
for every input shape, despite a differently-structured code path (this one
always attempts json.loads() rather than gating on a "[" prefix first; the
two are equivalent once a string is stripped, because valid JSON can only
decode to a list if it starts with "[").

normalize_fix_status() is a deliberate, narrower exception: the real
implementation does bare int(value) coercion, so it also accepts bool and
float (True/False, 1.0/0.0, and even silently truncates 1.5 -> 1). This
library intentionally rejects those -- see the design doc's section 8 for
the full reasoning -- confirmed safe against real on-disk data (every
metadata/*.json file has at most a plain int 0/1 for this field today, never
a bool or float), but genuinely narrower, not just a stricter version of the
same rule.
"""

from __future__ import annotations

import json
from collections.abc import Sequence


def normalize_cve_product(value: object) -> str | list[str] | None:
    """Normalizes a raw cve_product value into canonical str-or-list-of-str
    form. Accepts a plain string, a JSON-encoded list string, a
    semicolon-separated string, or an actual list (ignoring blank/non-string
    entries). Returns None for anything unrecognized."""
    if isinstance(value, list):
        return _collapse_product_list(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = json.loads(stripped)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return _collapse_product_list(parsed)
        if ";" in stripped:
            return _collapse_product_list(stripped.split(";"))
        return stripped
    return None


def _collapse_product_list(raw: Sequence[object]) -> str | list[str] | None:
    """Filters a raw list down to non-blank strings, then collapses a
    single remaining entry to a plain string -- the canonical form has no
    single-item-list case for callers to handle separately."""
    products = [item.strip() for item in raw if isinstance(item, str) and item.strip()]
    if not products:
        return None
    if len(products) == 1:
        return products[0]
    return products


def normalize_fix_status(value: object) -> int | None:
    """Coerces a raw fix_status value to 0 or 1, or None for anything else."""
    # isinstance(value, int) alone isn't enough: bool is an int subclass
    # (True == 1), and plain `value in (0, 1, ...)` would also accept 1.0
    # (1.0 == 1) -- both need excluding explicitly, not just value-equality.
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value in (0, 1):
        return value
    if isinstance(value, str) and value in ("0", "1"):
        return int(value)
    return None
