import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey, type ApiError } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Instellingen → Google (#22): the install's own OAuth client (docs/GOOGLE.md §2), surface
// toggles, Drive layout and gmail policy. Admin-only; the client secret is write-only — the
// API reports `client_secret_configured` and never plays the value back (the SSO pattern).
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "google.settings.manage")) throw redirect(303, "/");
  const api = apiFor(event);
  const [settings, connections, members] = await Promise.all([
    api.GET("/api/v1/google/settings"),
    // The automation-connection selector: background Drive work acts as one of these.
    api.GET("/api/v1/google/connections"),
    api.GET("/api/v1/members/lookup"),
  ]);
  return {
    settings: settings.data ?? null,
    connections: connections.data ?? [],
    members: members.data ?? [],
  };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const text = (name: string) => String(form.get(name) ?? "").trim() || null;
    const { error } = await apiFor(event).PUT("/api/v1/google/settings", {
      body: {
        client_id: text("client_id"),
        // Empty means "keep the stored secret" — the API never returns it.
        client_secret: text("client_secret"),
        calendar_enabled: form.get("calendar_enabled") !== null,
        drive_enabled: form.get("drive_enabled") !== null,
        gmail_enabled: form.get("gmail_enabled") !== null,
        drive_shared_drive_id: text("drive_shared_drive_id"),
        drive_parent_folder_id: text("drive_parent_folder_id"),
        drive_template_folder_id: text("drive_template_folder_id"),
        drive_auto_provision: form.get("drive_auto_provision") !== null,
        automation_connection_user_id: text("automation_connection_user_id"),
        gmail_approval_mode: String(
          form.get("gmail_approval_mode") ?? "approval_required",
        ) as "approval_required",
        gmail_thread_followup: String(
          form.get("gmail_thread_followup") ?? "inherit_pending",
        ) as "inherit_pending",
        gmail_log_internal: form.get("gmail_log_internal") !== null,
      },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields as ApiError["fields"] });
    }
    return { saved: true };
  },

  provisionAll: async (event) => {
    // Backfill: a folder for every client that has none — "every client gets their folder".
    const { data, error } = await apiFor(event).POST("/api/v1/google/drive/provision-all");
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { provisioned: data?.queued ?? 0 };
  },
};
