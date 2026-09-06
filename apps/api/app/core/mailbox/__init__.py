"""The provider-agnostic half of "log the mail that concerns a client" (CLAUDE.md §6, §10).

Two integrations feed the contactmomenten timeline from a connected mailbox — ``google``
(Gmail) and ``microsoft`` (Outlook) — and they may not import each other's internals. Everything
about *whose* mail it is, *whether* it concerns a client and *where* it files is a rule about the
agency rather than about a vendor's API, so it lives here, named once and read by both feeds:

- :mod:`~app.core.mailbox.matching` — the pure rules: participants off the headers, the intended
  owner, colleague-to-colleague chatter, the contact match and its ranking (#305, #324, #274).
- :mod:`~app.core.mailbox.gates` — the vocabulary of *why* a message was declined
  (:class:`~app.core.mailbox.gates.SkipReason`) and the ``Decision`` a dry run answers with (#372).
- :mod:`~app.core.mailbox.internals` — who counts as *us*, composed across every connected
  mailbox provider through a registry, so a copy held by a Gmail mailbox defers to the Outlook
  mailbox of the colleague it was addressed to, and the reverse.
- :mod:`~app.core.mailbox.policy` — the two org-level policy enums both settings screens share.

What stays in each integration is the vendor: the wire shape of a message, its ids, its labels or
categories, its body encoding, and the round trips.
"""
