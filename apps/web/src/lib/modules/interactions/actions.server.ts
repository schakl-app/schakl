/**
 * The form actions behind every contactmomenten panel. SvelteKit actions live on the page, so
 * each host detail page (company / project / contact / task) spreads these into its own
 * `actions` — the panel body posts to `?/createInteraction` etc. wherever it renders.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

const LINK_FIELDS = ["company_id", "project_id", "task_id", "contact_id"] as const;

/** "2026-07-10" + "14:30" → the tenant's wall clock, naive; the API attaches the org zone. */
function occurredAt(form: FormData): string | null {
  const date = String(form.get("occurred_date") ?? "").trim();
  if (!date) return null;
  const time = String(form.get("occurred_time") ?? "").trim() || "12:00";
  return `${date}T${time}:00`;
}

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}"));
  } catch {
    return {};
  }
}

function links(form: FormData): Record<string, string> {
  const out: Record<string, string> = {};
  for (const field of LINK_FIELDS) {
    const value = String(form.get(field) ?? "").trim();
    if (value) out[field] = value;
  }
  return out;
}

export const interactionActions = {
  createInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const occurred = occurredAt(form);
    if (!occurred) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/interactions", {
      body: {
        kind: String(form.get("kind") ?? "note") as "note",
        occurred_at: occurred,
        subject: String(form.get("subject") ?? "").trim(),
        body_text: String(form.get("body_text") ?? "").trim() || null,
        direction: String(form.get("direction") ?? "none") as "none",
        ...links(form),
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { ok: true };
  },

  updateInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const occurred = occurredAt(form);
    const { error } = await apiFor(event).PATCH("/api/v1/interactions/{interaction_id}", {
      params: { path: { interaction_id: id } },
      body: {
        kind: String(form.get("kind") ?? "note") as "note",
        ...(occurred ? { occurred_at: occurred } : {}),
        subject: String(form.get("subject") ?? "").trim(),
        body_text: String(form.get("body_text") ?? "").trim() || null,
        direction: String(form.get("direction") ?? "none") as "none",
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { ok: true };
  },

  deleteInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/interactions/{interaction_id}", {
      params: { path: { interaction_id: id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { ok: true };
  },

  approveInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/interactions/{interaction_id}/approve", {
      params: { path: { interaction_id: id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { ok: true };
  },

  /**
   * Move / re-link (#147). One dialog, two API paths: a manual row changes links through the
   * ordinary PATCH (own/any write scope); a gmail row goes through the owner-only review
   * remap. An empty picker posts "" and clears the link (explicit null).
   */
  moveInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const body: Record<string, string | null> = {};
    for (const field of LINK_FIELDS) {
      const value = String(form.get(field) ?? "").trim();
      body[field] = value || null;
    }
    const api = apiFor(event);
    const { error } =
      String(form.get("source") ?? "") === "gmail"
        ? await api.POST("/api/v1/interactions/{interaction_id}/remap", {
            params: { path: { interaction_id: id } },
            body,
          })
        : await api.PATCH("/api/v1/interactions/{interaction_id}", {
            params: { path: { interaction_id: id } },
            body,
          });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { ok: true };
  },

  /**
   * Create a contact from an unknown email participant (#160): the chip's ＋ opens the full
   * contact dialog prefilled with name + address; a checked "link to client" box carries the
   * interaction's company. Rides on every host page that spreads these actions, so the flow
   * exists wherever the timeline renders.
   */
  createParticipantContact: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const first_name = String(form.get("first_name") ?? "").trim();
    if (!first_name) return fail(400, { qcError: "errors.required" });
    const company_id = String(form.get("company_id") ?? "").trim();
    const { error } = await apiFor(event).POST("/api/v1/contacts", {
      body: {
        first_name,
        last_name: String(form.get("last_name") ?? "").trim() || null,
        email: String(form.get("email") ?? "").trim() || null,
        phone: String(form.get("phone") ?? "").trim() || null,
        job_title: String(form.get("job_title") ?? "").trim() || null,
        // The API links and promotes the first to primary only when the company is new to
        // the contact; an unchecked box simply creates an unlinked contact.
        company_ids: company_id ? [company_id] : undefined,
        custom: parseCustom(form.get("custom")),
      },
    });
    if (error) return fail(400, { qcError: apiErrorKey(error).key });
    return { ok: true };
  },

  rejectInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/interactions/{interaction_id}/reject", {
      params: { path: { interaction_id: id } },
      body: { suppress_thread: form.get("suppress_thread") === "1" },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { ok: true };
  },
};
