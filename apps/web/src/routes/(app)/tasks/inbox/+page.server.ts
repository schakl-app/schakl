import { fail, redirect } from "@sveltejs/kit";

import { editHref } from "$lib/core/edit-intent";
import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { taskCreateBody } from "$lib/modules/tasks/create";

import type { Actions, PageServerLoad } from "./$types";

/**
 * Tasks → E-mailinbox: the caller's own mails to the task address.
 *
 * A mail whose client could not be told waits here with everything it carried — the words,
 * the attachments, what the parser and the model made of it — for the person who sent it to
 * name the client. Finishing one runs the ordinary create as that person, under every rule a
 * form submit meets, and the mail's files move onto the task.
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "tasks.task.create")) throw redirect(303, "/tasks");
  const api = apiFor(event);
  const [rows, settings] = await Promise.all([
    api.GET("/api/v1/tasks/intake", { params: { query: { limit: 100 } } }),
    // The address is admin configuration; only somebody who may manage it reads it here.
    can(event.locals.user, "tasks.settings.manage")
      ? api.GET("/api/v1/tasks/settings")
      : Promise.resolve({ data: null }),
  ]);
  const items = rows.data ?? [];
  // The attachments of the parked ones, so the sender sees what will travel with the task.
  const parked = items.filter((row) => row.status === "needs_client" || row.status === "refused");
  const files = await Promise.all(
    parked.map((row) =>
      api
        .GET("/api/v1/files", {
          params: { query: { entity_type: "task_intake", entity_id: row.id } },
        })
        .then((res) => [row.id, res.data ?? []] as const),
    ),
  );
  return {
    items,
    filesByIntake: Object.fromEntries(files),
    intakeAddress: settings.data?.intake_address ?? null,
    open: event.url.searchParams.get("open"),
  };
};

export const actions: Actions = {
  /**
   * Finish a parked mail: the quick-create dialog's body (title, deadline, client, roster)
   * posted onto the mail's own create route, which carries the rest — the words, the steps,
   * the links, the attachments — off the row.
   */
  createFromIntake: async (event) => {
    const intakeId = event.url.searchParams.get("id");
    if (!intakeId) return fail(400, { qcError: "errors.validation" });
    const form = await event.request.formData();
    const body = taskCreateBody(form, { fallbackAssigneeUserId: event.locals.user?.id ?? null });
    if (!body) return fail(400, { qcError: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/tasks/intake/{intake_id}/create", {
      params: { path: { intake_id: intakeId } },
      body: {
        title: body.title,
        due_date: body.due_date,
        company_id: body.company_id,
        project_id: body.project_id,
        ...(body.assignees !== undefined
          ? { assignees: body.assignees }
          : { assignee_user_id: body.assignee_user_id ?? null }),
      },
    });
    if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
    throw redirect(303, editHref(`/tasks/${data.id}`));
  },

  discard: async (event) => {
    const form = await event.request.formData();
    const intakeId = String(form.get("id") ?? "");
    const { error } = await apiFor(event).DELETE("/api/v1/tasks/intake/{intake_id}", {
      params: { path: { intake_id: intakeId } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { discarded: true };
  },
};
