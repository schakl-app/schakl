"""Inline (``cid:``) body images across the four transports (epic #269).

An invoice mail wants its payment QR *in* the letter, not stapled to the back of it, and the
four providers disagree about how — a nested MIME part, a pair of fields on the attachment
object, a separate top-level array, and in Brevo's case not at all. Each of those is a shape
the JSON or the message tree either has or has not got, and every one of them fails the same
way when it is wrong: the mail is accepted, delivered, and drawn with a broken image box in
the middle of it. Nothing short of asserting on the actual structure catches that, so the SMTP
test walks the message tree rather than grepping its serialisation, and the HTTP tests capture
the payload at ``_post_json`` and compare whole dicts.

The one rule holding the four together — **the content id is the filename** — is asserted from
both ends: SMTP2GO has no id field at all, so its filename must be what the ``<img>`` names,
and SendGrid's freely-chosen ``content_id`` is made to agree with it.
"""

from __future__ import annotations

import base64
import logging

import pytest

from app.core.email import senders
from app.core.email.senders import (
    EmailAttachment,
    OutgoingEmail,
    Sender,
    supports_inline_images,
)
from app.core.email.templates import sanitize_email_html

_SENDER = Sender(from_email="facturen@bureau.nl", from_name="Bureau", reply_to=None)

#: Not a real QR — the bytes never leave this module and every transport treats them as opaque.
_QR = b"\x89PNG\r\n\x1a\nfake-qr-bytes"
_QR_B64 = base64.b64encode(_QR).decode()
_PDF = b"%PDF-1.7 fake"
_PDF_B64 = base64.b64encode(_PDF).decode()

_QR_PART = EmailAttachment(
    filename="invoice-qr.png", content=_QR, mimetype="image/png", inline=True
)
_PDF_PART = EmailAttachment(
    filename="factuur-2026-0001.pdf", content=_PDF, mimetype="application/pdf"
)


def _message(*, html: str | None = None, attachments: list[EmailAttachment]) -> OutgoingEmail:
    return OutgoingEmail(
        to="klant@voorbeeld.nl",
        subject="Factuur 2026-0001",
        text="Scan de code om te betalen.",
        html=html,
        attachments=attachments,
    )


_BODY = '<p>Scan om te betalen:</p><img src="cid:invoice-qr.png" alt="QR">'


# --------------------------------------------------------------------------- #
# SMTP — the message tree
# --------------------------------------------------------------------------- #
def test_smtp_relates_the_inline_image_to_the_html_part() -> None:
    """The nesting *is* the feature: related to the html part, mixed at the top."""
    mime = senders._mime(_SENDER, _message(html=_BODY, attachments=[_QR_PART, _PDF_PART]))

    # An ordinary attachment lifts the whole thing into multipart/mixed, body first.
    assert mime.get_content_type() == "multipart/mixed"
    top = list(mime.iter_parts())
    assert [p.get_content_type() for p in top] == ["multipart/alternative", "application/pdf"]

    # The plaintext alternative is untouched; the HTML half is now multipart/related.
    body, pdf = top
    alternatives = list(body.iter_parts())
    assert [p.get_content_type() for p in alternatives] == ["text/plain", "multipart/related"]
    plain, related = alternatives
    assert "Scan de code" in plain.get_content()

    # …and the image hangs off *that*, beside the html it is referenced from.
    html, image = list(related.iter_parts())
    assert html.get_content_type() == "text/html"
    assert 'src="cid:invoice-qr.png"' in html.get_content()
    assert image.get_content_type() == "image/png"
    assert image.get_payload(decode=True) == _QR

    # RFC 2392: the header carries the angle brackets, the URL in the body does not.
    assert image["Content-ID"] == "<invoice-qr.png>"
    assert image.get_content_disposition() == "inline"
    assert image.get_filename() == "invoice-qr.png"

    # The paperclip stayed a paperclip, at the top level where a mail client draws it as one.
    assert pdf.get_content_disposition() == "attachment"
    assert pdf.get_filename() == "factuur-2026-0001.pdf"
    assert pdf["Content-ID"] is None


