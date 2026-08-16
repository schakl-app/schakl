"""The error envelope's own invariants (``app/errors.py``, CLAUDE.md §9).

These exist because of a failure whose shape will recur. ``AppError`` gained a ``details``
field, and the handler reads it unconditionally — while two subclasses deliberately build
themselves **field by field** instead of calling the base initialiser, because
``super().__init__(message_key)`` would overwrite ``str(exc)``, which both need to stay the
provider's own sentence for their account row's ``last_error``.

``GtmError`` was written in a parallel worktree off a base that predated ``details`` and rebased
clean on top of the one that had it. Git had nothing to say; the attribute simply was not there,
and *every* refusal from that integration answered 500 out of the exception handler — the one
place in the app that is supposed to be incapable of failing. It was invisible to review (the
class looks complete), invisible to the type checker (an attribute set in one branch of the class
hierarchy), and caught only by three tests that happened to drive a real refusal.

So the guarantee is stated twice: the base class carries a default for everything the handler
reads, and this sweep fails the moment a subclass can no longer answer one.
"""

from __future__ import annotations

import inspect

import app.main  # noqa: F401  — importing the app mounts every enabled module, so the sweep sees them
from app.errors import AppError
from app.integrations.google_tag_manager.errors import (
    GtmApiDisabledError,
    GtmPermissionError,
    classify,
)

#: Exactly what ``_app_error_handler`` reads off the exception. Adding to the envelope means
#: adding here, which is what makes the next ``details`` inert instead of fatal.
ENVELOPE_ATTRS = ("code", "message_key", "status_code", "fields", "details")


def _subclasses(cls: type) -> list[type]:
    out = []
    for sub in cls.__subclasses__():
        out.append(sub)
        out.extend(_subclasses(sub))
    return out


def test_every_app_error_answers_the_whole_envelope() -> None:
    """A subclass that cannot answer one of these turns its own refusal into a 500.

    Checked at the **class** level on purpose: an instance attribute set by an ``__init__`` this
    sweep would have to guess the signature of proves nothing about the subclass that hand-rolls
    one, which is the only kind that has ever got this wrong.
    """
    missing: list[str] = []
    for cls in [AppError, *_subclasses(AppError)]:
        for attr in ENVELOPE_ATTRS:
            if not hasattr(cls, attr):
                missing.append(f"{cls.__module__}.{cls.__qualname__}.{attr}")
    assert not missing, "AppError subclasses that cannot fill the envelope: " + ", ".join(missing)


def test_a_hand_rolled_app_error_sets_every_attribute_it_declares() -> None:
    """The class default is a floor, not a licence to stop setting them.

    A subclass building itself field by field has opted out of the initialiser that would have
    done it; inheriting a *generic* ``code`` silently is how a refusal comes to answer
    ``errors.server`` while looking like it was classified.
    """
    for cls in _subclasses(AppError):
        source = inspect.getsource(cls) if cls.__dict__.get("__init__") else ""
        if "Exception.__init__(self" not in source:
            continue
        for attr in ENVELOPE_ATTRS:
            assert f"self.{attr}" in source, (
                f"{cls.__qualname__} bypasses AppError.__init__ but never sets self.{attr}"
            )


def test_gtm_carries_google_s_reason_and_not_its_prose() -> None:
    """``details`` is identifiers; the message a person reads is an i18n key (§9).

    The reason is the whole diagnosis for a 403 — the API switched off in the Cloud project and
    the client's GTM admin never granting access are the same status code with different people
    who can fix them.
    """
    disabled = classify(
        {
            "error": {
                "message": "Tag Manager API has not been used in project 12345 before",
                "details": [{"reason": "SERVICE_DISABLED"}],
            }
        },
        status=403,
        fallback="",
    )
    assert isinstance(disabled, GtmApiDisabledError)
    assert disabled.details == {"google_reason": "SERVICE_DISABLED"}
    assert disabled.message_key == "errors.gtm_api_disabled"
    assert "12345" not in str(disabled.details)

    # No reason at all is the ordinary case, and an empty ``details`` must stay absent rather
    # than become an empty object the client has to special-case.
    plain = classify({"error": {"message": "nope"}}, status=403, fallback="")
    assert isinstance(plain, GtmPermissionError)
    assert plain.details is None
