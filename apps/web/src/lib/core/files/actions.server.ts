/**
 * The form actions behind an attachment strip (`core/ui/FileAttachments`), one copy for every
 * host record — a task, a project, a client. Each host page spreads `fileActions("task")` into
 * its `actions`, the same contract the Drive and interaction panels use: a panel edits through
 * its host page, and the host owns the URL the panel posts to.
 *
 * Three actions, and why each is shaped as it is:
 *
 * - `uploadFile` — multipart through a plain `fetch`, because the typed client has no multipart
 *   serializer, with the same cookie + tenant host the client would send. It takes **every**
 *   `file` part in the form: a drop of four screenshots or a paste of one both arrive here, and
 *   a strip that accepts several files must not silently keep the first (§17's rule for a
 *   truncated import, one gesture over). The first refusal ends the batch and is reported —
 *   partial success is stated by the files that did land being on the page after the reload.
 * - `deleteFile` — the row; the bytes are the storage cron's (docs/STORAGE.md).
 * - `setFileVisibility` — the one editable fact about a stored file: whether the client may
 *   read it (`files.client_visible`).
 *
 * A refusal comes back as `fileError`, never `error` (#444): the strip renders it beside the
 * control that fired it, while the hosts paint `form.error` at the foot of the page.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";
import { apiFor } from "$lib/core/session";

export type FileEntityType = "task" | "project" | "company";

export function fileActions(entityType: FileEntityType) {
  return {
    uploadFile: async (event: RequestEvent) => {
      const entityId = event.params.id ?? "";
      const form = await event.request.formData();
      const uploads = form.getAll("file").filter((f): f is File => f instanceof File && f.size > 0);
      if (uploads.length === 0) return fail(400, { fileError: "errors.required" });
      // The strip may ask for the file to be readable by the client at upload time (a
      // screenshot pasted into a client-visible task); absent means the API's default, hidden.
      const clientVisible = form.get("client_visible") === "true";
      for (const upload of uploads) {
        const body = new FormData();
        body.append("file", upload, upload.name);
        const url = new URL(`${apiBaseUrl()}/api/v1/files`);
        url.searchParams.set("entity_type", entityType);
        url.searchParams.set("entity_id", entityId);
        const res = await event.fetch(url, {
          method: "POST",
          headers: {
            cookie: event.request.headers.get("cookie") ?? "",
            "x-forwarded-host": event.request.headers.get("host") ?? "",
          },
          body,
        });
        if (!res.ok) {
          return fail(400, {
            fileError: res.status === 413 ? "errors.upload_too_large" : "errors.upload_type",
          });
        }
        if (clientVisible) {
          const created = (await res.json()) as { id: string };
          await apiFor(event).PATCH("/api/v1/files/{file_id}", {
            params: { path: { file_id: created.id } },
            body: { client_visible: true },
          });
        }
      }
      return { fileUploaded: true };
    },

    deleteFile: async (event: RequestEvent) => {
      const form = await event.request.formData();
      const file_id = String(form.get("file_id") ?? "");
      if (file_id) {
        await apiFor(event).DELETE("/api/v1/files/{file_id}", { params: { path: { file_id } } });
      }
      return { fileDeleted: true };
    },

    setFileVisibility: async (event: RequestEvent) => {
      const form = await event.request.formData();
      const file_id = String(form.get("file_id") ?? "");
      const client_visible = form.get("client_visible") === "true";
      if (!file_id) return fail(400, { fileError: "errors.required" });
      const { error } = await apiFor(event).PATCH("/api/v1/files/{file_id}", {
        params: { path: { file_id } },
        body: { client_visible },
      });
      if (error) return fail(400, { fileError: "errors.forbidden" });
      return { fileVisibilityChanged: true };
    },
  };
}
