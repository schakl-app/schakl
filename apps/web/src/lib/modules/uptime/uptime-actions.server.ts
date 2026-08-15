/**
 * The form actions behind the uptime panel (docs/UPTIME.md §9). The website and domain detail
 * pages spread these into their `actions` — the same contract the WordPress and Cloudflare
 * panels use: a panel edits through its host page, because SvelteKit actions live on the page.
 *
 * Keeping them here rather than inline in the two routes is what stops `websites` and `domains`
 * from growing uptime internals (CLAUDE.md §6): each host imports one symbol and knows nothing
 * about monitors, anchors or link candidates.
 *
 * Every action is named `uptime*` so it cannot collide with a host page's own.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

/** What a link posted from a panel may attach to — the host route's own entity, never free text. */
const ANCHORS = ["website", "domain"] as const;
type Anchor = (typeof ANCHORS)[number];

export const uptimeActions = {
  /**
   * Attach one monitor to the website or domain whose page this is.
   *
   * The anchor id comes from the **route**, never from the form: the panel is rendered on one
   * record and posting an `entity_id` alongside it would be a second opinion about which one —
   * the shape `cloudflare`'s adopt button paid for once, where the obvious press posted whatever
   * was typed above it rather than the row it was drawn from.
   */
  uptimeLink: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const monitor_id = String(form.get("monitor_id") ?? "").trim();
    const raw = String(form.get("entity_type") ?? "");
    const entity_type = (ANCHORS as readonly string[]).includes(raw) ? (raw as Anchor) : null;
    if (!monitor_id || !entity_type) return fail(400, { uptimeError: "errors.required" });

    const { error } = await apiFor(event).POST("/api/v1/uptime/monitors/{monitor_id}/link", {
      params: { path: { monitor_id } },
      body: { entity_type, entity_id: event.params.id as string },
    });
    if (error) return fail(400, { uptimeError: apiErrorKey(error).key });
    return { uptimeLinked: true };
  },

  /**
   * Detach one monitor from whatever it is attached to.
   *
   * An explicit `null` on both fields, which is the API's detach (§18's rule): absent would mean
   * "leave alone". Nothing is written to Uptime Kuma and nothing is deleted — the monitor keeps
   * running and keeps being mirrored, it just stops claiming to be this record's.
   */
  uptimeUnlink: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const monitor_id = String(form.get("monitor_id") ?? "").trim();
    if (!monitor_id) return fail(400, { uptimeError: "errors.required" });

    const { error } = await apiFor(event).POST("/api/v1/uptime/monitors/{monitor_id}/link", {
      params: { path: { monitor_id } },
      body: { entity_type: null, entity_id: null },
    });
    if (error) return fail(400, { uptimeError: apiErrorKey(error).key });
    return { uptimeUnlinked: true };
  },
};
