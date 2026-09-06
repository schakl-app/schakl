import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey, type ApiError } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Instellingen → Microsoft 365 (docs/MICROSOFT.md §2): the agency's own Entra app registration,
// surface toggles, OneDrive layout and Outlook policy. Admin-only; the client secret is
// write-only — the API reports `client_secret_configured` and never plays the value back.
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "microsoft.settings.manage")) throw redirect(303, "/settings");
  const api = apiFor(event);
  const [settings, connections, members] = await Promise.all([
    api.GET("/api/v1/microsoft/settings"),
    // The automation-connection selector: background OneDrive work acts as one of these.
    api.GET("/api/v1/microsoft/connections"),
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
    const { error } = await apiFor(event).PUT("/api/v1/microsoft/settings", {
      body: {
        client_id: text("client_id"),
        // Empty means "keep the stored secret" — the API never returns it.
        client_secret: text("client_secret"),
        tenant_id: text("tenant_id"),
        calendar_enabled: form.get("calendar_enabled") !== null,
        onedrive_enabled: form.get("onedrive_enabled") !== null,
        outlook_enabled: form.get("outlook_enabled") !== null,
        onedrive_drive_id: text("onedrive_drive_id"),
        onedrive_parent_folder_id: text("onedrive_parent_folder_id"),
        onedrive_template_folder_id: text("onedrive_template_folder_id"),
        onedrive_auto_provision: form.get("onedrive_auto_provision") !== null,
        automation_connection_user_id: text("automation_connection_user_id"),
        outlook_approval_mode: String(
          form.get("outlook_approval_mode") ?? "approval_required",
        ) as "approval_required",
        outlook_thread_followup: String(
          form.get("outlook_thread_followup") ?? "inherit_pending",
        ) as "inherit_pending",
        outlook_log_internal: form.get("outlook_log_internal") !== null,
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
    const { data, error } = await apiFor(event).POST("/api/v1/microsoft/onedrive/provision-all");
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { provisioned: data?.queued ?? 0 };
  },
};
