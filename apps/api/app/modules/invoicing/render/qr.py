"""The QR code an invoice carries (issue #268), in the tenant's own colours (epic #269).

What it encodes is **this invoice's page in the client portal** — not a checkout URL. Both were
on the table and the difference matters: a live provider checkout URL is a bearer credential,
and printing one on paper hands whoever picks that paper up the ability to look at (and settle)
somebody else's bill. The portal link goes through the login #193 already established, so a
scan by the right person lands on the invoice and a scan by anyone else lands on a login screen.
The cost is a redirect through sign-in, and that is the correct cost.

The document gets an inline ``<svg>``, for two reasons that are both about the renderer's
sandbox (``render/engine.py``): the document CSP allows ``img-src data:`` and nothing else, and
the Jinja environment fetches nothing at all. An ``<img src="https://…/qr.png">`` would be
blocked in the preview and silently blank in the PDF — which is also why the logo travels as a
``data:`` URI rather than as a storage URL. The **mail** gets a PNG, because Gmail strips inline
SVG: an e-mail client is not a document renderer and the raster is the only thing every one of
them draws. Both come out of one encode (:func:`_encode`), so the two surfaces can never
disagree about error level or geometry.

*Branding is not decoration here — it is the thing most likely to break the code*, so four
rules are stated once and shared by both formats:

1. **A logo raises error correction to ``H``.** Anything overlaid on the middle of an ``m``
   symbol (~15% recovery) is damage the decoder has no budget for. ``H`` (~30%) is what buys
   the hole back. This is the single most important line in the file, and it is why the level
   is derived from the logo rather than passed in.
2. **The logo covers at most 22% of the symbol's width** — ~4.8% of its area, a deliberately
   conservative slice of what ``H`` tolerates, and computed from the module count so it holds
   at every version and every scale. Never pixels: the same code prints at 24mm and displays
   at 150px.
3. **A light quiet patch sits behind the logo.** A tenant logo with a transparent background
   would otherwise leave live modules showing through it, which is worse than covering them —
   the decoder reads noise where the overlay at least reads as uniform damage. The patch snaps
   to whole modules so it never slices one in half.
4. **The dark colour must stay dark** (:func:`readable_dark`), and there is no dark mode:
   ``light`` is white, because paper is.

Deliberately **not** imported from ``app.core.auth.twofactor``: that helper exists to draw an
authenticator secret, and coupling the document renderer to the 2FA module to save six lines
would be the wrong kind of reuse.

*What* it encodes lives in :mod:`app.modules.invoicing.paylinks`, beside the mail's button and
the document's pay-online line — three surfaces offering one destination, which stays true only
while exactly one function names it.
"""

from __future__ import annotations

import base64
import io
from dataclasses import dataclass

import segno
from PIL import Image, ImageDraw, ImageOps

from app.core.documents.colors import INK, contrast_on_white, hex_rgb, rgb_hex

#: Paper is white and so is every e-mail chrome worth designing for. A dark-mode QR is a
#: request that sounds reasonable and produces an unscannable invoice, so it is not offered.
LIGHT = "#ffffff"

#: The dark colour's minimum WCAG contrast against white. ISO/IEC 15415 grades a printed
#: symbol on *reflectance* difference and calls ≥70% grade A; 4.5:1 here works out at a
#: relative luminance of ≤0.183, i.e. ~82% symbol contrast — grade A with room left over for
#: print gain, a photocopy, and a phone camera in a badly lit office. It is also exactly the
#: threshold :func:`app.core.documents.colors.document_accent` uses for small text, which is
#: the point: the code and the headings above it settle "dark enough" with one number instead
#: of each carrying a private opinion.
MIN_CONTRAST_ON_WHITE = 4.5

#: Rule 2. A fraction of the symbol's width, not its area — 0.22² ≈ 4.8% of the modules.
LOGO_WIDTH_FRACTION = 0.22

#: The standard quiet zone, in modules. The SVG omits it (``border=0``, unchanged from #268):
#: it is placed on white paper inside a 24mm box, and the surrounding page *is* the zone. A
#: PNG lands directly on an e-mail's background with no such guarantee, so it carries its own.
QUIET_ZONE_MODULES = 4

#: A logo smaller than this many modules is a smudge, and paying ``H``'s density for a smudge
#: is a worse code for no gain — such a symbol prints plain instead.
MIN_LOGO_MODULES = 3

#: The same cap ``render/context.py`` puts on an inlined image. Duplicated rather than imported
#: because ``context`` imports *this* module; a one-line constant beats an import cycle.
MAX_LOGO_BYTES = 3 * 1024 * 1024