def test_smtp_nesting_does_not_depend_on_attachment_order() -> None:
    """The PDF wraps the tree in multipart/mixed and the QR wraps the html in
    multipart/related, and the two wraps commute — the html part keeps its identity through
    both. Worth pinning: a caller composes its attachment list in whatever order suits it, and
    an order-sensitive builder would put the QR one level too high for half of them."""
    mime = senders._mime(_SENDER, _message(html=_BODY, attachments=[_PDF_PART, _QR_PART]))
    alternative, pdf = list(mime.iter_parts())
    _plain, related = list(alternative.iter_parts())
    assert related.get_content_type() == "multipart/related"
    assert [p.get_content_type() for p in related.iter_parts()] == ["text/html", "image/png"]
    assert pdf.get_content_type() == "application/pdf"


def test_smtp_without_html_drops_the_inline_image() -> None:
    """Nothing to be inline *in*. Attaching it anyway would produce exactly the stray
    paperclip the flag exists to avoid, so it is dropped — Brevo's judgement, one layer down."""
    mime = senders._mime(_SENDER, _message(attachments=[_QR_PART]))
    assert mime.get_content_type() == "text/plain"
    assert not list(mime.iter_parts())


def test_smtp_without_inline_images_is_unchanged() -> None:
    """The pre-#269 shape still holds: alternative body, attachment at the top."""
    mime = senders._mime(_SENDER, _message(html="<p>hoi</p>", attachments=[_PDF_PART]))
    alternative, pdf = list(mime.iter_parts())
    assert [p.get_content_type() for p in alternative.iter_parts()] == [
        "text/plain",
        "text/html",
    ]
    assert pdf.get_content_disposition() == "attachment"


# --------------------------------------------------------------------------- #
# The HTTP transports — the posted JSON
# --------------------------------------------------------------------------- #
@pytest.fixture
def posted(monkeypatch) -> list[dict]:
    """Capture what a sender would have posted, at the one seam all three share."""
    captured: list[dict] = []

    async def _fake_post(url: str, *, headers, payload, ok_statuses, error_path):  # noqa: ANN001
        captured.append(payload)
        return True, None

    monkeypatch.setattr(senders, "_post_json", _fake_post)
    return captured


async def test_sendgrid_marks_the_inline_image_in_the_same_array(posted) -> None:
    ok, error = await senders._send_sendgrid(
        {"api_key": "SG.x"}, _SENDER, _message(html=_BODY, attachments=[_QR_PART, _PDF_PART])
    )
    assert (ok, error) == (True, None)
    qr, pdf = posted[0]["attachments"]
    # Both fields, exactly as documented: "The content_id is used when the disposition is set
    # to inline and the attachment is an image, allowing the file to be displayed within the
    # body of the email." The id is the filename, so one <img> works on every transport.
    assert qr == {
        "content": _QR_B64,
        "filename": "invoice-qr.png",
        "type": "image/png",
        "disposition": "inline",
        "content_id": "invoice-qr.png",
    }
    assert qr["content_id"] == qr["filename"]
    # An ordinary attachment keeps exactly the shape it had before #269 — no content_id.
    assert pdf == {
        "content": _PDF_B64,
        "filename": "factuur-2026-0001.pdf",
        "type": "application/pdf",
        "disposition": "attachment",
    }


async def test_smtp2go_puts_the_inline_image_in_its_own_array(posted) -> None:
    await senders._send_smtp2go(
        {"api_key": "api-x"}, _SENDER, _message(html=_BODY, attachments=[_QR_PART, _PDF_PART])
    )
    payload = posted[0]
    # A separate top-level array — "An array of images to be inlined into the email. Use an
    # image in content as <img src="cid:filename"/>" — in the same entry shape as attachments.
    # There is no id field, which is *why* the filename is the cid everywhere else.
    assert payload["inlines"] == [
        {"filename": "invoice-qr.png", "fileblob": _QR_B64, "mimetype": "image/png"}
    ]
    assert payload["attachments"] == [
        {
            "filename": "factuur-2026-0001.pdf",
            "fileblob": _PDF_B64,
            "mimetype": "application/pdf",
        }
    ]


