import { redirect } from "@sveltejs/kit";

import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";
import { manageActions } from "$lib/modules/subscriptions/manage.server";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  const user = event.locals.user;
  if (!can(user, "subscriptions.type.manage") && !can(user, "subscriptions.template.manage")) {
    throw redirect(303, "/");
  }
  const api = apiFor(event);
  const [types, templates, taskTemplates] = await Promise.all([
    api.GET("/api/v1/subscriptions/types", { params: { query: { include_inactive: true } } }),
    api.GET("/api/v1/subscriptions/templates"),
    // For the "spawn on activation" picker; admin-gated screen, so the read grant is there.
    api.GET("/api/v1/tasks/templates"),
  ]);
  return {
    types: types.data ?? [],
    templates: templates.data ?? [],
    taskTemplates: (taskTemplates.data ?? []).map((tpl) => ({ id: tpl.id, name: tpl.name })),
    canManageTypes: can(user, "subscriptions.type.manage"),
    canManageTemplates: can(user, "subscriptions.template.manage"),
    locale: event.locals.locale,
  };
};

// Shared with the subscriptions page's own beheer sections (manage.server.ts).
export const actions: Actions = { ...manageActions };
