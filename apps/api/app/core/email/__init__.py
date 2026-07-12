"""Org-level outbound e-mail (#17): DB-stored, UI-configured transports — never env vars.

One :class:`~app.core.email.models.EmailSettings` row per org selects a provider — the official
HTTP APIs of Brevo / SendGrid / SMTP2GO, or a plain SMTP relay — with its secrets encrypted at
rest (:mod:`app.core.crypto`). Consumers (notification e-mail, later auth mail and PDFs) call
:func:`app.core.email.service.send_org_email` and never touch provider specifics.
"""
