/**
 * The form actions behind the OneDrive panels. Host detail pages (company / project / task)
 * spread these into their `actions`, the same contract `driveActions` uses — a panel edits
 * through its host page.
 *
 * A refusal comes back as `oneDriveError`, not `error` (#444's rule): the hosts render
 * `form.error` at the very bottom of the page, so a 409 from the create-folder button would
 * paint a red line two thousand pixels from the button that fired it. The panels render
 * `oneDriveError` themselves, beside the control it is about.
 *
 * A Graph item is addressed by **two** ids — the drive it lives in and the item — where a Drive
 * file is one, so every action that names a file carries the pair.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

export const oneDriveActions = {
  linkOneDriveFile: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const entity_type = String(form.get("entity_type") ?? "");
    const entity_id = String(form.get("entity_id") ?? "");
    const drive_id = String(form.get("drive_id") ?? "");
    const item_id = String(form.get("item_id") ?? "");
    if (!entity_type || !entity_id || !drive_id || !item_id)
      return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/microsoft/onedrive/links", {
      body: { entity_type: entity_type as "company", entity_id, drive_id, item_id },
    });
    if (error) return fail(400, { oneDriveError: apiErrorKey(error).key });
    return { oneDriveLinked: true };
  },

  /**
   * Point this record at an existing OneDrive folder (the picker). A separate action from
   * `linkOneDriveFile` because it is a separate act: the API asks for `microsoft.onedrive.manage`
   * when the record already has a folder, and never for a plain attachment.
   */
  setOneDriveFolder: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const entity_type = String(form.get("entity_type") ?? "");
    const entity_id = String(form.get("entity_id") ?? "");
    const drive_id = String(form.get("drive_id") ?? "");
    const item_id = String(form.get("item_id") ?? "");
    if (!entity_type || !entity_id || !drive_id || !item_id)
      return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PUT("/api/v1/microsoft/onedrive/folder", {
      body: { entity_type: entity_type as "company", entity_id, drive_id, item_id },
    });
    if (error) return fail(400, { oneDriveError: apiErrorKey(error).key });
    return { oneDriveFolderSet: true };
  },

  unlinkOneDriveFile: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const link_id = String(form.get("link_id") ?? "");
    if (!link_id) return fail(400, { error: "errors.required" });
    // Unlink removes the reference; the OneDrive item itself is never deleted.
    const { error } = await apiFor(event).DELETE("/api/v1/microsoft/onedrive/links/{link_id}", {
      params: { path: { link_id } },
    });
    if (error) return fail(400, { oneDriveError: apiErrorKey(error).key });
    return { oneDriveUnlinked: true };
  },

  /**
   * Move the item itself to the OneDrive recycle bin. The *other* act from `unlinkOneDriveFile`,
   * and never a rename of it: unlink says "this file is not about this record" and touches
   * nothing in OneDrive, this says "this file should not exist". The API runs it as the
   * signed-in user, so OneDrive's own permissions answer, and it drops every link naming that
   * item.
   */
  deleteOneDriveFile: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const drive_id = String(form.get("drive_id") ?? "");
    const item_id = String(form.get("item_id") ?? "");
    if (!drive_id || !item_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE(
      "/api/v1/microsoft/onedrive/files/{drive_id}/{item_id}",
      { params: { path: { drive_id, item_id } } },
    );
    if (error) return fail(400, { oneDriveError: apiErrorKey(error).key });
    return { oneDriveFileTrashed: true };
  },

  provisionOneDriveFolder: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const entity_type = String(form.get("entity_type") ?? "");
    const entity_id = String(form.get("entity_id") ?? "");
    if (!entity_type || !entity_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/microsoft/onedrive/provision", {
      body: { entity_type: entity_type as "company", entity_id },
    });
    if (error) return fail(400, { oneDriveError: apiErrorKey(error).key });
    return { oneDriveProvisionQueued: true };
  },
};
