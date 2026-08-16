"""Integrations: the modules that are a conversation with somebody else's service.

The split from ``app.modules`` is the one in CLAUDE.md §6a. A **module** is a domain capability
schakl itself provides — it owns entities, screens and a data model, and it would still be worth
having with every third-party account cancelled. An **integration** holds a *credential* for an
external account, and what it stores is a **mirror of**, or a **pointer into**, state that lives
somewhere else. Cancel the vendor and a module is poorer; an integration is gone.

Everything else about them is identical: an integration is a ``ModuleDescriptor`` like any other,
registers the same way, declares the same permissions, is enabled per tenant in the same list, and
may be licensed by the same sku machinery. Only two fields differ — ``kind`` says which it is, and
``requires`` names the modules it has nowhere to put its data without.
"""
