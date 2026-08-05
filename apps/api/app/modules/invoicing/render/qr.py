"""The QR code an invoice carries (issue #268).

What it encodes is **this invoice's page in the client portal** — not a checkout URL. Both were
on the table and the difference matters: a live provider checkout URL is a bearer credential,
and printing one on paper hands whoever picks that paper up the ability to look at (and settle)
somebody else's bill. The portal link goes through the login #193 already established, so a
scan by the right person lands on the invoice and a scan by anyone else lands on a login screen.
The cost is a redirect through sign-in, and that is the correct cost.

It is a plain inline ``<svg>``, for two reasons that are both about the renderer's sandbox
(``render/engine.py``): the document CSP allows ``img-src data:`` and nothing else, and the
Jinja environment fetches nothing at all. An ``<img src="https://…/qr.png">`` would be blocked
in the preview and silently blank in the PDF. ``segno`` is already a dependency — the 2FA
enrolment screen uses it the same way — so this adds nothing to install.

Deliberately **not** imported from ``app.core.auth.twofactor``: that helper exists to draw an
authenticator secret, and coupling the document renderer to the 2FA module to save six lines
would be the wrong kind of reuse.
"""

from __future__ import annotations

import io

import segno


def qr_svg(payload: str, *, scale: int = 4) -> str:
    """``payload`` as an inline SVG QR code.

    Error correction ``m`` (~15%): a printed invoice gets folded, stapled and photographed at
    an angle, and the next level up would cost density this has no room for at ``28mm``.
    ``xmldecl=False`` because the fragment is embedded in an HTML document, not served as a
    standalone file.
    """
    if not payload:
        return ""
    buffer = io.BytesIO()
    segno.make(payload, error="m").save(
        buffer, kind="svg", xmldecl=False, svgclass=None, lineclass=None, scale=scale, border=0
    )
    return buffer.getvalue().decode("utf-8")


def invoice_portal_url(base_url: str, invoice_id: object) -> str:
    """Where a scan should land: this invoice, on the tenant's own canonical host.

    ``base_url`` comes from :func:`app.core.hosts.org_base_url` at the service boundary — the
    renderer is handed a string and never an ``Org``, so it stays sandboxed and cannot resolve
    a host of its own (Golden Rule 4).
    """
    return f"{base_url.rstrip('/')}/invoices/{invoice_id}"
