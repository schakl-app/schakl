import { error, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { parseParty } from "$lib/core/party";
import { can } from "$lib/core/permissions";
import {
  createCompanyAction,
  createContactAction,
  createProviderAction,
} from "$lib/core/quickcreate.server";
import { entityPanelsFor } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";
// The WordPress and uptime panels edit through this page, because SvelteKit actions live on the
// page. One import and one spread each: the route learns nothing about application passwords or
// about monitors (CLAUDE.md §6).
import { uptimeActions } from "$lib/integrations/uptime/uptime-actions.server";
import { wordpressActions } from "$lib/integrations/wordpress/wordpress-actions.server";
import "$lib/modules";

import type { Actions, PageServerLoad } from "./$types";

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}"));
  } catch {
    return {};
  }
}

/**
 * A website's own detail page.
 *
 * It used to have none: the list linked to `/domains/<id>#website`, so two different records —
 * the name and the site running on it — shared one page, and "open this website" landed on a
 * domain with a website section halfway down it. They are separate rows with separate lifecycles
 * (a domain outlives the site on it), so they get separate pages, cross-linked both ways.
 *
 * Only what is about *this* website is loaded here. Every picker and definition set — hosting,
 * clients, employees, contacts, the website and hosting custom fields — comes from the section
 * layout, which does not rerun when you move between websites (#290).
 */
export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "websites.website.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const website_id = event.params.id;

  // The activity trail rides the core entity-panel seam (§16) — composed, never imported.
  const context = { entityId: website_id, periodStart: null };
  const enabled = event.locals.theme?.enabledModules ?? [];
  const panels = entityPanelsFor(enabled, "website", event.locals.user);

  const [website, ...panelData] = await Promise.all([
    api.GET("/api/v1/websites/{website_id}", { params: { path: { website_id } } }),
    ...panels.map((panel) => panel.load(api, context)),
  ]);

  if (!website.data) throw error(404, { code: "not_found", message: "errors.not_found" });

  return {
    website: website.data,
    panels: panels.map((panel, i) => ({
      key: panel.key,
      titleKey: panel.titleKey,
      data: panelData[i],
    })),
    context,
    agencyLabel: event.locals.theme?.brandName ?? "",
    locale: event.locals.locale,
  };
};

export const actions: Actions = {
  ...wordpressActions,
  ...uptimeActions,

  update: async (event) => {
    const form = await event.request.formData();
    const { error: err } = await apiFor(event).PATCH("/api/v1/websites/{website_id}", {
      params: { path: { website_id: event.params.id } },
      body: {
        root: form.get("root") !== "www",
        technical_owner: parseParty(form.get("technical_owner")),
        hosting_id: String(form.get("hosting_id") ?? "") || null,
        uptime_enabled: form.get("uptime_enabled") !== null,
        custom: parseCustom(form.get("custom")),
      },
    });
    if (err) {
      const e = apiErrorKey(err);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { updated: true };
  },

  delete: async (event) => {
    await apiFor(event).DELETE("/api/v1/websites/{website_id}", {
      params: { path: { website_id: event.params.id } },
    });
    throw redirect(303, "/websites");
  },

  createCompany: createCompanyAction,
  createContact: createContactAction,
  createProvider: createProviderAction,

  // Inline-create for the hosting picker (#115): the full HostingForm in a modal.
  createHosting: async (event) => {
    const form = await event.request.formData();
    const body = {
      name: String(form.get("name") ?? "").trim(),
      company_id: String(form.get("company_id") ?? "") || null,
      provider_id: String(form.get("provider_id") ?? "") || null,
      ip_address: String(form.get("ip_address") ?? "").trim() || null,
      contact: parseParty(form.get("contact")),
      custom: parseCustom(form.get("custom")),
    };
    if (!body.name) return fail(400, { qcError: "errors.required" });
    const { data, error: err } = await apiFor(event).POST("/api/v1/hosting", { body });
    if (err || !data) return fail(400, { qcError: apiErrorKey(err).key });
    return { inlineCreated: { slot: "hosting_account", id: data.id } };
  },
};
