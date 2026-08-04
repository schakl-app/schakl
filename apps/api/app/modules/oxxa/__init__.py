"""oxxa module (CLAUDE.md §6, issue #296) — the registrar half of epic #278.

Gives a domain record the four facts only a registrar knows — **when it expires**, **whether it
is locked**, **what the registry has delegated** (as opposed to what public DNS answers today)
and **who the registrant is** — and one write: moving that delegation.

That write is what finishes the Cloudflare story. Connecting a domain to Cloudflare produces a
nameserver pair and, until now, moving the domain onto it meant logging into the OXXA portal by
hand. `CloudflareZone.name_servers` has been storing exactly that payload since #278 shipped,
waiting for this.

**Written from OXXA's official API documentation (v1.2); not yet exercised against a live
account.** No sandbox credential exists — see ``docs/OXXA.md`` §1 for exactly what that means
for the parsing here and what to check first when a credential does arrive. CLAUDE.md §11's ban
is on writing an integration *from memory*; this one has the document, and says plainly where
the document is the only evidence.

Importing this package self-registers the module and its registrar provider.
"""

from __future__ import annotations

from app.core.registrar import register_registrar
from app.modules.oxxa.client import OxxaClient
from app.modules.oxxa.permissions import OXXA_PERMISSIONS
from app.modules.oxxa.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="oxxa",
    router=router,
    i18n_namespace="oxxa",
    # Licensed module (issue #137): a paid integration, the same bracket as ``cloudflare`` /
    # ``google`` / ``invoicing``, never part of the free CRM core (epic #140). Past
    # expiry+grace the mount-time gate turns every mutation 402; the read surface (the stored
    # register, expiry dates, the status report) keeps working, so an expired licence never
    # leaves an agency unable to see when a client's domain runs out.
    sku="oxxa",
    permissions=OXXA_PERMISSIONS,
)

registry.register(module)

# The registrar seam (#296 scope item 4). Registering the class rather than an instance: a
# provider is constructed per credential, and there is one credential per account row.
register_registrar(OxxaClient.key, OxxaClient)
