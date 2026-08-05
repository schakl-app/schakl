"""The branded QR renderer (epic #269, on top of #268's plain code).

Split out of ``test_invoicing_render`` because it guards a different kind of thing. That suite
asks which pieces print and in what order — a failure there is a screenshot away. This one asks
whether a scanner can still read the result, and *that* fails silently: a code with the brand's
pale yellow in it, or a logo two modules too wide, renders beautifully, passes every layout
assertion, and is a phone that will not focus at the client's desk. So the invariants that keep
it scannable get a named test each.

**Decoding is not verified here, on purpose.** Neither ``pyzbar`` nor ``opencv`` is installed
and neither is worth adding: ``pyzbar`` needs the ``libzbar`` system library (a shared object in
every image that builds this API, for one assertion) and ``opencv-python`` is ~90MB of wheel.
What these tests assert instead is the *structure* the standard's error budget is spent on —
the error level, the covered fraction, the opaque patch, the quiet zone and the ink's contrast.
Those are the four things a change to this file can plausibly get wrong; a decoder would mostly
re-prove that ``segno`` works. If a decoder ever lands in the image for another reason, the
honest upgrade is to add one test here that scans ``qr_png`` and reads the payload back.
"""

from __future__ import annotations

import io
from xml.etree import ElementTree

import segno
from PIL import Image

from app.modules.invoicing.render.qr import (
    LOGO_WIDTH_FRACTION,
    QUIET_ZONE_MODULES,
    qr_png,
    qr_svg,
    readable_dark,
)

PAY_URL = "https://bureau.schakl.app/invoices/8f1c2a90-0c9c-4a1a-9a1a-000000000042"
BRAND = "#4f46e5"
BRAND_RGB = (79, 70, 229)
LOGO_RGB = (200, 30, 30)
SVG_NS = "{http://www.w3.org/2000/svg}"


def _logo(size: tuple[int, int] = (60, 30), *, transparent: bool = False) -> bytes:
    """A tenant logo as PNG bytes — deliberately *not* square, like most wordmarks."""
    image = Image.new("RGBA", size, (*LOGO_RGB, 0 if transparent else 255))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _shapes(svg: str) -> tuple[dict, list[dict], list[dict]]:
    root = ElementTree.fromstring(svg)
    rects = [el.attrib for el in root.iter(f"{SVG_NS}rect")]
    images = [el.attrib for el in root.iter(f"{SVG_NS}image")]
    return root.attrib, rects, images


def _png(raw: bytes) -> Image.Image:
    return Image.open(io.BytesIO(raw)).convert("RGB")


# --------------------------------------------------------------------------- #
# Rule 1 — the level rises to H, and only when it has to
# --------------------------------------------------------------------------- #


def test_a_logo_raises_the_error_level_to_h() -> None:
    """The single most important line in ``qr.py``. Covering the middle of an ``m`` symbol
    (~15% recovery) is damage the decoder has no budget for; ``H`` (~30%) buys the hole back.

    Asserted through the symbol's size, which is the observable consequence: the same payload
    needs a bigger version at ``H``, so "the branded code is exactly the H symbol for this URL"
    is checkable from the output without reaching into the encoder.
    """
    branded, _, _ = _shapes(qr_svg(PAY_URL, dark=BRAND, logo=_logo()))
    plain, _, _ = _shapes(qr_svg(PAY_URL, dark=BRAND))
    assert branded["width"] == str(segno.make(PAY_URL, error="h").symbol_size(scale=4, border=0)[0])
    assert plain["width"] == str(segno.make(PAY_URL, error="m").symbol_size(scale=4, border=0)[0])
    assert branded["width"] != plain["width"], "H must cost density; equal sizes means it did not"


