"""Permissions the reporting module introduces (issue #300, CLAUDE.md §15).

Six capabilities, and the two splits that are not obvious are the ones that matter.

**Reading is scoped, writing and sending are not.** ``reporting.report.read`` carries
``("own", "any")`` because one key has to serve two very different readers: a client portal
login reading *their own published* reports, and staff reading every draft. #266 is the lesson
being applied rather than repeated — there, ``invoicing.invoice.read`` turned out to gate seven
endpoints of which only three were documents, and granting it to ``client`` handed over the
seller's bank details and every employee's hourly rate. So before granting this to ``client``:
the scope narrows the *surface*, the company horizon narrows *whose*, and
``Report.__portal_horizon_clause__`` narrows *which* — audience and publication, which no scope
could express.

**Sending is not writing.** Drafting a report and putting it in a client's inbox under the
agency's brand are different acts with different blast radii, so they are different keys. An
agency can let a junior write the month's reports and keep the send button for whoever owns the
relationship — which is the same reasoning invoicing already applies to issuing a document.
"""

from __future__ import annotations

from app.core.permissions import PermissionSpec

REPORTING_PERMISSIONS: list[PermissionSpec] = [
    # `client` gets the narrow half so a portal login reads its own published reports; the
    # model's portal clause is what keeps that to *published, client-facing* ones.
    PermissionSpec(
        "reporting.report.read",
        scopes=("own", "any"),
        position=10,
        default_roles=("admin", "member"),
        default_own_roles=("client",),
    ),
    # Generating, regenerating a paragraph, editing the prose.
    PermissionSpec("reporting.report.write", position=20, default_roles=("admin", "member")),
    # Publishing to the portal and mailing it. Deliberately not implied by write.
    PermissionSpec("reporting.report.send", position=30),
    # The internal analysis: risks, gaps, "mogelijk buiten scope". Never `client`, and never
    # implied by reading the client document — they are different documents about the same
    # month, and only one of them is written to be shown to the customer.
    PermissionSpec(
        "reporting.internal.read", position=40, default_roles=("admin", "member")
    ),
    # A client's editorial profile — their goals, focus and recipients. Account-manager work,
    # so members hold it: it is edited on the client's own page, beside everything else about
    # them.
    PermissionSpec(
        "reporting.profile.manage", position=50, default_roles=("admin", "member")
    ),
    # Tones, templates, the org-wide schedule defaults. Configuration, so admin only — the
    # house voice is not something each account manager rewrites for their own clients.
    PermissionSpec("reporting.settings.manage", position=60),
]
