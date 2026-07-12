/**
 * The form actions behind the Drive panels (issue #21). Host detail pages (company /
 * project / task) spread these into their `actions`, the same contract the interactions
 * panels use — a panel edits through its host page.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

export const driveActions = {
  linkDriveFile: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const entity_type = String(form.get("entity_type") ?? "");
    const entity_id = String(form.get("entity_id") ?? "");
    const drive_file_id = String(form.get("drive_file_id") ?? "");
    if (!entity_type || !entity_id || !drive_file_id)
      return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/google/drive/links", {
      body: {
        entity_type: entity_type as "company",
        entity_id,
        drive_file_id,
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { driveLinked: true };
  },

  unlinkDriveFile: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const link_id = String(form.get("link_id") ?? "");
    if (!link_id) return fail(400, { error: "errors.required" });
    // Unlink removes the reference; the Drive file itself is never deleted (#21).
    const { error } = await apiFor(event).DELETE("/api/v1/google/drive/links/{link_id}", {
      params: { path: { link_id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { driveUnlinked: true };
  },

  provisionDriveFolder: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const entity_type = String(form.get("entity_type") ?? "");
    const entity_id = String(form.get("entity_id") ?? "");
    if (!entity_type || !entity_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/google/drive/provision", {
      body: { entity_type: entity_type as "company", entity_id },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { driveProvisionQueued: true };
  },
};
