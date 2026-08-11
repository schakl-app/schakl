"""What an Uptime Kuma call can refuse with, as distinct types (docs/UPTIME.md §14).

One class per *instruction to the admin*, not one per HTTP-ish condition. The distinctions here
were chosen from what a live 2.5.0 actually answers, and two of them are the whole reason this
file is not a single exception with a string:

* :class:`ReauthRequired` and :class:`CredentialsRejected` are the same shape and opposite
  instructions — "the credential you typed months ago has been revoked at the far end" versus
  "the credential you just typed is wrong". Kuma distinguishes them for us (``authInvalidToken``),
  so collapsing them would discard information the server volunteered.
* :class:`RateLimited` is neither. Kuma's login limiter is twenty per minute on **one bucket
  shared by every caller of that instance**, so it refuses a *correct* password once the budget
  is spent. Reading that as "wrong password" sends an admin to rotate a working credential.
"""

from __future__ import annotations


class UptimeKumaError(RuntimeError):
    """An Uptime Kuma call failed. ``args[0]`` is Kuma's own text or i18n key, never a credential.

    ``i18n`` records whether the message is one of Kuma's own translation keys. 2.x answers
    ``{"msg": "successAdded", "msgi18n": True}`` where 1.x answered English prose — with the
    rate limiter as the one exception that still sends a bare English sentence. Nothing may
    branch on the prose; this flag is what lets a caller know which kind it is holding.
    """

    def __init__(self, message: str, *, i18n: bool = False) -> None:
        super().__init__(message)
        self.i18n = i18n


class Unreachable(UptimeKumaError):
    """The socket would not open: DNS, the tunnel, TLS, or Kuma is down.

    Never a statement about the credential — nothing was authenticated for it to be wrong about.
    """


class NotUptimeKuma(UptimeKumaError):
    """The target answered, but never sent an ``info`` event (docs/UPTIME.md §5, gate 3).

    A half-installed Uptime Kuma 2.x serves its SPA's HTML with **HTTP 200 on every path**,
    ``/socket.io/`` included, until its database wizard is answered. So "something answered" is
    not proof of anything, and this is the gate that stops an SSRF-shaped ``base_url`` from
    being stored as a working instance.
    """


class GatewayRefused(UptimeKumaError):
    """Something in front of Kuma refused the handshake — Access, a proxy, a WAF.

    Its own class because it is indistinguishable from :class:`Unreachable` at the socket layer
    and needs the opposite fix: a service token to correct, not a host to bring back up.
    """


class CredentialsRejected(UptimeKumaError):
    """Enrolment failed: wrong username or password. Reachable at enrolment only."""


class TotpRequired(UptimeKumaError):
    """The account has 2FA and no code was supplied — Kuma answers ``{tokenRequired: true}``."""


class TotpRejected(UptimeKumaError):
    """The code was wrong. Usually clock skew, and never a statement about the password."""


class ReauthRequired(UptimeKumaError):
    """The stored token no longer verifies: ``authInvalidToken``.

    The Kuma password changed or the user was deactivated. A retry cannot fix it and the
    credential was never wrong — an admin must re-enrol. This is a *state* the instance sits in
    (``needs_reauth``), not an error the mirror should be hidden behind.
    """


class RateLimited(UptimeKumaError):
    """Kuma's login limiter refused the attempt — twenty per minute, instance-wide.

    Retry later. Rotating the credential makes it worse, because every attempt spends from the
    same shared bucket that the instance's own owner logs in through.
    """


class VersionUnsupported(UptimeKumaError):
    """The instance is below the floor this module can speak to."""

    def __init__(self, message: str, *, version: str | None = None) -> None:
        super().__init__(message)
        #: What the instance reported, so the message can name it rather than say "too old".
        self.version = version


class MonitorTypeUnsupported(UptimeKumaError):
    """This instance's version has no such monitor type.

    Eleven of 2.5.0's thirty-three types did not exist in 1.x, so this is an ordinary answer for
    a tenant running an older instance and not a bug.
    """
