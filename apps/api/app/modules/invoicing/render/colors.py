"""Paper colour maths — moved to :mod:`app.core.documents.colors` (issue #300).

Re-exported here rather than deleted: the names are referenced from this module path in
``context.py`` and in the tests, and the invoice designs are the reason most of them exist.
The maths has exactly one home, which is the point of the move — the reporting module needs
the same contrast-corrected accent and may not import invoicing to get it (CLAUDE.md §6).
"""

from __future__ import annotations

from app.core.documents.colors import (
    DEFAULT_ACCENT,
    INK,
    MUTED,
    RGB,
    RULE,
    WASH,
    accent_for,
    contrast_on_white,
    document_accent,
    hex_rgb,
    luminance,
    mix_on_white,
    rgb_hex,
    rgba,
)

__all__ = [
    "DEFAULT_ACCENT",
    "INK",
    "MUTED",
    "RGB",
    "RULE",
    "WASH",
    "accent_for",
    "contrast_on_white",
    "document_accent",
    "hex_rgb",
    "luminance",
    "mix_on_white",
    "rgb_hex",
    "rgba",
]
