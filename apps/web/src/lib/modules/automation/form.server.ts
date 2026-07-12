/**
 * Shared server-side plumbing for the rule editor (new + edit routes, issue #27).
 *
 * The editor serializes its condition tree and action list into two hidden JSON fields
 * (state → JSON is the component's job); this parses them back and shapes the API body.
 * Validation of the *content* (known trigger, known action types, well-formed tree) is the
 * API's job — the routes surface its error envelope, never duplicate its rules.
 */
import type { ServerLoadEvent } from "@sveltejs/kit";

import { apiErrorKey, type ApiError } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

export interface RuleFormBody {
  name: string;
  trigger_event: string;
  enabled: boolean;
  conditions: Record<string, unknown>;
  actions: { action_type: string; config: Record<string, unknown> }[];
}

export function parseRuleForm(form: FormData): RuleFormBody | null {
  let conditions: unknown;
  let actions: unknown;
  try {
    conditions = JSON.parse(String(form.get("conditions") || "{}"));
    actions = JSON.parse(String(form.get("actions") || "[]"));
  } catch {
    return null;
  }
  if (typeof conditions !== "object" || conditions === null || !Array.isArray(actions)) {
    return null;
  }
  return {
    name: String(form.get("name") ?? "").trim(),
    trigger_event: String(form.get("trigger_event") ?? ""),
    enabled: form.get("enabled") != null,
    conditions: conditions as Record<string, unknown>,
    actions: actions as RuleFormBody["actions"],
  };
}

/** The first *specific* key from the envelope: a field key beats the generic message. */
export function firstErrorKey(error: unknown): string {
  const parsed: ApiError = apiErrorKey(error);
  const fieldKey = parsed.fields && Object.values(parsed.fields)[0];
  return fieldKey ?? parsed.key;
}

/** The lookups the editor renders from; failures degrade to empty lists, never a crash. */
export async function loadEditorLookups(event: ServerLoadEvent) {
  const api = apiFor(event);
  const [catalog, members, templates] = await Promise.all([
    api.GET("/api/v1/automation/catalog"),
    api.GET("/api/v1/members/lookup"),
    // Task templates power task.create's template picker; someone without task read
    // simply gets the bare-title variant.
    api.GET("/api/v1/tasks/templates"),
  ]);
  return {
    catalog: catalog.data ?? { triggers: [], actions: [] },
    members: members.data ?? [],
    templates: (templates.data ?? []).map((tpl) => ({ id: tpl.id, name: tpl.name })),
  };
}
