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
   * Create a monitor for the website or domain whose page this is, and push it to Uptime Kuma
   * (#366).
   *
   * The anchor is the **route's** record, exactly as `uptimeLink`'s is: the form above this
   * button describes the monitor, never which record it belongs to. `company_id` is deliberately
   * not posted at all — the API derives it from the anchor (`_resolve_anchor`), because two
   * copies of "whose monitor is this" is how the horizon starts disagreeing with the record.
   *
   * Every settings field is *absent* when the box was left empty rather than sent as `0` or an
   * empty string: `null` means **inherit** all the way down (`profiles.resolve`), and a blank
   * interval posted as a number would pin the monitor to the invariant floor — the kind of
   * plausible wrong value nobody notices until a client asks why their site is checked every
   * twenty seconds.
   */
  uptimeCreateMonitor: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const raw = String(form.get("entity_type") ?? "");
    const entity_type = (ANCHORS as readonly string[]).includes(raw) ? (raw as Anchor) : null;
    const name = String(form.get("name") ?? "").trim();
    const instance_id = String(form.get("instance_id") ?? "").trim();
    if (!entity_type || !name || !instance_id) {
      return fail(400, { uptimeError: "errors.required" });
    }

    /** An empty box is not a zero — absent means inherit, so it is left out of the body. */
    const optionalNumber = (field: string): number | undefined => {
      const value = String(form.get(field) ?? "").trim();
      if (value === "") return undefined;
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : undefined;
    };
    const optionalId = (field: string): string | undefined =>
      String(form.get(field) ?? "").trim() || undefined;

    const anchor = event.params.id as string;
    const { error } = await apiFor(event).POST("/api/v1/uptime/monitors", {
      body: {
        instance_id,
        name,
        monitor_type: String(form.get("monitor_type") ?? "http"),
        target: String(form.get("target") ?? "").trim() || null,
        port: optionalNumber("port"),
        interval_seconds: optionalNumber("interval_seconds"),
        retries: optionalNumber("retries"),
        parent_id: optionalId("parent_id"),
        profile_id: optionalId("profile_id"),
        // The route's own record, and only ever one of the two.
        website_id: entity_type === "website" ? anchor : undefined,
        domain_id: entity_type === "domain" ? anchor : undefined,
        active: true,
      },
    });
    if (error) return fail(400, { uptimeError: apiErrorKey(error).key });
    return { uptimeCreated: true };
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