async def test_smtp2go_omits_the_empty_array(posted) -> None:
    """An inline-only mail sends no `attachments` key at all, and vice versa: an empty array
    is not the same message, and the transport should never be asked to interpret one."""
    await senders._send_smtp2go(
        {"api_key": "api-x"}, _SENDER, _message(html=_BODY, attachments=[_QR_PART])
    )
    assert "attachments" not in posted[0] and len(posted[0]["inlines"]) == 1
    await senders._send_smtp2go(
        {"api_key": "api-x"}, _SENDER, _message(html=_BODY, attachments=[_PDF_PART])
    )
    assert "inlines" not in posted[1] and len(posted[1]["attachments"]) == 1


async def test_brevo_drops_the_inline_image_rather_than_paperclipping_it(posted) -> None:
    """Brevo documents no Content-ID mechanism, so the QR cannot render wherever it is put.
    Sending it as an ordinary attachment would leave a bare image at the bottom of an invoice
    mail beside a broken-image box — worse than the plain link the composer draws instead."""
    await senders._send_brevo(
        {"api_key": "xkeysib-x"}, _SENDER, _message(html=_BODY, attachments=[_QR_PART, _PDF_PART])
    )
    assert posted[0]["attachment"] == [{"name": "factuur-2026-0001.pdf", "content": _PDF_B64}]


async def test_brevo_sends_no_attachment_key_for_an_inline_only_mail(posted) -> None:
    await senders._send_brevo(
        {"api_key": "xkeysib-x"}, _SENDER, _message(html=_BODY, attachments=[_QR_PART])
    )
    assert "attachment" not in posted[0]
    assert posted[0]["htmlContent"] == _BODY  # the body itself is never rewritten


async def test_brevo_says_so_once_not_once_per_mail(posted, caplog) -> None:
    """A nightly invoice run over a Brevo org must not write the same line a thousand times:
    what is being reported is a property of the transport, not of any one message."""
    senders._INLINE_DROP_LOGGED.discard("brevo")
    with caplog.at_level(logging.INFO, logger="schakl.email"):
        for _ in range(3):
            await senders._send_brevo(
                {"api_key": "xkeysib-x"}, _SENDER, _message(html=_BODY, attachments=[_QR_PART])
            )
    lines = [r for r in caplog.records if r.name == "schakl.email"]
    assert len(lines) == 1 and "inline" in lines[0].getMessage()


# --------------------------------------------------------------------------- #
# Asking before composing
# --------------------------------------------------------------------------- #
def test_supports_inline_images_answers_per_transport() -> None:
    assert supports_inline_images("smtp") is True
    assert supports_inline_images("sendgrid") is True
    assert supports_inline_images("smtp2go") is True
    assert supports_inline_images("brevo") is False
    # Fails closed on anything it does not know — including "instance", which is a settings
    # choice (#199) rather than a transport and which send_email rejects too. A composer
    # holding that name resolves it to the real provider first.
    assert supports_inline_images("instance") is False
    assert supports_inline_images("") is False
    assert supports_inline_images("mailgun") is False


# --------------------------------------------------------------------------- #
# The tenant-authored body
# --------------------------------------------------------------------------- #
def test_sanitiser_keeps_a_cid_image_and_still_strips_javascript() -> None:
    """A tenant may put the QR where they want it in their own invoice template, so `cid:`
    survives the allow-list — it addresses a part of this very message, not the network.
    Everything else it refused before, it still refuses."""
    html = sanitize_email_html(
        '<p><img src="cid:invoice-qr.png" alt="QR" width="120"></p>'
        '<p><a href="javascript:alert(1)">klik</a></p>'
        '<p><img src="javascript:alert(2)"><img src="data:image/png;base64,AAAA"></p>'
    )
    assert 'src="cid:invoice-qr.png"' in html
    assert 'alt="QR"' in html and 'width="120"' in html
    assert "javascript" not in html
    assert "data:image" not in html