def test_a_code_with_no_logo_never_pays_for_h() -> None:
    """``m`` is #268's choice and it stays: a printed invoice is folded, stapled and shot at an
    angle, but the level above that costs modules a 24mm box has nowhere to put. Redundancy is
    bought for a hole, so with no hole there is nothing to buy."""
    assert qr_svg(PAY_URL) == qr_svg(PAY_URL, logo=None)
    assert qr_png(PAY_URL) == qr_png(PAY_URL, logo=None)


def test_a_symbol_too_small_to_carry_a_logo_prints_plain() -> None:
    """A logo of one or two modules is a smudge, and ``H``'s density is a real cost paid for
    it. Below the floor the overlay is dropped *and* the level drops back with it — otherwise
    the code would be denser for an overlay that never got drawn."""
    assert qr_svg("x", logo=_logo()) == qr_svg("x")
    assert qr_png("x", logo=_logo()) == qr_png("x")


# --------------------------------------------------------------------------- #
# Rule 2 — how much of the code the logo is allowed to eat
# --------------------------------------------------------------------------- #


def test_the_overlay_covers_at_most_a_fifth_of_the_code_and_is_centred() -> None:
    """22% of the *width* is ~4.8% of the area — a conservative slice of what ``H`` tolerates,
    with the rest of the budget left for the fold, the staple and the camera angle. Measured
    off the rendered geometry rather than the constant, because the failure this guards against
    is a rounding change in ``_overlay_box``, which a constant cannot see."""
    root, rects, images = _shapes(qr_svg(PAY_URL, dark=BRAND, logo=_logo()))
    width = float(root["width"])
    patch, logo = rects[0], images[0]
    assert float(patch["width"]) / width <= LOGO_WIDTH_FRACTION
    assert float(logo["width"]) < float(patch["width"]), "the patch must out-margin the logo"
    # Square and dead centre, in both axes — an off-centre overlay damages the timing pattern
    # on one side while leaving the other untouched, which is the worst way to spend the budget.
    for shape in (patch, logo):
        assert shape["width"] == shape["height"], "a square box; the logo is fitted inside it"
        assert float(shape["x"]) == float(shape["y"])
        assert float(shape["x"]) * 2 + float(shape["width"]) == width


def test_the_overlay_is_measured_in_modules_not_pixels() -> None:
    """The same code prints at 24mm and displays at 150px, so the covered *fraction* has to
    survive a scale change. Doubling the scale doubles every coordinate and nothing else."""
    small = _shapes(qr_svg(PAY_URL, logo=_logo(), scale=4))
    large = _shapes(qr_svg(PAY_URL, logo=_logo(), scale=8))
    assert float(large[0]["width"]) == 2 * float(small[0]["width"])
    assert float(large[1][0]["width"]) == 2 * float(small[1][0]["width"])
    assert float(large[2][0]["x"]) == 2 * float(small[2][0]["x"])


# --------------------------------------------------------------------------- #
# Rule 3 — the quiet patch
# --------------------------------------------------------------------------- #


def test_a_light_patch_sits_behind_the_logo() -> None:
    """A logo with a transparent background would otherwise leave live modules showing through
    it. That is worse than covering them: uniform damage is what the error budget is *for*,
    while a half-visible module is noise the binarizer has to guess at."""
    _, rects, _ = _shapes(qr_svg(PAY_URL, dark=BRAND, logo=_logo()))
    assert rects and rects[0]["fill"] == "#ffffff"
    assert float(rects[0].get("rx", 0)) > 0, "a rounded patch, not a hard square"


