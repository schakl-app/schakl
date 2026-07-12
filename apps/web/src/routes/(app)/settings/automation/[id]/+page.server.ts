import { error as httpError, fail, redirect } from "@sveltejs/kit";

import {
  firstErrorKey,
  loadEditorLookups,
  parseRuleForm,
} from "$lib/modules/automation/form.server";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "automation.rule.write")) throw redirect(303, "/settings");
  const [rule, lookups] = await Promise.all([
    apiFor(event).GET("/api/v1/automation/rules/{rule_id}", {
      params: { path: { rule_id: event.params.id } },
    }),
    loadEditorLookups(event),
  ]);
  if (!rule.data) throw httpError(404);
  return { rule: rule.data, ...lookups };
};

export const actions: Actions = {
  update: async (event) => {
    const body = parseRuleForm(await event.request.formData());
    if (!body || !body.name) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/automation/rules/{rule_id}", {
      params: { path: { rule_id: event.params.id } },
      body,
    });
    if (error) return fail(400, { error: firstErrorKey(error) });
    throw redirect(303, "/settings/automation");
  },

  dryRun: async (event) => {
    const form = await event.request.formData();
    const body = parseRuleForm(form);
    const entity_id = String(form.get("entity_id") ?? "").trim();
    if (!body || !entity_id) return fail(400, { error: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/automation/dry-run", {
      body: {
        trigger_event: body.trigger_event,
        conditions: body.conditions,
        actions: body.actions,
        entity_id,
      },
    });
    if (error) return fail(400, { error: firstErrorKey(error) });
    return { dryRun: data };
  },
};
