/**
 * The form actions behind the Outlook half of the interactions timeline (docs/MICROSOFT.md §6).
 *
 * A host list page spreads these into its `actions`, the same contract `gmailActions` uses —
 * the buttons live on the interactions timeline, but what they drive is a Microsoft connection,
 * so the calls belong to this module rather than to whichever screen renders them. The four
 * mirror the Gmail four exactly, because the screen draws one picker and one refresh button for
 * whichever mailbox the viewer holds: a cooldown is data rather than `fail()`, a refused lookup
 * keeps the reference on the form, and an import's duplicate refusal is a warning to confirm.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import type { components } from "$lib/core/api/schema";
import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";
import { checked } from "$lib/core/forms";
import { linkBody } from "$lib/modules/interactions/actions.server";

export type OutlookRefreshResult = components["schemas"]["OutlookRefreshResult"];

export const outlookActions = {
  refreshOutlook: async (event: RequestEvent) => {
    const { data, error } = await apiFor(event).POST("/api/v1/microsoft/outlook/refresh", {});
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { outlookRefresh: data as OutlookRefreshResult };
  },

  /** Resolve a pasted Outlook reference, or read one conversation (#342, Outlook-flavoured). */
  lookupOutlookMessage: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const reference = String(form.get("reference") ?? "").trim();
    const conversationId = String(form.get("thread_id") ?? "").trim();
    if (!reference && !conversationId) return fail(400, { error: "errors.required" });
    const api = apiFor(event);
    const { data, error } = conversationId
      ? await api.GET("/api/v1/microsoft/outlook/threads/{conversation_id}", {
          params: { path: { conversation_id: conversationId } },
        })
      : await api.GET("/api/v1/microsoft/outlook/lookup", { params: { query: { reference } } });
    if (error) return fail(400, { error: apiErrorKey(error).key, outlookReference: reference });
    return { outlookLookup: data, outlookReference: reference };
  },

  /** Search the caller's **own** mailbox for a message to file — named fields only (#372). */
  searchOutlookMessages: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const query = {
      participant: String(form.get("participant") ?? "").trim() || undefined,
      subject: String(form.get("subject") ?? "").trim() || undefined,
      after: String(form.get("after") ?? "").trim() || undefined,
      before: String(form.get("before") ?? "").trim() || undefined,
    };
    const { data, error } = await apiFor(event).GET("/api/v1/microsoft/outlook/search", {
      params: { query },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { outlookLookup: data };
  },

  /** Log one message the poller skipped, filed where the dialog says. */
  importOutlookMessage: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const message_id = String(form.get("message_id") ?? "").trim();
    if (!message_id) return fail(400, { error: "errors.required" });
    const body = linkBody(form);
    const { data, error } = await apiFor(event).POST("/api/v1/microsoft/outlook/import", {
      body: {
        message_id,
        company_id: (body.company_id as string | null) ?? null,
        project_id: (body.project_id as string | null) ?? null,
        task_id: (body.task_id as string | null) ?? null,
        ...(Array.isArray(body.contact_ids) ? { contact_ids: body.contact_ids } : {}),
        ...(Array.isArray(body.task_ids) ? { task_ids: body.task_ids } : {}),
        allow_duplicate: form.get("allow_duplicate") === "1",
        enrich_task: checked(form, "enrich_task"),
      },
    });
    if (error)
      return fail(400, {
        error: apiErrorKey(error).key,
        outlookDuplicate: apiErrorKey(error).key === "errors.interactions_eml_duplicate",
      });
    return { outlookImported: data };
  },
};