def readable_dark(color: str | None) -> str:
    """``color`` if it scans against white, else near-black. Never raises.

    Rule 4. A brand colour is chosen to look right beside a logo, not to be binarized by a
    camera, so a pale mint or a soft yellow makes a code that is beautiful in the preview and
    unreadable in the room. Below :data:`MIN_CONTRAST_ON_WHITE` we fall back to the document's
    own ink rather than *darkening the hue* the way ``document_accent`` does for headings: a
    heading dragged down the lightness axis still reads as the brand, but a QR's colour is pure
    decoration and its legibility is judged by a machine — so on the one element where nobody
    can squint harder, the honest answer is the one that always works.

    Garbage (``None``, ``"rebeccapurple"``, ``"#12"``) resolves through ``hex_rgb``'s fallback
    to the same ink: a document that fails to print because a settings field held a typo would
    be the worse failure.
    """
    rgb = hex_rgb(color, INK)
    return rgb_hex(rgb if contrast_on_white(rgb) >= MIN_CONTRAST_ON_WHITE else INK)


@dataclass(frozen=True)
class _Logo:
    """A logo we have actually decoded once, with the media type its *bytes* claim."""

    data: bytes
    content_type: str

    @property
    def uri(self) -> str:
        # Safe unquoted in an attribute: base64 has no quote or angle bracket, and the media
        # type comes from Pillow's own table rather than from the caller.
        return f"data:{self.content_type};base64,{base64.b64encode(self.data).decode('ascii')}"


@dataclass(frozen=True)
class _Box:
    """The overlay's geometry, in **modules** — the only unit that survives a scale change."""

    patch: int  # side of the quiet patch
    logo: int  # side of the logo box inside it
    offset: int  # modules from the symbol's edge to the patch


def _looks_like_svg(data: bytes) -> bool:
    head = data[:512].lstrip(b"\xef\xbb\xbf").lstrip()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2048])


def _probe_logo(data: bytes | None, content_type: str | None, *, raster_only: bool) -> _Logo | None:
    """The one gate: bytes we have opened, or nothing.

    The declared content type is trusted to **rule out** and never to rule in — the same
    asymmetry CLAUDE.md §17 states for spreadsheets, where the format comes from the content
    and not from the filename. A caller reading a row out of storage passes whatever was
    recorded there years ago; Pillow answers what the bytes are today.

    Validating here rather than at paste time is what makes "a broken logo degrades to a plain
    code" literally true: the error level is chosen *after* this returns, so an unopenable
    logo produces bytes identical to no logo at all, rather than an ``H`` symbol with a hole
    and nothing in it.
    """
    if not data or len(data) > MAX_LOGO_BYTES:
        return None
    declared = (content_type or "").split(";")[0].strip().lower()
    if declared and not declared.startswith("image/"):
        return None
    if _looks_like_svg(data):
        # A vector logo inside a vector code is the best version of this, and WeasyPrint draws
        # it. Pillow cannot rasterise one, so the mail takes the plain code rather than a hole.
        return None if raster_only else _Logo(data, "image/svg+xml")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            mime = Image.MIME.get(probe.format or "")
    except Exception:  # an unreadable logo is a plain code, never an exception
        return None
    return _Logo(data, mime) if mime else None


