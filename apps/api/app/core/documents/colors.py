"""Paper colour maths, shared by every document a tenant's brand appears on.

Lived in ``modules/invoicing/render/colors.py`` until reporting needed the same answers
(issue #300). It is core for the reason CLAUDE.md §6 gives: a module may not import another
module's internals, and "what hex does this tenant's accent print as" is not invoicing's
question — it is every document's. Mirrors ``documentAccent()`` in
``apps/web/src/lib/modules/invoicing/types.ts``, which the web still uses for swatches.
"""

from __future__ import annotations

import colorsys

RGB = tuple[int, int, int]

INK: RGB = (23, 23, 23)  # --text
MUTED: RGB = (115, 115, 115)  # --text-muted
RULE: RGB = (229, 229, 229)  # --border
WASH: RGB = (250, 250, 250)  # --surface
DEFAULT_ACCENT = "#4f46e5"


def hex_rgb(value: str | None, fallback: RGB) -> RGB:
    raw = (value or "").lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        return fallback
    try:
        return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


def rgb_hex(color: RGB) -> str:
    return "#" + "".join(f"{max(0, min(255, int(c))):02x}" for c in color)


def rgba(color: RGB, alpha: float) -> str:
    return f"rgba({color[0]}, {color[1]}, {color[2]}, {alpha:g})"


def luminance(color: RGB) -> float:
    """WCAG relative luminance."""

    def channel(value: int) -> float:
        s = value / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_on_white(color: RGB) -> float:
    return 1.05 / (luminance(color) + 0.05)


def document_accent(color: RGB) -> RGB:
    """The tenant's colour, darkened in HSL until it reads on paper — hue preserved.

    Paper is white, so a pale-yellow or mint brand would otherwise print an invisible heading
    and an unreadable section rule. The threshold is 4.5:1 because the accent carries *small*
    text (section labels, the total), not only rules.
    """
    if contrast_on_white(color) >= 4.5:
        return color
    r, g, b = (c / 255 for c in color)
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    current = color
    for _ in range(24):
        if contrast_on_white(current) >= 4.5:
            break
        lightness = max(lightness - 0.04, 0.08)
        current = tuple(  # type: ignore[assignment]
            round(c * 255) for c in colorsys.hls_to_rgb(hue, lightness, saturation)
        )
    return current


def accent_for(template_color: str | None, brand_color: str | None) -> str:
    """The document's accent: the template's, else the tenant's brand, else the app's indigo.

    Branding is runtime (Golden Rule 4) — nothing downstream of here may hold a default hex.
    """
    return rgb_hex(
        document_accent(hex_rgb(template_color, hex_rgb(brand_color, hex_rgb(DEFAULT_ACCENT, INK))))
    )


def mix_on_white(color: RGB, weight: float) -> RGB:
    """``color`` at ``weight`` over white — a tint, not a transparency.

    A PDF prints what it is given, so a wash that must survive on paper is computed here
    rather than left to ``rgba()`` compositing.
    """
    weight = max(0.0, min(1.0, weight))
    return tuple(round(255 + (c - 255) * weight) for c in color)  # type: ignore[return-value]
