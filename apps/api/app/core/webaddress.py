"""What a website is called, and whose it is — stated once, read by every module that names one.

A website has no name of its own: it is *the site at an address*, and the address is composed
from three facts on two tables — the parent domain's name, whether the site answers on the apex
or on ``www.``, and the **path** under that host. The path is what lets an agency record the
dev installs it runs under one domain of its own (``breik.dev/briellaerd``, ``breik.dev/nova``):
before it existed, everything after the host was stripped by the domain normaliser and a second
site on the same domain had nowhere to live.

Whose it is has the same shape: the parent domain's client by default, and a client the website
names for itself where the two differ (``websites.company_override_id``, ``NULL`` = *follow the
domain*). A dev site on the agency's own domain is the **client's** site, and every screen that
groups websites by client — the hub, the pickers, the horizon — has to agree on that.

Five modules compose one or both of these (websites, uptime, wordpress, marketing,
subscriptions), and §6 forbids them importing each other's internals, so the rule lives here in
core in two forms: pure Python for a row already in hand, and SQL fragments for the bare-table
statements the borrowers write. Writing ``CASE WHEN w.root THEN d.name ELSE 'www.' || d.name END``
in five files is how the sixth forgets the path.
"""

from __future__ import annotations

import re
from typing import Any

from sqlalchemy import case, func, literal
from sqlalchemy.sql import ColumnElement

__all__ = [
    "normalize_path",
    "split_address",
    "website_company_expr",
    "website_company_sql",
    "website_host",
    "website_label",
    "website_label_expr",
    "website_label_sql",
    "website_url",
]

_MAX_PATH = 500
_ILLEGAL = re.compile(r"[\s?#]")


def normalize_path(value: str | None) -> str:
    """The stored form of a path: ``""`` for the root, else ``/segment[/segment…]``.

    Tolerant of how people type a path — ``briellaerd``, ``/briellaerd/``, ``//briellaerd`` all
    mean the same install — and strict about what is not a path at all: a scheme, a query, a
    fragment or whitespace is refused with an i18n key, because ``breik.dev/x?preview=1`` is a
    page somebody visited, not a site the agency runs. Case is kept: a path is case-sensitive on
    every Linux host, and lowercasing what somebody typed would make ``/Briellaerd`` and
    ``/briellaerd`` the same record while the server treats them as two.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    if "://" in raw or _ILLEGAL.search(raw):
        raise ValueError("errors.invalid_website_path")
    segments = [part for part in raw.split("/") if part]
    if not segments:
        return ""
    path = "/" + "/".join(segments)
    if len(path) > _MAX_PATH:
        raise ValueError("errors.invalid_website_path")
    return path


def split_address(value: str) -> tuple[str, bool, str]:
    """``(apex, root, path)`` from an address as a person types it.

    ``https://www.breik.dev/briellaerd/`` → ``("breik.dev", False, "/briellaerd")``. The
    host half follows the domain normaliser's reading (scheme, credentials and port dropped,
    ``www.`` folded into ``root``), the path half goes through :func:`normalize_path`. It is
    the inverse of :func:`website_label`, which is what lets an import match a row on the
    label an export wrote.
    """
    raw = value.strip()
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    host, _, path = raw.partition("/")
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    host = host.split(":", 1)[0].strip().lower().strip(".")
    root = True
    if host == "www":
        host = ""
    elif host.startswith("www."):
        host = host[4:]
        root = False
    return host, root, normalize_path(path)


def website_host(domain_name: str, root: bool) -> str:
    """The hostname the site answers on: the apex, or ``www.`` + the apex."""
    name = (domain_name or "").strip()
    return name if root or not name else f"www.{name}"


def website_label(domain_name: str, root: bool, path: str | None) -> str:
    """What every screen prints for a website: the host plus the path, no scheme.

    ``breik.dev/briellaerd``, ``www.klant.nl``, ``klant.nl`` — the shortest string that names
    exactly one site, and the one an export carries as the row's identity.
    """
    return website_host(domain_name, root) + (path or "")


def website_url(domain_name: str, root: bool, path: str | None) -> str:
    """The address to open: ``https://`` + the label. A site the agency records is a site that
    serves TLS in 2026; a monitor's own target field stays the place to say otherwise."""
    label = website_label(domain_name, root, path)
    return f"https://{label}" if label else ""


# --- SQL twins, for the bare-table statements borrowing modules write (§6) -------------- #


def website_label_expr(
    root: ColumnElement[Any], path: ColumnElement[Any], domain_name: ColumnElement[Any]
) -> ColumnElement[Any]:
    """:func:`website_label` as a SQLAlchemy expression over the two bare tables' columns."""
    host = case((root.is_(True), domain_name), else_=literal("www.") + domain_name)
    return host + func.coalesce(path, literal(""))


def website_company_expr(
    company_override_id: ColumnElement[Any], domain_company_id: ColumnElement[Any]
) -> ColumnElement[Any]:
    """Whose the site is: its own client where it names one, else the domain's."""
    return func.coalesce(company_override_id, domain_company_id)


def website_label_sql(w: str = "w", d: str = "d") -> str:
    """:func:`website_label` for a hand-written ``text()`` statement, over the aliases the
    caller joined ``websites`` (``w``) and ``domains`` (``d``) under."""
    return (
        f"(CASE WHEN {w}.root THEN {d}.name ELSE 'www.' || {d}.name END"
        f" || COALESCE({w}.path, ''))"
    )


def website_company_sql(w: str = "w", d: str = "d") -> str:
    """:func:`website_company_expr` for a hand-written ``text()`` statement."""
    return f"COALESCE({w}.company_override_id, {d}.company_id)"
