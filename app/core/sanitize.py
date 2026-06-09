"""
Dependency-free input sanitization for user-supplied free-text fields.

This is a defense-in-depth measure against stored XSS (OWASP A03). The POS and
admin frontends render some user-controlled strings; stripping HTML at the API
boundary guarantees no markup is ever persisted, regardless of how a given
client renders it. The frontend should still treat all data as untrusted
(DOMPurify / React's default escaping).
"""
import re
from typing import Optional
from pydantic import BeforeValidator
from typing_extensions import Annotated

# Matches any complete HTML/XML-style tag, e.g. <script>, </b>, <img src=x onerror=...>
_TAG_RE = re.compile(r"<[^>]*?>")
# Matches HTML entities that could be used to smuggle markup back in
_ENTITY_RE = re.compile(r"&(#x?[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});")


def sanitize_text(value: Optional[str]) -> Optional[str]:
    """Strip HTML tags and neutralize stray angle brackets from free text.

    Removes complete tags first, then deletes any leftover '<' or '>' so a
    partial/truncated tag (e.g. ``<img src=x onerror=alert(1)``) can never be
    reassembled into executable markup. Non-string input is returned unchanged
    so Pydantic type validation still runs afterwards.
    """
    if not isinstance(value, str):
        return value
    cleaned = _TAG_RE.sub("", value)
    cleaned = cleaned.replace("<", "").replace(">", "")
    return cleaned.strip()


# Reusable annotated type: use as `name: SafeStr` in Pydantic schemas.
SafeStr = Annotated[str, BeforeValidator(sanitize_text)]
SafeOptStr = Annotated[Optional[str], BeforeValidator(sanitize_text)]