def test_the_patch_is_opaque_even_under_a_fully_transparent_logo() -> None:
    """The PNG half of the same rule, where it is actually observable: paste with the logo's
    own alpha as the mask and a transparent logo shows the patch, not the modules under it."""
    image = _png(qr_png(PAY_URL, dark=BRAND, logo=_logo(transparent=True)))
    width, height = image.size
    assert image.getpixel((width // 2, height // 2)) == (255, 255, 255)


def test_the_logo_lands_in_the_middle_of_the_png_keeping_its_shape() -> None:
    """A wordmark is wider than it is tall and must not be squashed into the square box.

    Counted rather than sampled, because "centred" and "not stretched" are the two things a
    paste gets wrong and a single pixel proves neither: a 60×30 logo fitted into the box paints
    exactly twice as many pixels across the middle row as down the middle column, and the
    column's remainder is the patch showing above and below it.
    """
    image = _png(qr_png(PAY_URL, dark=BRAND, logo=_logo()))
    width, height = image.size
    assert image.getpixel((width // 2, height // 2)) == LOGO_RGB
    row = [image.getpixel((x, height // 2)) for x in range(width)]
    column = [image.getpixel((width // 2, y)) for y in range(height)]
    assert row.count(LOGO_RGB) == 2 * column.count(LOGO_RGB)
    assert column.count((255, 255, 255)) > 0, "the patch, above and below a short logo"


# --------------------------------------------------------------------------- #
# Rule 4 — the ink
# --------------------------------------------------------------------------- #


def test_readable_dark_passes_a_brand_colour_that_scans() -> None:
    """The whole point of the feature: a tenant's own colour on the code, not a black square
    with a policy attached. Anything at or above 4.5:1 on white is ~82% symbol contrast, well
    inside ISO/IEC 15415's grade A (≥70%)."""
    assert readable_dark(BRAND) == BRAND
    assert readable_dark("#000000") == "#000000"
    assert readable_dark("#123") == "#112233", "shorthand hex resolves, it does not fall back"


def test_readable_dark_refuses_a_colour_the_camera_would_lose() -> None:
    """A soft yellow reads as *lighter than the paper's shadow* to a binarizer. Falling back to
    the document's own ink is the difference between a pretty code and a code."""
    assert readable_dark("#ffe066") == "#171717"
    assert readable_dark("#ffffff") == "#171717"


def test_readable_dark_never_raises_on_a_settings_field_someone_typed_into() -> None:
    """Branding is runtime data (Golden Rule 4), so this eventually gets a colour picker's
    output, a pasted ``rgb()`` string and an empty field. An invoice that fails to render
    because of a typo in a theme setting is the worse failure by a wide margin."""
    assert readable_dark(None) == "#171717"
    assert readable_dark("") == "#171717"
    assert readable_dark("rebeccapurple") == "#171717"
    assert readable_dark("#12") == "#171717"


def test_the_brand_colour_reaches_the_code_and_a_pale_one_does_not() -> None:
    """``readable_dark`` is applied at the seam, not left to the caller — both formats."""
    assert 'stroke="#4f46e5"' in qr_svg(PAY_URL, dark=BRAND)
    assert "#ffe066" not in qr_svg(PAY_URL, dark="#ffe066")
    assert 'stroke="#171717"' in qr_svg(PAY_URL, dark="#ffe066")
    # A finder pattern's outer corner is dark in every symbol, so it is the one pixel whose
    # colour is knowable without decoding anything.
    quiet = 4 * 8
    assert _png(qr_png(PAY_URL, dark=BRAND)).getpixel((quiet + 2, quiet + 2)) == BRAND_RGB
    assert _png(qr_png(PAY_URL, dark="#ffe066")).getpixel((quiet + 2, quiet + 2)) == (23, 23, 23)


# --------------------------------------------------------------------------- #
# The SVG's own contract: a data URI, and an intrinsic size
# --------------------------------------------------------------------------- #


def test_the_logo_travels_as_a_data_uri_or_not_at_all() -> None:
    """The document CSP allows ``img-src data:`` and nothing else, and the Jinja environment
    fetches nothing — so an external URL would be blocked in the preview and blank in the PDF.
    A code with no logo carries no ``<image>`` at all rather than an empty one."""
    branded = qr_svg(PAY_URL, dark=BRAND, logo=_logo(), logo_content_type="image/png")
    _, _, images = _shapes(branded)
    assert images[0]["href"].startswith("data:image/png;base64,")
    plain = qr_svg(PAY_URL, dark=BRAND)
    assert "data:" not in plain and "<image" not in plain


def test_the_declared_content_type_may_rule_out_but_never_rule_in() -> None:
    """What the bytes are beats what a stored row says they are (CLAUDE.md §17's rule for
    spreadsheets, same reason). A PNG mislabelled ``image/jpeg`` still renders as a PNG; a
    caller who says this is a PDF is believed, because that claim can only lose us a logo."""
    mislabelled = qr_svg(PAY_URL, logo=_logo(), logo_content_type="image/jpeg")
    assert "data:image/png;base64," in mislabelled
    assert qr_svg(PAY_URL, logo=_logo(), logo_content_type="application/pdf") == qr_svg(PAY_URL)


def test_the_svg_has_an_intrinsic_aspect_ratio_so_css_can_size_it() -> None:
    """``segno`` emits ``width``/``height`` and no ``viewBox``. Without one there is nothing to
    scale: the designs' ``.payment-qr-code svg { width: 100%; height: 100% }`` resizes the
    viewport and leaves a 132-unit symbol overflowing a 24mm box instead of fitting into it."""
    for svg in (qr_svg(PAY_URL), qr_svg(PAY_URL, logo=_logo())):
        root, _, _ = _shapes(svg)
        assert root["viewBox"] == f"0 0 {root['width']} {root['height']}"


def test_an_empty_payload_renders_nothing_in_either_format() -> None:
    """There is no such thing as a QR for "no destination"; #268's caller gates on the URL and
    this is the second line of that same defence."""
    assert qr_svg("") == ""
    assert qr_png("") == b""


# --------------------------------------------------------------------------- #
# The PNG's own contract: e-mail cannot draw an SVG
# --------------------------------------------------------------------------- #


def test_the_png_is_a_real_square_png() -> None:
    """Gmail strips inline SVG, so the mail's code is a raster or it is nothing. Squareness is
    not cosmetic: a mail client that receives one dimension and infers the other from a
    stretched box would hand the camera a rectangle no decoder will read."""
    for raw in (qr_png(PAY_URL), qr_png(PAY_URL, dark=BRAND, logo=_logo())):
        assert raw[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = _png(raw).size
        assert width == height


def test_the_png_carries_its_own_quiet_zone() -> None:
    """The SVG's zone is the white page around it; a PNG lands on whatever background an e-mail
    client paints, so it brings the standard's four modules with it. Checked as size (the
    symbol plus eight modules) and as a white corner."""
    modules = len(segno.make(PAY_URL, error="m").matrix)
    image = _png(qr_png(PAY_URL, dark=BRAND, scale=8))
    assert image.size == ((modules + 8) * 8, (modules + 8) * 8)
    assert image.getpixel((0, 0)) == (255, 255, 255)
    assert image.getpixel((image.size[0] - 1, 0)) == (255, 255, 255)


def test_the_composite_damages_only_the_box_it_claims() -> None:
    """The closest thing to decoding this suite can honestly do (see the module docstring).

    Every module *outside* the overlay is compared against ``segno``'s own matrix, so the
    branded code is proved to be the H symbol with exactly one rectangle of damage in it — no
    stray patch, no off-by-one paste, no logo bleeding over the timing pattern. Given ``H``
    recovers ~30% and that rectangle is ~4.8% of the area, a decoder has nothing left to
    object to; what it would add is confidence in ``segno``, which is not our code.
    """
    scale = 8
    matrix = segno.make(PAY_URL, error="h").matrix
    # At scale 1 an SVG user unit *is* a module, so the overlay's own output states the box.
    _, rects, _ = _shapes(qr_svg(PAY_URL, dark=BRAND, logo=_logo(), scale=1))
    low = int(float(rects[0]["x"]))
    high = low + int(float(rects[0]["width"]))
    image = _png(qr_png(PAY_URL, dark=BRAND, logo=_logo(), scale=scale))
    damaged = 0
    for row, modules in enumerate(matrix):
        for column, dark in enumerate(modules):
            if low <= row < high and low <= column < high:
                damaged += 1
                continue
            centre = (QUIET_ZONE_MODULES + column) * scale + scale // 2
            pixel = image.getpixel((centre, (QUIET_ZONE_MODULES + row) * scale + scale // 2))
            assert (pixel == BRAND_RGB) == bool(dark), f"module {row},{column} was altered"
    assert damaged / (len(matrix) ** 2) <= LOGO_WIDTH_FRACTION**2


def test_the_branded_png_is_bigger_than_the_plain_one() -> None:
    """Both consequences of the logo at once: ``H`` needs more modules, and a photograph of a
    logo does not compress like a two-colour grid. A branded code that came out identical in
    size would mean the overlay never happened."""
    plain, branded = qr_png(PAY_URL), qr_png(PAY_URL, dark=BRAND, logo=_logo())
    assert _png(branded).size > _png(plain).size
    assert len(branded) > len(plain)


# --------------------------------------------------------------------------- #
# Degradation — a logo must never be able to stop an invoice
# --------------------------------------------------------------------------- #


def test_a_logo_that_cannot_be_opened_degrades_to_a_plain_code() -> None:
    """Byte-identical to no logo, which is the strongest available statement of "degraded
    cleanly": it means the level dropped back to ``m`` too, rather than leaving an ``H`` symbol
    with a white hole and nothing in it. The gate runs *before* the encode for exactly this.

    An invoice is a legal document with a due date. A logo row that turns out to be a truncated
    upload from 2023 is a cosmetic problem, and raising here would promote it into an unsendable
    invoice — the send path is the last place that trade is worth making.
    """
    for junk in (b"not an image", b"\x89PNG\r\n\x1a\n" + b"truncated", b"", b"%PDF-1.7"):
        assert qr_svg(PAY_URL, dark=BRAND, logo=junk) == qr_svg(PAY_URL, dark=BRAND)
        assert qr_png(PAY_URL, dark=BRAND, logo=junk) == qr_png(PAY_URL, dark=BRAND)


def test_an_oversized_logo_is_refused_rather_than_inlined() -> None:
    """A data URI is base64 inside the document itself, so a 20MB logo is a 27MB invoice. The
    same cap ``render/context.py`` puts on the letterhead, for the same reason."""
    huge = b"\x89PNG\r\n\x1a\n" + b"\x00" * (3 * 1024 * 1024)
    assert qr_svg(PAY_URL, logo=huge) == qr_svg(PAY_URL)


def test_a_vector_logo_is_drawn_in_the_svg_and_skipped_in_the_png() -> None:
    """A vector logo inside a vector code is the best version of this and WeasyPrint draws it.
    Pillow cannot rasterise one, so rather than an ``H`` symbol with an empty hole in it the
    mail takes the plain code — the divergence is one flag with one reason."""
    vector = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>'
    assert "data:image/svg+xml;base64," in qr_svg(PAY_URL, logo=vector)
    assert qr_png(PAY_URL, logo=vector) == qr_png(PAY_URL)


def test_the_code_is_deterministic_and_a_function_of_its_payload() -> None:
    """#268's assertion, kept alive through the rewrite: a renderer that returned the same
    bytes for two different URLs would pass every other test in this file."""
    other = "https://bureau.schakl.app/invoices/8f1c2a90-0c9c-4a1a-9a1a-000000000043"
    assert qr_svg(PAY_URL, dark=BRAND, logo=_logo()) == qr_svg(PAY_URL, dark=BRAND, logo=_logo())
    assert qr_png(PAY_URL, dark=BRAND, logo=_logo()) == qr_png(PAY_URL, dark=BRAND, logo=_logo())
    assert qr_svg(PAY_URL, dark=BRAND) != qr_svg(other, dark=BRAND)
    assert qr_png(PAY_URL, dark=BRAND) != qr_png(other, dark=BRAND)
