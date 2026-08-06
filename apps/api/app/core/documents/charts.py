"""Charts for a printed document: inline SVG, drawn server-side, no network.

Why SVG and not an image service: ``engine.no_network_fetcher`` answers ``data:`` and raises
on everything else, deliberately — so a chart cannot be an ``<img src="https://…">``. That
constraint turns out to be the right design anyway. The n8n workflow this replaces built
QuickChart URLs (``http://quickchart:3400/chart?c=…``), which meant a second container, a
three-second render wait, a raster image in a vector document, and the client's own numbers
travelling in a URL. Inline SVG is none of those things: it prints at the printer's
resolution, costs one string concatenation, and never leaves the process.

**Everything is passed in.** No function here owns a hex (Golden Rule 4) — the accent is the
tenant's, resolved by ``colors.accent_for`` upstream. Number formatting arrives as a callable
because a document formats in *its own* locale, not the viewer's (docs/INVOICING.md).

Form choices are deliberate and two of them differ from the workflow being replaced:

* **One series → one colour, never a hue per bar.** Colouring "sessions per channel" with a
  fixed green/blue/orange/red map double-encodes bar length as hue and burns the only free
  channel on information the chart already shows. The bars are the tenant's accent; the
  channel is named on the axis.
* **Part-to-whole is a share bar, not a doughnut.** A doughnut where Google holds 95 % of the
  slices is a circle with one slice; the reader learns nothing a sentence would not tell them
  faster. A single 100 %-wide stacked bar states the same split in a quarter of the height and
  survives a black-and-white printer.

Print-specific: gridlines are solid hairlines (a dashed grid reads as a threshold), text wears
ink/muted tokens rather than the series colour, and no value is stamped on a mark — every
chart in a report sits beside the table that carries its numbers, which is the table-view
requirement met by construction.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from html import escape

from app.core.documents.colors import INK, MUTED, RGB, RULE, mix_on_white, rgb_hex

#: Bar thickness cap — a bar that fills its band reads as a block, not a mark.
_MAX_BAR = 24.0
#: The surface gap that separates touching marks. White does the separating, never a stroke.
_GAP = 2.0
#: Rounded data-end; square at the baseline.
_RADIUS = 4.0
#: Rough advance width of the document face at 1pt, for deciding whether a label fits.
_CHAR_W = 0.55

Formatter = Callable[[float], str]


def _default_format(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", ".")


@dataclass(frozen=True)
class ChartStyle:
    """The palette a chart draws in — every value resolved by the caller.

    ``accent`` is the tenant's contrast-corrected brand colour; ``comparison`` is the
    de-emphasis grey a previous period wears. They differ in *lightness* as well as hue so the
    pair still separates on a black-and-white printer, which is where a client report often
    ends up.
    """

    accent: str
    comparison: str = rgb_hex(mix_on_white(INK, 0.34))
    ink: str = rgb_hex(INK)
    muted: str = rgb_hex(MUTED)
    rule: str = rgb_hex(RULE)
    font_size: float = 8.0


def _esc(value: object) -> str:
    return escape(str(value if value is not None else ""), quote=True)


def _fits(text: str, width: float, font_size: float) -> bool:
    return len(text) * _CHAR_W * font_size + 8 <= width


def _nice_ceiling(value: float) -> tuple[float, list[float]]:
    """A clean axis top and its ticks — 0 / 1.000 / 2.000, never 0 / 837 / 1.674."""
    if value <= 0:
        return 1.0, [0.0, 1.0]
    magnitude = 10 ** (len(str(int(value))) - 1)
    for step in (1, 2, 2.5, 5, 10):
        top = step * magnitude
        if top >= value:
            divisions = 4 if step in (1, 2, 10) else 5
            return top, [top * i / divisions for i in range(divisions + 1)]
    return value, [0.0, value]


def _column_path(x: float, y: float, width: float, height: float) -> str:
    """A column with a rounded cap and a square foot."""
    if height <= 0:
        return ""
    radius = min(_RADIUS, width / 2, height)
    bottom = y + height
    return (
        f"M{x:.2f},{bottom:.2f} V{y + radius:.2f} "
        f"Q{x:.2f},{y:.2f} {x + radius:.2f},{y:.2f} "
        f"H{x + width - radius:.2f} Q{x + width:.2f},{y:.2f} "
        f"{x + width:.2f},{y + radius:.2f} V{bottom:.2f} Z"
    )


def _svg(width: float, height: float, body: str, title: str) -> str:
    """A standalone, responsive, accessible SVG fragment.

    ``font-family: inherit`` so the chart wears the document's face rather than carrying its
    own; ``role="img"`` + ``<title>`` so a screen reader on the HTML preview announces what it
    is instead of skipping an anonymous graphic.
    """
    return (
        f'<svg viewBox="0 0 {width:.0f} {height:.0f}" width="100%" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'style="font-family:inherit;display:block;max-width:100%;height:auto">'
        f"<title>{_esc(title)}</title>{body}</svg>"
    )


def _axis(
    style: ChartStyle,
    plot_left: float,
    plot_right: float,
    plot_top: float,
    plot_bottom: float,
    ticks: Sequence[float],
    top: float,
    fmt: Formatter,
) -> str:
    """Solid hairline gridlines with clean, right-aligned, tabular ticks."""
    out: list[str] = []
    height = plot_bottom - plot_top
    for tick in ticks:
        y = plot_bottom - (tick / top) * height if top else plot_bottom
        out.append(
            f'<line x1="{plot_left:.1f}" y1="{y:.1f}" x2="{plot_right:.1f}" y2="{y:.1f}" '
            f'stroke="{style.rule}" stroke-width="1" />'
        )
        out.append(
            f'<text x="{plot_left - 6:.1f}" y="{y + 3:.1f}" text-anchor="end" '
            f'font-size="{style.font_size:.1f}" fill="{style.muted}" '
            f'style="font-variant-numeric:tabular-nums">{_esc(fmt(tick))}</text>'
        )
    return "".join(out)


def _category_labels(
    style: ChartStyle, labels: Sequence[str], centres: Sequence[float], baseline: float
) -> str:
    """Category names under the baseline, rotated only when they would otherwise collide."""
    out: list[str] = []
    band = (centres[1] - centres[0]) if len(centres) > 1 else 999.0
    rotate = any(not _fits(label, band, style.font_size) for label in labels)
    for label, centre in zip(labels, centres, strict=False):
        if rotate:
            out.append(
                f'<text transform="translate({centre:.1f},{baseline + 8:.1f}) rotate(-35)" '
                f'text-anchor="end" font-size="{style.font_size:.1f}" '
                f'fill="{style.muted}">{_esc(label)}</text>'
            )
        else:
            out.append(
                f'<text x="{centre:.1f}" y="{baseline + 11:.1f}" text-anchor="middle" '
                f'font-size="{style.font_size:.1f}" fill="{style.muted}">{_esc(label)}</text>'
            )
    return "".join(out)


def _legend(style: ChartStyle, entries: Sequence[tuple[str, str]], x: float, y: float) -> str:
    """A legend is always present for two or more series — identity is never colour alone."""
    out: list[str] = []
    cursor = x
    for colour, label in entries:
        out.append(
            f'<rect x="{cursor:.1f}" y="{y - 6:.1f}" width="8" height="8" rx="2" '
            f'fill="{colour}" />'
        )
        out.append(
            f'<text x="{cursor + 12:.1f}" y="{y + 1:.1f}" font-size="{style.font_size:.1f}" '
            f'fill="{style.muted}">{_esc(label)}</text>'
        )
        cursor += 12 + len(label) * _CHAR_W * style.font_size + 18
    return "".join(out)


def column_chart(
    labels: Sequence[str],
    values: Sequence[float],
    *,
    style: ChartStyle,
    title: str = "",
    fmt: Formatter = _default_format,
    width: float = 320.0,
    height: float = 170.0,
) -> str:
    """One series, one colour. "Compare magnitude" — the bar length is the whole message."""
    labels, values = list(labels)[:12], [max(0.0, float(v or 0)) for v in list(values)[:12]]
    if not labels:
        return ""
    top, ticks = _nice_ceiling(max(values, default=0.0))
    left, right, plot_top = 44.0, width - 6.0, 8.0
    baseline = height - 34.0
    band = (right - left) / len(labels)
    bar = min(_MAX_BAR, band - _GAP * 2)
    body = [_axis(style, left, right, plot_top, baseline, ticks, top, fmt)]
    centres: list[float] = []
    for index, value in enumerate(values):
        centre = left + band * (index + 0.5)
        centres.append(centre)
        bar_height = (value / top) * (baseline - plot_top) if top else 0.0
        path = _column_path(centre - bar / 2, baseline - bar_height, bar, bar_height)
        if path:
            body.append(f'<path d="{path}" fill="{style.accent}" />')
    body.append(
        f'<line x1="{left:.1f}" y1="{baseline:.1f}" x2="{right:.1f}" y2="{baseline:.1f}" '
        f'stroke="{style.rule}" stroke-width="1" />'
    )
    body.append(_category_labels(style, labels, centres, baseline))
    return _svg(width, height, "".join(body), title)


def grouped_columns(
    labels: Sequence[str],
    series: Sequence[tuple[str, Sequence[float]]],
    *,
    style: ChartStyle,
    title: str = "",
    fmt: Formatter = _default_format,
    width: float = 320.0,
    height: float = 190.0,
) -> str:
    """Two series side by side — this period against the one it is compared with.

    Capped at two: a third grouped column per category is where a print chart stops being
    readable, and the report never has one (a period has exactly one comparison).
    """
    labels = list(labels)[:10]
    series = list(series)[:2]
    if not labels or not series:
        return ""
    columns = [[max(0.0, float(v or 0)) for v in list(vals)[:10]] for _, vals in series]
    top, ticks = _nice_ceiling(max((max(c, default=0.0) for c in columns), default=0.0))
    left, right, plot_top = 44.0, width - 6.0, 8.0
    baseline = height - 46.0
    band = (right - left) / len(labels)
    colours = [style.accent, style.comparison][: len(series)]
    bar = min(_MAX_BAR, (band - _GAP * 3) / len(series))
    body = [_axis(style, left, right, plot_top, baseline, ticks, top, fmt)]
    centres: list[float] = []
    group_width = bar * len(series) + _GAP * (len(series) - 1)
    for index in range(len(labels)):
        centre = left + band * (index + 0.5)
        centres.append(centre)
        for slot, values in enumerate(columns):
            value = values[index] if index < len(values) else 0.0
            bar_height = (value / top) * (baseline - plot_top) if top else 0.0
            x = centre - group_width / 2 + slot * (bar + _GAP)
            path = _column_path(x, baseline - bar_height, bar, bar_height)
            if path:
                body.append(f'<path d="{path}" fill="{colours[slot]}" />')
    body.append(
        f'<line x1="{left:.1f}" y1="{baseline:.1f}" x2="{right:.1f}" y2="{baseline:.1f}" '
        f'stroke="{style.rule}" stroke-width="1" />'
    )
    body.append(_category_labels(style, labels, centres, baseline))
    body.append(
        _legend(
            style,
            [(colours[i], name) for i, (name, _) in enumerate(series)],
            left,
            height - 6.0,
        )
    )
    return _svg(width, height, "".join(body), title)


def share_bar(
    items: Sequence[tuple[str, float]],
    *,
    style: ChartStyle,
    title: str = "",
    other_label: str = "",
    width: float = 320.0,
    height: float = 62.0,
    max_segments: int = 5,
) -> str:
    """Part-to-whole as one 100 %-wide stacked bar, with a legend under it.

    Segments are tints of the *tenant's* accent, light → dark by share, so it stays one hue
    (a rainbow of six brand-unrelated colours for "which search engine" says nothing the
    order does not) and it degrades to distinguishable greys in black and white. The tail
    folds into one segment rather than growing an eighth colour nobody can name.
    """
    ranked = sorted(
        ((str(label), max(0.0, float(value or 0))) for label, value in items),
        key=lambda pair: pair[1],
        reverse=True,
    )
    total = sum(value for _, value in ranked)
    if total <= 0:
        return ""
    head = ranked[:max_segments]
    tail = sum(value for _, value in ranked[max_segments:])
    if tail > 0 and other_label:
        head.append((other_label, tail))
    left, right = 4.0, width - 4.0
    bar_top, bar_height = 8.0, 18.0
    span = right - left
    body: list[str] = []
    legend: list[tuple[str, str]] = []
    cursor = left
    steps = max(len(head) - 1, 1)
    for index, (label, value) in enumerate(head):
        fraction = value / total
        # The trailing 2px gap comes out of each segment, so the bar still ends flush right.
        segment = max(0.0, span * fraction - (_GAP if index < len(head) - 1 else 0.0))
        weight = 1.0 - 0.62 * (index / steps)
        colour = rgb_hex(mix_on_white(_hex_rgb(style.accent), weight))
        body.append(
            f'<rect x="{cursor:.2f}" y="{bar_top:.1f}" width="{segment:.2f}" '
            f'height="{bar_height:.1f}" rx="2" fill="{colour}" />'
        )
        percent = f"{fraction * 100:.0f}%"
        if _fits(percent, segment, style.font_size):
            # Inside a colored fill is the one place a label may not wear an ink token: pick
            # white or ink by the fill's own luminance so it always clears contrast.
            fill = "#ffffff" if weight > 0.55 else style.ink
            body.append(
                f'<text x="{cursor + segment / 2:.2f}" y="{bar_top + 12.5:.1f}" '
                f'text-anchor="middle" font-size="{style.font_size:.1f}" fill="{fill}" '
                f'style="font-variant-numeric:tabular-nums">{percent}</text>'
            )
        legend.append((colour, f"{label} {percent}"))
        cursor += segment + _GAP
    body.append(_legend(style, legend, left, height - 8.0))
    return _svg(width, height, "".join(body), title)


def sparkline(
    values: Sequence[float],
    *,
    style: ChartStyle,
    title: str = "",
    width: float = 96.0,
    height: float = 22.0,
) -> str:
    """A 2px trend line with an end marker — the stat-tile companion, not a chart of its own."""
    points = [float(v or 0) for v in values][-40:]
    if len(points) < 2:
        return ""
    low, high = min(points), max(points)
    span = (high - low) or 1.0
    step = (width - 6) / (len(points) - 1)

    def y_of(value: float) -> float:
        return height - 4 - ((value - low) / span) * (height - 9)

    path = " ".join(
        f"{'M' if i == 0 else 'L'}{3 + i * step:.2f},{y_of(v):.2f}"
        for i, v in enumerate(points)
    )
    end_x, end_y = 3 + (len(points) - 1) * step, y_of(points[-1])
    return _svg(
        width,
        height,
        f'<path d="{path}" fill="none" stroke="{style.accent}" stroke-width="2" '
        f'stroke-linejoin="round" stroke-linecap="round" />'
        f'<circle cx="{end_x:.2f}" cy="{end_y:.2f}" r="2.5" fill="{style.accent}" '
        f'stroke="#ffffff" stroke-width="2" />',
        title,
    )


def _hex_rgb(value: str) -> RGB:
    from app.core.documents.colors import hex_rgb

    return hex_rgb(value, INK)
