"""Text normalization for CER scoring.

Applies `STT-PIPELINE-SPEC.md §5.1` to both hypothesis and reference identically:
    1. Unicode NFC
    2. remove `[INAUDIBLE]` token (case-insensitive)
    3. strip punctuation set
    4. lowercase Latin letters
    5. keep digits as-is (no Korean conversion)
    6. strip all whitespace (including U+3000)
"""

from __future__ import annotations

import re
import unicodedata

# §5.1 step 2 — drop INAUDIBLE markers entirely (case-insensitive).
_INAUDIBLE_RE = re.compile(r"\[INAUDIBLE\]", re.IGNORECASE)

# §5.1 step 3 — punctuation set (ASCII + common CJK). Defined as a
# character set so order is irrelevant; one regex pass strips them all.
_PUNCT_CHARS = (
    r"""."""
    r""","""
    r"""?!…"""
    r"""「」『』()"'`~@#$%^&*_+=\-:;/\\<>|"""
    r"""·、。"""
    r"""“”‘’"""  # “ ” ‘ ’
    r"""《》〈〉【】〔〕"""
)
_PUNCT_RE = re.compile("[" + re.escape(_PUNCT_CHARS) + "]")

# §5.1 step 6 — all Unicode whitespace incl. U+3000 ideographic space.
_WHITESPACE_RE = re.compile(r"[\s　]+", re.UNICODE)


def normalize(text: str) -> str:
    if text is None:
        return ""
    s = unicodedata.normalize("NFC", text)
    s = _INAUDIBLE_RE.sub("", s)
    s = _PUNCT_RE.sub("", s)
    s = s.lower()
    s = _WHITESPACE_RE.sub("", s)
    return s
