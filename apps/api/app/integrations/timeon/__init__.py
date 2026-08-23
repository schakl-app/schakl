"""``timeon`` integration (CLAUDE.md §6, §6a) — a two-way sync with Timeon's time registration.

Its predecessor was a **one-way importer** (``docs/TIMEON.md`` §2, the ``timeon-import`` branch),
and that document argued against building this: a sync buys a permanent two-writer problem to
cross a bridge once, and ``TimeEntry.invoiced_at`` is a downstream fact that no reconcile can
repair once an hour is on a client's invoice. The argument was right about invoices and wrong
about the bridge — a cutover that takes months is not a bridge, it is two systems both being
used, and during those months an importer either loses the corrections people make in schakl or
loses the hours they log in Timeon.

So the invoice argument becomes a *mechanism* rather than a veto: ``protect_invoiced`` refuses,
per entry, to let anything rewrite what has already been billed, and ``history_floor`` keeps the
whole imported past out of reach. Everything else the original document said stays true and is
implemented here — the natural key that recognises what the importer already wrote, the
per-employee ownership no REST call can express, the break field that is not a break.

**A conversation with somebody else's service** (§6a): it holds a credential and what it stores
is a *pointer into* state that lives over there. Switch Timeon off tomorrow and this module is
gone; ``time`` is merely poorer by one source. Hence ``requires=("time",)`` — an hour has
nowhere to go without it — and nothing else, deliberately: pairing projects is better with the
``projects`` module and is not impossible without it (an hour books onto a client), and
over-declaring makes a tenant switch on a module they did not want.

Written against the **live** API with a working key (§11). ``docs/TIMEON.md`` §3 records every
call that was made and the seven behaviours its OpenAPI document does not mention — including
that the document describes no response bodies at all, and no request body for the one endpoint
that writes.

Importing this package self-registers the integration.
"""

from __future__ import annotations

from arq import cron

from app.integrations.timeon.jobs import timeon_prune_runs, timeon_tick
from app.integrations.timeon.permissions import TIMEON_PERMISSIONS
from app.integrations.timeon.router import router
from app.registry import KIND_INTEGRATION, ModuleDescriptor, registry

module = ModuleDescriptor(
    name="timeon",
    kind=KIND_INTEGRATION,
    # `time` and nothing else — see the module docstring.
    requires=("time",),
    router=router,
    i18n_namespace="timeon",
    # Licensed, the same bracket as every other integration (epic #137). Past expiry+grace the
    # mount-time gate turns every mutation 402, so nothing new is synced — while the reads keep
    # working, which is what an agency needs in order to see what *was* synced and to find the
    # pairings before they disconnect.
    sku="timeon",
    permissions=TIMEON_PERMISSIONS,
    # **No company panel** (#411), and the loss is deliberate rather than overlooked: the
    # hub's card carried this client's pairing count and their open conflicts, and nothing
    # takes its place. Timeon is a cutover integration whose home is `/timeon` — the screen
    # somebody opens *because* a sync is running — and a card on every client's page for a
    # migration that ends is a card that outlives its reason. The conflicts queue is where
    # a decision is actually made; the hub only ever said one was waiting.
    cron_jobs=[
        # **The tick, not the schedule** (#388). Every quarter of an hour, clear of the
        # platform's other quarter-hourly jobs; each account then decides whether its own moment
        # has come, on the org's clock. One cron cannot express "hourly for this connection and
        # nightly for that one", and during a cutover — both systems written to all day — how
        # often the two are reconciled is an operational choice an agency makes, not a constant
        # we pick for them. The cost of a tick that decides nothing is two queries per org.
        cron(timeon_tick, minute={4, 19, 34, 49}),
        cron(timeon_prune_runs, hour=3, minute=50, weekday=0),
    ],
)

registry.register(module)
