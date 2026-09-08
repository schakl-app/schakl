/**
 * The form actions behind a subscription form, wherever it is mounted.
 *
 * `/subscriptions` is the list page and owns the create/edit dialog; a client's page hosts the
 * same form in a dialog of its own (`SubscriptionDialog`) so an agreement can be recorded from
 * where the client is and the page is still underneath it when it closes — the shape #402 gave
 * hours. SvelteKit actions live on the page, so each host spreads `subscriptionActions` into its
 * own `actions`, and the list page mounts the same handlers under its short names.
 *
 * The three quick-creates ride along on purpose: a picker's "＋ … toevoegen" posts to a fixed
 * action name, and a form whose pickers only create on one of its two hosts is half a form
 * (`InteractionForm`'s rule). The names are prefixed so that spreading them beside another
 * module's `createProject` can never silently replace it.
 *
 * **Host contract:** the dialog posts to `?/createSubscription`, and its pickers to
 * `?/createSubscriptionProject`, `?/createSubscriptionType` and `?/createSubscriptionCompany`.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { createCompanyAction } from "$lib/core/quickcreate.server";
import { apiFor } from "$lib/core/session";
import { createErrorKey, slugify } from "$lib/core/slug";
import { readAutoInvoiceMode } from "$lib/modules/invoicing/types";
import { parseLabelI18n } from "$lib/modules/subscriptions/manage.server";

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}"));
  } catch {
    return {};
  }
}

/** What an agreement may be attached to — the API's own vocabulary (`LinkEntityType`). */
const LINK_KINDS = ["project", "task", "website"] as const;
type LinkKind = (typeof LINK_KINDS)[number];

function linkKind(raw: unknown): LinkKind | null {
  return (LINK_KINDS as readonly string[]).includes(String(raw)) ? (raw as LinkKind) : null;
}

/**
 * The form posts every link as one JSON field (single-save surface) — the projects it covers
 * *and* the websites it keeps online. The save replaces the whole set, so a kind this parser
 * dropped would be a kind every save silently unlinked.
 */
function parseLinks(
  raw: FormDataEntryValue | null,
): { entity_type: LinkKind; entity_id: string }[] {
  try {
    const parsed = JSON.parse(String(raw ?? "[]"));
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((l) => {
      const kind = linkKind(l?.entity_type);
      return kind && typeof l.entity_id === "string"
        ? [{ entity_type: kind, entity_id: l.entity_id as string }]
        : [];
    });
  } catch {
    return [];
  }
}

/** An agreement's own say over its preset — the billing direction, printing the notes:
 *  `"true"`/`"false"`, or `""` to follow the standard subscription (and the type). */
function readOverride(raw: FormDataEntryValue | null): boolean | null {
  const value = String(raw ?? "").trim();
  return value === "" ? null : value === "true";
}

/** The recurring-agreement fields create and update share (#30). */
export function subscriptionBody(form: FormData) {
  const amount = String(form.get("amount") ?? "").trim();
  return {
    name: String(form.get("name") ?? "").trim(),
    subscription_type_id: String(form.get("subscription_type_id") ?? "").trim() || null,
    status: String(form.get("status") ?? "active") as "active",
    interval: String(form.get("interval") ?? "monthly") as "monthly",
    start_date: String(form.get("start_date") ?? "").trim(),
    end_date: String(form.get("end_date") ?? "").trim() || null,
    next_invoice_date: String(form.get("next_invoice_date") ?? "").trim() || null,
    // "Already invoiced up to": empty withdraws the statement (an explicit null).
    billed_until: String(form.get("billed_until") ?? "").trim() || null,
    // "" is the inherit choice, and it must reach the API as an explicit null: the column's
    // third state is "follow the org", which is not the same as any level.
    auto_invoice_mode: readAutoInvoiceMode(form.get("auto_invoice_mode")),
    // Same three states: "" follows the standard subscription and the type, and reaches the
    // API as an explicit null so a cleared override is actually cleared.
    billed_in_advance_override: readOverride(form.get("billed_in_advance_override")),
    // And whether its notes print on its invoices: "" follows the standard subscription.
    notes_on_invoice_override: readOverride(form.get("notes_on_invoice_override")),
    included_hours: String(form.get("included_hours") ?? "").trim() || null,
    notes: String(form.get("notes") ?? "").trim() || null,
    amount: amount || undefined,
    custom: parseCustom(form.get("custom")),
    links: parseLinks(form.get("links")),
  };
}

export async function createSubscription(event: RequestEvent) {
  const form = await event.request.formData();
  const body = subscriptionBody(form);
  const company_id = String(form.get("company_id") ?? "");
  if (!body.name || !company_id || !body.start_date || body.amount === undefined) {
    return fail(400, { error: "errors.required" });
  }
  // Only create carries it: it records which preset the form was prefilled from, so a
  // later rename of that standard subscription reaches this agreement (an edit never
  // re-links, and renaming the agreement itself is how it stops following).
  const subscription_template_id =
    String(form.get("subscription_template_id") ?? "").trim() || null;
  const { error } = await apiFor(event).POST("/api/v1/subscriptions", {
    body: { ...body, company_id, amount: body.amount, subscription_template_id } as never,
  });
  if (error) {
    const e = apiErrorKey(error);
    return fail(400, { error: e.key, fields: e.fields });
  }
  return { created: true };
}