def _overlay_box(modules: int) -> _Box | None:
    """Rule 2 and rule 3's alignment, from the module count alone.

    A QR symbol is always an odd number of modules across (``4·version + 17``), so the patch is
    forced odd too: matching parity is what lets it sit dead centre *on the grid* instead of
    straddling half a module on each side, which would leave a decoder reading a sliver of
    white where a dark module should be.
    """
    patch = int(modules * LOGO_WIDTH_FRACTION)
    if (modules - patch) % 2:
        patch -= 1
    logo = patch - 2  # one module of light margin all round, inside the patch
    if logo < MIN_LOGO_MODULES:
        return None
    return _Box(patch=patch, logo=logo, offset=(modules - patch) // 2)


def _encode(
    payload: str, logo: bytes | None, content_type: str | None, *, raster_only: bool
) -> tuple[segno.QRCode, _Box | None, _Logo | None]:
    """Rule 1, in one place so SVG and PNG can never disagree.

    ``h`` (~30% recovery) **only** when there is something to overlay; ``m`` (~15%) otherwise,
    unchanged from #268 — a printed invoice gets folded, stapled and photographed at an angle,
    and the level above that costs density a 24mm box has no room for. Paying ``H`` for a code
    with nothing covering it would shrink every module for nothing.
    """
    asset = _probe_logo(logo, content_type, raster_only=raster_only)
    if asset is not None:
        code = segno.make(payload, error="h")
        box = _overlay_box(len(code.matrix))
        if box is not None:
            return code, box, asset
    return segno.make(payload, error="m"), None, None


def qr_svg(
    payload: str,
    *,
    dark: str = "#000000",
    logo: bytes | None = None,
    logo_content_type: str | None = None,
    scale: int = 4,
) -> str:
    """``payload`` as an inline SVG QR code, optionally in ``dark`` with ``logo`` in the middle.

    ``xmldecl=False`` because the fragment is embedded in an HTML document, not served as a
    standalone file. The ``viewBox`` is added because segno emits ``width``/``height`` and no
    ``viewBox`` at all: without one there is no intrinsic aspect ratio, so the designs'
    ``.payment-qr-code svg { width: 100%; height: 100% }`` resizes the *viewport* and leaves a
    132-unit symbol overflowing a 24mm box instead of scaling into it.

    The composite is spliced onto segno's finished document rather than re-derived: its writer
    owns the path, and reimplementing it here to gain two child elements would be trading a
    tested encoder for a hand-rolled one.
    """
    if not payload:
        return ""
    code, box, asset = _encode(payload, logo, logo_content_type, raster_only=False)
    width, height = code.symbol_size(scale=scale, border=0)
    buffer = io.BytesIO()
    code.save(
        buffer,
        kind="svg",
        xmldecl=False,
        svgclass=None,
        lineclass=None,
        scale=scale,
        border=0,
        dark=readable_dark(dark),
    )
    svg = buffer.getvalue().decode("utf-8")
    svg = svg.replace("<svg ", f'<svg viewBox="0 0 {width} {height}" ', 1)
    if box is None or asset is None:
        return svg
    edge = box.offset * scale
    patch = box.patch * scale
    inner_edge = (box.offset + 1) * scale
    inner = box.logo * scale
    overlay = (
        f'<rect x="{edge}" y="{edge}" width="{patch}" height="{patch}" '
        f'rx="{round(patch * 0.18)}" fill="{LIGHT}"/>'
        # `meet` keeps a wordmark's aspect ratio and centres it in the square box; `href`
        # (not `xlink:href`) is what WeasyPrint's SVG reader and every browser both accept.
        f'<image x="{inner_edge}" y="{inner_edge}" width="{inner}" height="{inner}" '
        f'preserveAspectRatio="xMidYMid meet" href="{asset.uri}"/>'
    )
    return svg[: svg.rindex("</svg>")] + overlay + "</svg>"


def qr_png(
    payload: str,
    *,
    dark: str = "#000000",
    logo: bytes | None = None,
    logo_content_type: str | None = None,
    scale: int = 8,
) -> bytes:
    """``payload`` as a PNG — the same code, for the surfaces that cannot draw an SVG.

    Rendered by segno and composited with Pillow (``qrcode-artistic`` is not a dependency and
    is not worth becoming one for a paste and a rounded rectangle). ``scale=8`` because a mail
    displays this around 150px on a screen that may have twice the pixels, and a QR that has
    been resampled *up* is one a camera has to guess at.

    **A broken logo never stops an invoice going out.** Everything after the encode is wrapped:
    if Pillow opened the header in :func:`_probe_logo` and then chokes on the body, the reader
    gets the code without the logo rather than a 500 on the send path.
    """
    if not payload:
        return b""
    code, box, asset = _encode(payload, logo, logo_content_type, raster_only=True)
    buffer = io.BytesIO()
    code.save(
        buffer,
        kind="png",
        scale=scale,
        border=QUIET_ZONE_MODULES,
        dark=readable_dark(dark),
        light=LIGHT,
    )
    plain = buffer.getvalue()
    if box is None or asset is None:
        return plain
    try:
        # Opaque RGB, not RGBA: a transparent PNG over a mail client's own background is the
        # dark-mode QR rule 4 refuses, arrived at by accident.
        canvas = Image.open(io.BytesIO(plain)).convert("RGB")
        art = Image.open(io.BytesIO(asset.data))
        art.load()
        art = art.convert("RGBA")  # normalises palette-with-transparency, LA, CMYK alike
        edge = (QUIET_ZONE_MODULES + box.offset) * scale
        patch = box.patch * scale
        ImageDraw.Draw(canvas).rounded_rectangle(
            (edge, edge, edge + patch - 1, edge + patch - 1),
            radius=round(patch * 0.18),
            fill=LIGHT,
        )
        inner = box.logo * scale
        art = ImageOps.contain(art, (inner, inner), Image.Resampling.LANCZOS)
        origin = edge + scale
        canvas.paste(
            art,
            (origin + (inner - art.width) // 2, origin + (inner - art.height) // 2),
            art,  # its own alpha as the mask, so a transparent corner shows the patch
        )
        out = io.BytesIO()
        canvas.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception:  # see the docstring: the invoice ships either way
        return plain
