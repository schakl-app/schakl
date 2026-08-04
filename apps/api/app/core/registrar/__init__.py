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

__all__ = [
    "RegistrarAuthError",
    "RegistrarContact",
    "RegistrarDomain",
    "RegistrarError",
    "RegistrarProvider",
    "get_registrar",
    "known_registrars",
    "register_registrar",
    "split_suffix",
]