/** Inline project create from the links picker (docs/UX.md — per-picker definition of
 *  done). Returns `inlineCreated` so the form auto-selects the new project as a link. */
export async function createSubscriptionProject(event: RequestEvent) {
  const form = await event.request.formData();
  const name = String(form.get("name") ?? "").trim();
  if (!name) return fail(400, { qcError: "errors.required" });
  // A project belongs to a client (`ProjectCreate`): named here so the dialog says
  // which field, instead of relaying a bare validation envelope.
  const company_id = String(form.get("company_id") ?? "").trim();
  if (!company_id) return fail(400, { qcError: "errors.projects_company_required" });
  const { data, error } = await apiFor(event).POST("/api/v1/projects", {
    body: {
      name,
      company_id,
      status: "active",
      budget_period: "total",
      currency: event.locals.theme.currency,
      // Made for an agreement, so it starts non-billable (#284): the retainer already pays
      // for this work. Saving the agreement links it and would clear the flag anyway — this
      // is so the project reads right the moment it exists, not one save later.
      billable_default: false,
      custom: {},
    },
  });
  if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
  return { inlineCreated: { slot: "project", id: data.id, name: data.name } };
}

/** Inline type create from the form's picker (docs/UX.md — per-picker definition of done).
 *  The full type dialog minus the spawn list. One label language is enough (docs/UX.md):
 *  a missing locale falls back at render time. */
export async function createSubscriptionType(event: RequestEvent) {
  const form = await event.request.formData();
  const label_i18n = parseLabelI18n(form);
  if (Object.keys(label_i18n).length === 0) {
    return fail(400, { qcError: "errors.required" });
  }
  // The tenant only types the label; the immutable key is derived from it (#234).
  const key = slugify(label_i18n.nl || label_i18n.en || "");
  if (!key) return fail(400, { qcError: "errors.label_no_key" });
  const { data, error, response } = await apiFor(event).POST("/api/v1/subscriptions/types", {
    // A type made inline bills in arrears until somebody says otherwise in Instellingen — the
    // cron's original reading, and the only default that changes nothing for an existing type.
    body: {
      key,
      label_i18n,
      position: 0,
      active: true,
      task_template_ids: [],
      billed_in_advance: false,
      // ...and covers no website until Instellingen says so, for the same reason.
      covers_websites: false,
    },
  });
  if (error || !data) return fail(400, { qcError: createErrorKey(error, response) });
  const name = label_i18n.nl || label_i18n.en || key;
  return { inlineCreated: { slot: "subscription_type", id: data.id, name } };
}

/** What a host page spreads to mount `SubscriptionDialog` — the names the dialog posts to. */
export const subscriptionActions = {
  createSubscription,
  createSubscriptionProject,
  createSubscriptionType,
  createSubscriptionCompany: createCompanyAction,
};

/**
 * The actions behind a record's Abonnementen panel (`WebsiteSubscriptionsPanel`): attach one
 * agreement to the record whose page this is, or detach one. The record's id comes from the
 * **route**, never from the form — the panel is drawn on one record, and posting an
 * `entity_id` beside it would be a second opinion about which (the uptime panel's rule).
 * Which *kind* of record it is travels with the form, because a panel can be mounted on more
 * than one host; it is checked against the API's own vocabulary.
 */
export const subscriptionLinkActions = {
  subscriptionLink: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const subscription_id = String(form.get("subscription_id") ?? "").trim();
    const entity_type = linkKind(form.get("entity_type"));
    if (!subscription_id || !entity_type) {
      return fail(400, { subscriptionLinkError: "errors.required" });
    }
    const { error } = await apiFor(event).POST("/api/v1/subscriptions/{subscription_id}/links", {
      params: { path: { subscription_id } },
      body: { entity_type, entity_id: event.params.id as string },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { subscriptionLinkError: e.fields?.links ?? e.key });
    }
    return { subscriptionLinked: true };
  },

  subscriptionUnlink: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const subscription_id = String(form.get("subscription_id") ?? "").trim();
    const entity_type = linkKind(form.get("entity_type"));
    if (!subscription_id || !entity_type) {
      return fail(400, { subscriptionLinkError: "errors.required" });
    }
    const { error } = await apiFor(event).DELETE(
      "/api/v1/subscriptions/{subscription_id}/links/{entity_type}/{entity_id}",
      { params: { path: { subscription_id, entity_type, entity_id: event.params.id as string } } },
    );
    if (error) return fail(400, { subscriptionLinkError: apiErrorKey(error).key });
    return { subscriptionUnlinked: true };
  },
};
