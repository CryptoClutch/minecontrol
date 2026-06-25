"""
Safe template substitution for user-supplied pool_url / worker_name_template
strings. Deliberately avoids str.format(), since format() on untrusted
templates allows attribute/index access tricks like "{0.__class__.__mro__}"
that can leak internals or behave unpredictably. This only supports flat
{name} placeholders from a fixed allow-list - nothing else is ever resolved.
"""

import re

_PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def safe_format(template: str, values: dict[str, str]) -> str:
    """
    Replace {key} placeholders in `template` using only the provided
    `values` dict. Unknown placeholders are left as-is (so a typo doesn't
    silently vanish - it'll show up clearly in the resolved output instead).
    """
    def replace(match):
        key = match.group(1)
        return values.get(key, match.group(0))

    return _PLACEHOLDER_RE.sub(replace, template)
