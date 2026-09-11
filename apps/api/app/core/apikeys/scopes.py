"""What a key's ``scopes`` list may say, and what it means at the moment a request arrives.

Two vocabularies live in that one JSONB column, and only one of them existed before this file.

**A permission string** (``companies.company.read``, ``time.entry.write:own``) is a catalog key,
stored suffixed where the permission is scoped (§15), and is exactly the capability it names —
a key minted by hand on Instellingen → API en MCP holds a list of these and nothing else.

**A coarse scope** (``mcp:full``, ``mcp:read``) is a *rule*, not a list: "everything the owner
holds", or "every read the owner holds", resolved against the catalog **at request time**. It is
what an OAuth consent stores when the person did not narrow the offer — and it exists because a
list is frozen the moment it is written. Search Console shipped thirteen releases after the
first connector was consented; a key holding the 220 permission strings its owner had in August
could not name a module that did not exist yet, so the connector answered 403 on every Search
Console tool and nothing on any screen said why. The rule survives the catalog growing, which is
the whole point: a person who agreed to "everything I may do" meant the modules their agency
switches on next quarter too, and re-consenting is not something a chat client ever prompts for.

Coarse scopes are **only ever expanded against a live holder**, which is why a service-account
key — a synthetic principal with nobody to expand against — cannot carry one
(``ApiKeyService._validate_scopes`` refuses anything outside the catalog). The cap that makes a
personal key safe (owner's live permissions, re-read on every request) is the same cap that
makes a coarse scope safe: it can never resolve to more than the person holds *now*.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.core.permissions.catalog import all_permissions
from app.core.permissions.permset import PermissionSet

#: Coarse scopes a client may ask for by name — and a consent may store by name. A connector
#: that has never met this instance cannot know that ``time.entry.write:own`` exists, and a
#: request listing 300 permission strings is not one a person can read on a consent screen, so
#: the request is allowed to be coarse and the consent screen is where it becomes exact — or
#: stays coarse, which is the honest record of "everything, now and later".
SCOPE_MCP_READ = "mcp:read"
SCOPE_MCP_FULL = "mcp:full"
COARSE_SCOPES = (SCOPE_MCP_READ, SCOPE_MCP_FULL)


def _catalog() -> dict[str, bool]:
    """Permission key → whether it is scoped (``:own``/``:any``)."""
    return {spec.key: bool(spec.scopes) for spec in all_permissions()}


def is_read(key: str) -> bool:
    return key.rsplit(".", 1)[-1] == "read"


def is_coarse(scope: str) -> bool:
    return scope in COARSE_SCOPES


def split_scope(scope: str) -> tuple[str, str | None]:
    """``"time.entry.read:own"`` → ``("time.entry.read", "own")``; a bare key → ``(key, None)``."""
    base, sep, suffix = scope.partition(":")
    return base, (suffix if sep else None)


def expand_scopes(requested: Sequence[str], holder: PermissionSet) -> list[str]:
    """What the client asked for, resolved against the catalog and capped by the *holder*.

    The cap is the whole safety property and it is applied twice on purpose: here, so a consent
    screen never offers a person the ability to hand out something they do not have, and again
    on every request the key later makes (:func:`effective_scopes`), so a permission removed
    tomorrow is removed from the connector tomorrow.

    An unknown scope is *dropped* rather than fatal: a client sends the scopes it was built to
    send, an instance runs the modules it runs, and refusing the whole authorization because a
    connector asked for something this instance does not have would be a dead "Add connector"
    button with no way for anyone to see why.
    """
    scoped = _catalog()
    wanted = set(requested)
    want_all = SCOPE_MCP_FULL in wanted or not wanted
    want_read = SCOPE_MCP_READ in wanted

    resolved: list[str] = []
    for key, is_scoped in scoped.items():
        coarse = want_all or (want_read and is_read(key))
        explicit = key in wanted or any(w.split(":")[0] == key for w in wanted if ":" in w)
        if not (coarse or explicit):
            continue
        # A scoped permission is only ever stored suffixed (§15), and the broadest suffix the
        # holder actually has is the honest answer: handing out `:any` to someone holding `:own`
        # would be a silent escalation, and handing out `:own` to a holder of `:any` would
        # quietly break a screen they can open.
        if is_scoped:
            suffix = next((s for s in ("any", "own") if holder.has(key, s)), None)
            if suffix is None:
                continue
            resolved.append(f"{key}:{suffix}")
        elif holder.has(key):
            resolved.append(key)
    return sorted(resolved)


def effective_scopes(stored: Sequence[str], holder: PermissionSet) -> list[str]:
    """What a personal key may exercise *right now*: its stored list ∩ the owner's live grants.

    A coarse entry expands against today's catalog and today's holder; an explicit entry is kept
    only while the holder still has it. Both halves are the same cap read in two directions — a
    coarse scope may *widen* as the catalog grows and an explicit one never does, but neither can
    ever exceed the person the key acts as.
    """
    coarse = [s for s in stored if is_coarse(s)]
    explicit = [s for s in stored if not is_coarse(s)]
    effective: list[str] = []
    if coarse:
        # `expand_scopes` reads an empty request as "everything"; a stored coarse list is never
        # empty here, so the call means exactly the tokens it is handed.
        effective.extend(expand_scopes(coarse, holder))
    for scope in explicit:
        base, suffix = split_scope(scope)
        if holder.has(base, suffix) and scope not in effective:
            effective.append(scope)
    return effective


__all__ = [
    "COARSE_SCOPES",
    "SCOPE_MCP_FULL",
    "SCOPE_MCP_READ",
    "effective_scopes",
    "expand_scopes",
    "is_coarse",
    "is_read",
    "split_scope",
]
