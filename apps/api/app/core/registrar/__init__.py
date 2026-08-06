"""Registrar seam (issue #296) — what schakl may ask *any* registrar, and the dispatch for it.

Core owns the vocabulary; a module (``oxxa``, and whatever comes after it) owns the protocol
talking to one. The precedent is :mod:`app.core.storage.backend` — protocol + registry +
config-free dispatch — with the shape borrowed from ``marketing/sources/base.py``, which is the
closer relative: async, credential-bearing, and already carrying an ``external_id`` mapping.
"""

from __future__ import annotations

from app.core.registrar.backend import (
    RegistrarAuthError,
    RegistrarContact,
    RegistrarDomain,
    RegistrarError,
    RegistrarProvider,
    get_registrar,
    known_registrars,
    register_registrar,
    split_suffix,
)
from app.core.registrar.expiry import (
    RegisterExpiry,
    register_expiries,
    register_expiry,
    register_expiry_expression,
)
from app.core.registrar.presence import (
    RegisterPresence,
    register_presence,
    register_presences,
)

__all__ = [
    "RegisterExpiry",
    "RegisterPresence",
    "RegistrarAuthError",
    "RegistrarContact",
    "RegistrarDomain",
    "RegistrarError",
    "RegistrarProvider",
    "get_registrar",
    "known_registrars",
    "register_expiries",
    "register_expiry",
    "register_expiry_expression",
    "register_presence",
    "register_presences",
    "register_registrar",
    "split_suffix",
]
