/**
 * uptime web module (CLAUDE.md §6, docs/UPTIME.md) — mirrors the API module.
 *
 * It contributes no nav item, for `cloudflare`'s reason: uptime is not a place you go, it is
 * something a *website* has. So the working surface is an `EntityPanelSpec` on the pages of the
 * things a monitor can be attached to — and the org-wide configuration, the instances and their
 * credentials, lives under Instellingen where docs/UX.md principle 6 puts it.
 *
 * **A domain gets the same panel as a website**, because `uptime_monitors.domain_id` has always
 * been a real link with a matcher behind it — it is what covers a client's mail server, VPN
 * endpoint and NAS, hosts inside a zone we hold that will never be websites (`matching`'s own
 * ladder). It had a write path and no read path anywhere in the product: confirming *"koppel aan
 * domein acme.nl"* stored the row correctly and then showed it on no screen at all, which reads
 * exactly like a button that does nothing.
 *
 * Each panel's `load` is deliberately Kuma-free: stored-state reads, no outside service, so these
 * pages render at the same speed whether the client's monitoring is up, down or unreachable
 * (docs/PERFORMANCE.md). Going and looking is the settings screen's explicit action.
 */
import type { ApiClient } from "$lib/core/api/client";
import { registerWebModule } from "$lib/core/registry";

import UptimeCompanyPanel from "./UptimeCompanyPanel.svelte";
import UptimePanel from "./UptimePanel.svelte";

/**
 * The panel's reads, shared by both anchors so the two can never answer differently.
 *
 * The picker's options are **streamed, not awaited** (`docs/PERFORMANCE.md`'s `createForm`
 * pattern, which this page already uses for its website modal). Attaching a monitor by hand is
 * something a person does once per record and never again, so blocking every website and domain
 * page on a list most visits never open would be paying for the rare case on every read. The
 * panel's own gate is a permission the loader cannot see — `EntityPanelContext` carries no user —
 * and widening a core contract for one module's optimisation is the worse trade of the two.
 */
function loadPanel(api: ApiClient, entityId: string, anchorType: "website" | "domain") {
  const query = anchorType === "website" ? { website_id: entityId } : { domain_id: entityId };
  const attached = api.GET("/api/v1/uptime/monitors", {
    // `count=false`: the panel draws rows, not a total, and a count it never renders is a second
    // query per page load. `meta=true` is the opposite trade and worth it here: this panel *does*
    // draw the group name, and it costs one query for the whole page rather than one per row.
    params: { query: { ...query, limit: 50, offset: 0, count: false, meta: true } },
  });
  const attachable = api
    .GET("/api/v1/uptime/monitors", {
      // Everything still attachable, which is a different set from any one matcher outcome:
      // asking for `unmatched` would offer a monitor nobody confirmed a proposal for and hide
      // the ones with no proposal at all — exactly the rows a person opens a picker to reach.
      params: {
        query: { link_status: "unlinked", limit: 100, offset: 0, count: false, meta: true },
      },
    })
    // A **group** is a monitor here (`monitor_type = "group"`, docs/UPTIME.md §7) and watches
    // nothing, so it can never be what a website is monitored by — attaching one would put a
    // folder in the panel. The matcher never proposes one either, because it has no target to
    // match on; this picker is the only other way in, so it draws the same line.
    .then((r) => (r.data?.items ?? []).filter((m) => m.monitor_type !== "group"))
    // A picker that cannot load its options must not take the page down with it: the monitors
    // already attached are the half of this panel that matters, and they are on screen.
    .catch(() => []);

  // What the *create* form needs (#366). Streamed for `attachable`'s reason and more strongly:
  // making a monitor is something a person does once per record, so three lookups on every
  // website and domain page load would be paying for the rare case on every read. One `Promise`
  // for all three, because the form draws them together and a partial one is not a form.
  const createForm = Promise.all([
    api.GET("/api/v1/uptime/instances/selectable"),
    // A group *is* a monitor (`monitor_type = "group"`, docs/UPTIME.md §7), so the group picker's
    // options are a filtered monitor list rather than a second endpoint. `meta=false`: the form
    // draws the group's own name and its instance is decided by the picker above it.
    api.GET("/api/v1/uptime/monitors", {
      params: {
        query: { monitor_type: "group", limit: 100, offset: 0, count: false, meta: false },
      },
    }),
    // Readable on `monitor.read`, which is what this panel already holds — the create form has to
    // *show* which defaults a monitor will follow, and that is why `list_profiles` is not gated
    // on `profile.manage` (#310).
    api.GET("/api/v1/uptime/profiles"),
  ])
    .then(([instances, groups, profiles]) => ({
      instances: instances.data ?? [],
      groups: groups.data?.items ?? [],
      profiles: profiles.data ?? [],
    }))
    // A form that cannot load its pickers must not take the page down with it: the monitors
    // already attached are the half of this panel that matters, and they are on screen.
    .catch(() => ({ instances: [], groups: [], profiles: [] }));

  return attached.then((r) => ({
    monitors: r.data?.items ?? [],
    attachable,
    createForm,
    anchorType,
  }));
}

registerWebModule({
  name: "uptime",
  // A conversation with somebody else's service (CLAUDE.md §6a).
  kind: "integration",
  companyPanels: [
    {
      // Matches the API `PanelSpec.key`. Without a component registered here the company page
      // falls through to its `<pre>{JSON.stringify(...)}</pre>` fallback — which is what it had
      // been doing, printing `{"total": 2, "by_status": {"active": 2}, "visible": true}` on
      // every client's page since the panel was contributed.
      key: "uptime.company",
      module: "uptime",
      component: UptimeCompanyPanel,
      position: 460,
      // Nothing here yet folds into the hub's one ＋ strip (#364).
      emptyHref: (id: string) => `/websites?company=${id}`,
    },
  ],
  entityPanels: [
    {
      key: "uptime.website",
      module: "uptime",
      entityType: "website",
      titleKey: "uptime.panel.title",
      position: 40,
      // The key the call actually makes, not the one the screen is about (#310). Without it the
      // panel is a 403 and an empty box.
      requiresPermission: "uptime.monitor.read",
      load: async (api, { entityId }) => loadPanel(api, entityId, "website"),
      component: UptimePanel,
    },
    {
      key: "uptime.domain",
      module: "uptime",
      entityType: "domain",
      titleKey: "uptime.panel.title",
      position: 40,
      requiresPermission: "uptime.monitor.read",
      load: async (api, { entityId }) => loadPanel(api, entityId, "domain"),
      component: UptimePanel,
    },
  ],
});
