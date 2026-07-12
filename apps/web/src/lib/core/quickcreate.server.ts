/**
 * Form actions behind the inline-create pickers (#115, docs/UX.md): a Combobox's
 * "＋ … toevoegen" opens a full create modal whose form posts to one of these. Shared by the
 * domains/hosting pages so each `+page.server.ts` mounts them under its own action names.
 *
 * On success they return `inlineCreated: { slot, id }`, which the page hands to the form
 * component to auto-select the new entity in the picker that asked for it. Errors come back
 * as `qcError`, deliberately distinct from the main form's `error` so a failed quick-create
 * doesn't paint the outer modal red.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

const PROVIDER_KINDS = ["registrar", "dns", "email", "hosting"] as const;

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}"));
  } catch {
    return {};
  }
}

export async function createCompanyAction(event: RequestEvent) {
  const form = await event.request.formData();
  const name = String(form.get("name") ?? "").trim();
  if (!name) return fail(400, { qcError: "errors.required" });
  const { data, error } = await apiFor(event).POST("/api/v1/companies", {
    body: {
      name,
      website: String(form.get("website") ?? "").trim() || null,
      status: String(form.get("status") ?? "active") as "active",
      custom: parseCustom(form.get("custom")),
    },
  });
  if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
  // The dialog echoes the slot of the picker that asked (a PartyPicker names its own),
  // so a company created from a party field never auto-selects into a sibling company field.
  return { inlineCreated: { slot: String(form.get("slot") ?? "") || "company", id: data.id } };
}

export async function createContactAction(event: RequestEvent) {
  const form = await event.request.formData();
  const first_name = String(form.get("first_name") ?? "").trim();
  if (!first_name) return fail(400, { qcError: "errors.required" });
  const { data, error } = await apiFor(event).POST("/api/v1/contacts", {
    body: {
      first_name,
      last_name: String(form.get("last_name") ?? "").trim() || null,
      email: String(form.get("email") ?? "").trim() || null,
      phone: String(form.get("phone") ?? "").trim() || null,
      job_title: String(form.get("job_title") ?? "").trim() || null,
      custom: parseCustom(form.get("custom")),
    },
  });
  if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
  return { inlineCreated: { slot: String(form.get("slot") ?? "") || "contact", id: data.id } };
}

export async function createProviderAction(event: RequestEvent) {
  const form = await event.request.formData();
  const name = String(form.get("name") ?? "").trim();
  const kind = String(form.get("kind") ?? "") as (typeof PROVIDER_KINDS)[number];
  if (!name || !PROVIDER_KINDS.includes(kind)) return fail(400, { qcError: "errors.required" });
  const { data, error } = await apiFor(event).POST("/api/v1/providers", {
    body: { kind, name, active: true, position: 0 },
  });
  if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
  // The slot is the kind: each form has at most one picker per provider kind.
  return { inlineCreated: { slot: kind, id: data.id } };
}
