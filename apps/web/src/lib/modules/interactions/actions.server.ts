/**
 * The form actions behind every contactmomenten panel. SvelteKit actions live on the page, so
 * each host detail page (company / project / contact / task) spreads these into its own
 * `actions` — the panel body posts to `?/createInteraction` etc. wherever it renders.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";
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
    // "Voeg aan mijn uren toe" (#175): the linked entry rides the same request. Its start is
    // the moment's own time field (#184); only the end is extra. Times follow the time module's
    // wall-clock-as-UTC convention, on the interaction's date.
    const date = String(form.get("occurred_date") ?? "").trim();
    const logStart = String(form.get("occurred_time") ?? "").trim();
    const logEnd = String(form.get("log_end") ?? "").trim();
    const logTime =
      form.get("log_time") === "1" && date && logStart && logEnd
        ? { started_at: `${date}T${logStart}:00Z`, ended_at: `${date}T${logEnd}:00Z` }
        : undefined;
    const api = apiFor(event);
    const { data, error } = await api.POST("/api/v1/interactions", {
      body: {
        kind: String(form.get("kind") ?? "note"),
        occurred_at: occurred,
        subject: String(form.get("subject") ?? "").trim(),
        body_text: String(form.get("body_text") ?? "").trim() || null,
        direction: String(form.get("direction") ?? "none") as "none",
        ...(logTime ? { log_time: logTime } : {}),
        ...links(form),
      },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    // "Close task with this" ticked in the create form (#232, mirroring the approve flow):
    // the create stands on its own — a close failure reports, it never rolls the create back.
    const task_id = String(form.get("task_id") ?? "").trim();
    const close_status = String(form.get("close_status") ?? "").trim();
    if (form.get("close_task") === "1" && task_id && close_status) {
      const { error: closeError } = await api.PATCH("/api/v1/tasks/{task_id}", {
        params: { path: { task_id } },
        body: { status: close_status, closing_interaction_id: data.id },
      });
      if (closeError) {
        return fail(400, { error: apiErrorKey(closeError).key, createdButCloseFailed: true });
      }
    }
    return { ok: true };
  },

  /**
   * Log an email from its `.eml` export (#262). Multipart through a plain fetch — the typed
   * client has no multipart serializer (the file-upload actions do the same) — carrying the
   * same cookie + tenant host the client would send.
   *
   * The API answers 409 when this `Message-ID` is already on the timeline; that is a question,
   * not a refusal, so it comes back as `emlDuplicate` and the form offers "toch vastleggen".
   */
  uploadInteractionEml: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const upload = form.get("file");
    if (!(upload instanceof File) || upload.size === 0)
      return fail(400, { error: "errors.required" });
    const body = new FormData();
    body.append("file", upload, upload.name);
    for (const [field, value] of Object.entries(links(form))) body.append(field, value);
    if (form.get("allow_duplicate") === "1") body.append("allow_duplicate", "true");
    const res = await event.fetch(`${apiBaseUrl()}/api/v1/interactions/upload-eml`, {
      method: "POST",
      headers: {
        cookie: event.request.headers.get("cookie") ?? "",
        "x-forwarded-host": event.request.headers.get("host") ?? "",
      },
      body,
    });
    if (!res.ok) {
      const envelope = await res.json().catch(() => null);
      const parsed = apiErrorKey(envelope);
      return fail(res.status === 413 ? 413 : 400, {
        // A field-level key (bad type, unreadable message) is the specific one; fall back to
        // the envelope's message so a 403/500 still says something true.
        error: parsed.fields?.file ?? parsed.key,
        emlDuplicate: res.status === 409,
      });
    }
    const data = await res.json();
    return {
      ok: true,
      emlUploaded: {
        stored: Number(data?.attachments_stored ?? 0),
        skipped: Number(data?.attachments_skipped ?? 0),
      },
    };
  },

  updateInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const occurred = occurredAt(form);
    const { error } = await apiFor(event).PATCH("/api/v1/interactions/{interaction_id}", {
      params: { path: { interaction_id: id } },
      body: {
        kind: String(form.get("kind") ?? "note"),
        ...(occurred ? { occurred_at: occurred } : {}),
        subject: String(form.get("subject") ?? "").trim(),
        // Only fields the form actually rendered are sent (the API's PATCH is
        // `exclude_unset`): editing an uploaded email (#262) offers neither the note editor
        // nor the direction select, and an absent field must leave the received message
        // alone rather than blanking its body or resetting its direction to "none".
        ...(form.has("body_text")
          ? { body_text: String(form.get("body_text") ?? "").trim() || null }
          : {}),
        ...(form.has("direction")
          ? { direction: String(form.get("direction") ?? "none") as "none" }
          : {}),
        // The edit form carries all four link pickers now (#263, was contact-only since #173):
        // an edit may set, repoint or clear any of them, the same explicit-null contract the
        // move dialog's PATCH uses. The client rides along as the value the form derived from
        // the project/task — `_resolve_links(partial=True)` does not derive over an explicit
        // key, so posting a bare null here would drop the client the picker just showed.
        ...Object.fromEntries(
          LINK_FIELDS.map((field) => [field, String(form.get(field) ?? "").trim() || null]),
        ),
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
    // Assign links in the same step (#183) only when the approve came from the review dialog
    // (`assign=1`); the one-click inline approve sends no links and touches none.
    const body =
      form.get("assign") === "1"
        ? Object.fromEntries(
            LINK_FIELDS.map((field) => [field, String(form.get(field) ?? "").trim() || null]),
          )
        : undefined;
    const api = apiFor(event);
    const { error } = await api.POST("/api/v1/interactions/{interaction_id}/approve", {
      params: { path: { interaction_id: id } },
      ...(body ? { body } : {}),
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    // "Close the task with this contact moment" ticked in the review dialog (#157 extended):
    // the approve stands on its own — a close failure reports, it never rolls the approve back.
    const task_id = String(form.get("task_id") ?? "").trim();
    const close_status = String(form.get("close_status") ?? "").trim();
    if (form.get("close_task") === "1" && task_id && close_status) {
      const { error: closeError } = await api.PATCH("/api/v1/tasks/{task_id}", {
        params: { path: { task_id } },
        body: { status: close_status, closing_interaction_id: id },
      });
      if (closeError) {
        return fail(400, { error: apiErrorKey(closeError).key, approvedButCloseFailed: true });
      }
    }
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

  /**
   * Inline-create behind the form's contact picker (#173): creates the contact immediately
   * and answers with `inlineCreated` so the picker that asked auto-selects it (docs/UX.md).
   * Distinct from `createParticipantContact`, whose chip flow has no picker to select into.
   */
  createInteractionContact: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const first_name = String(form.get("first_name") ?? "").trim();
    if (!first_name) return fail(400, { qcError: "errors.required" });
    const company_id = String(form.get("company_id") ?? "").trim();
    const { data, error } = await apiFor(event).POST("/api/v1/contacts", {
      body: {
        first_name,
        last_name: String(form.get("last_name") ?? "").trim() || null,
        email: String(form.get("email") ?? "").trim() || null,
        phone: String(form.get("phone") ?? "").trim() || null,
        job_title: String(form.get("job_title") ?? "").trim() || null,
        company_ids: company_id ? [company_id] : undefined,
        custom: parseCustom(form.get("custom")),
      },
    });
    if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
    return {
      inlineCreated: { slot: String(form.get("slot") ?? "") || "interaction_contact", id: data.id },
    };
  },

  /**
   * Inline-create behind the review dialog's task picker (docs/UX.md): creates the task
   * immediately and answers with `inlineCreated` so the picker auto-selects it. The dialog's
   * current client/project ride along, so the new task lands where the email is being filed.
   */
  createInteractionTask: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const title = String(form.get("title") ?? "").trim();
    if (!title) return fail(400, { qcError: "errors.required" });
    const company_id = String(form.get("company_id") ?? "").trim();
    const project_id = String(form.get("project_id") ?? "").trim();
    const assignee_user_id = String(form.get("assignee_user_id") ?? "").trim();
    const due_date = String(form.get("due_date") ?? "").trim();
    const { data, error } = await apiFor(event).POST("/api/v1/tasks", {
      body: {
        title,
        company_id: company_id || undefined,
        project_id: project_id || undefined,
        assignee_user_id: assignee_user_id || undefined,
        due_date: due_date || undefined,
        priority: "normal",
        requires_interaction: false,
        visible_to_client: false,
      },
    });
    if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
    return {
      inlineCreated: {
        slot: String(form.get("slot") ?? "") || "move_task",
        id: data.id,
        project_id: data.project_id ?? null,
        company_id: data.company_id ?? null,
      },
    };
  },

  /**
   * Close the linked task with this contact moment (#157): sets the picked terminal status
   * and designates the interaction as the close's justification. The API validates linkage
   * and the per-status requires_interaction policy.
   */
  closeTaskWithInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const task_id = String(form.get("task_id") ?? "");
    const interaction_id = String(form.get("interaction_id") ?? "");
    const status = String(form.get("status") ?? "");
    if (!task_id || !interaction_id || !status) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/tasks/{task_id}", {
      params: { path: { task_id } },
      body: { status, closing_interaction_id: interaction_id },
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

  /**
   * Manually glue this email onto another's conversation (#272): a reply Gmail didn't thread
   * automatically is folded onto the target the user picked. Owner-only, gmail-only — the API
   * enforces both on the row and the target.
   */
  addInteractionToConversation: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const target_interaction_id = String(form.get("target_interaction_id") ?? "");
    if (!id || !target_interaction_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST(
      "/api/v1/interactions/{interaction_id}/add-to-conversation",
      {
        params: { path: { interaction_id: id } },
        body: { target_interaction_id },
      },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { ok: true };
  },
};
