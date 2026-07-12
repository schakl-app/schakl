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
