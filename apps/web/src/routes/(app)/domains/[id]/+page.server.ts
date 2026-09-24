import { error, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { originOf } from "$lib/core/origin";
import { parseParty } from "$lib/core/party";
import {
  createCompanyAction,
  createContactAction,
  createProviderAction,
} from "$lib/core/quickcreate.server";
import { entityPanelsFor } from "$lib/core/registry";
import { readAutoInvoiceMode } from "$lib/modules/invoicing/types";
import { apiFor } from "$lib/core/session";
// The Cloudflare panel edits through this page's actions, the way the Drive panels do — a panel
// cannot own form actions, so the host spreads them in (CLAUDE.md §6: one import, no internals).
import { cloudflareActions } from "$lib/integrations/cloudflare/cloudflare-actions.server";
import { readInvoiceable } from "$lib/modules/domains/normalize";
// The registrar panel edits through this page too (#296) — same contract, one import.
import { oxxaActions } from "$lib/integrations/oxxa/oxxa-actions.server";
// And the uptime panel, which is on a domain because a monitor may watch a host inside this zone
// that will never be a website — a client's mail server, VPN endpoint or NAS.
import { uptimeActions } from "$lib/integrations/uptime/uptime-actions.server";
import "$lib/modules";

import type { Actions, PageServerLoad } from "./$types";

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}"));
  } catch {
    return {};
  }
}

export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  const domain_id = event.params.id;

  // The activity trail rides the core entity-panel seam (§16) — composed, never imported.
  const context = { entityId: domain_id, periodStart: null };
  const enabled = event.locals.theme?.enabledModules ?? [];
  const panels = entityPanelsFor(enabled, "domain", event.locals.user);
  // Only what is about *this* domain. Every picker and definition set that does not vary by id
  // — clients, providers, employees, contacts, the domain custom fields, the two inline
  // quick-create sets, the TLD prices — comes from the section layout, which does not rerun
  // when you move between domains (#290).
  // A domain carries one site per *address* (`app/core/webaddress.py`), so the page lists them
  // rather than holding one; the record itself lives on its own page, and creating one is the
  // websites dialog opened on this domain (`/websites?domain=…&new=1`).
  const [domain, websites, ...panelData] = await Promise.all([
    api.GET("/api/v1/domains/{domain_id}", { params: { path: { domain_id } } }),
    api.GET("/api/v1/websites", {
      params: { query: { domain_id, limit: 20, offset: 0, sort: "name" } },
    }),
    ...panels.map((panel) => panel.load(api, context)),
  ]);

  if (!domain.data) throw error(404, { code: "not_found", message: "errors.not_found" });

  return {
    domain: domain.data,
    websites: websites.data?.items ?? [],
    websiteTotal: websites.data?.total ?? 0,
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
  ...cloudflareActions,
  ...oxxaActions,
  ...uptimeActions,

  update: async (event) => {
    const form = await event.request.formData();
    const email_enabled = form.get("email_enabled") !== null;
    const { error: err } = await apiFor(event).PATCH("/api/v1/domains/{domain_id}", {
      params: { path: { domain_id: event.params.id } },
      body: {
        name: String(form.get("name") ?? "").trim() || undefined,
        company_id: String(form.get("company_id") ?? "") || undefined,
        status: String(form.get("status") ?? "active") as never,
        redirect_url: String(form.get("redirect_url") ?? "").trim() || null,
        start_date: String(form.get("start_date") ?? "").trim() || undefined,
        // Empty **resets** the renewal date rather than stopping the cycle: the API re-resolves
        // the register's expiry, else the anniversary of the start date. So it is an explicit
        // null, not `undefined` — "leave it alone" is what not sending the key means.
        next_invoice_date: String(form.get("next_invoice_date") ?? "").trim() || null,
        // Empty withdraws the "already invoiced up to" statement — an explicit null, because
        // absent means leave alone.
        billed_until: String(form.get("billed_until") ?? "").trim() || null,
        // Empty clears the override: the TLD list price applies again.
        price_override: String(form.get("price_override") ?? "").trim() || null,
        // Three-state (#298): "" clears the decision back to *follow the register*.
        invoiceable: readInvoiceable(form.get("invoiceable")),
        // "" is the inherit choice; it must reach the API as an explicit null.
        auto_invoice_mode: readAutoInvoiceMode(form.get("auto_invoice_mode")),
        registrar_provider_id: String(form.get("registrar_provider_id") ?? "") || null,
        dns_provider_id: String(form.get("dns_provider_id") ?? "") || null,
        registry_contact: parseParty(form.get("registry_contact")),
        email_enabled,
        email_provider_id: email_enabled
          ? String(form.get("email_provider_id") ?? "") || null
          : null,
        email_contact: email_enabled ? parseParty(form.get("email_contact")) : null,
        custom: parseCustom(form.get("custom")),
      },
    });
    if (err) {
      const e = apiErrorKey(err);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { updated: true };
  },

  refresh: async (event) => {
    const { error: err } = await apiFor(event).POST("/api/v1/domains/{domain_id}/refresh", {
      params: { path: { domain_id: event.params.id } },
    });
    if (err) return fail(400, { error: apiErrorKey(err).key });
    return { refreshed: true };
  },

  delete: async (event) => {
    await apiFor(event).DELETE("/api/v1/domains/{domain_id}", {
      params: { path: { domain_id: event.params.id } },
    });
    // Back where the detour started (#408); the register only when nothing said otherwise. This
    // is the case the browser-only breadcrumb trail can never serve — a server-side redirect has
    // no `sessionStorage` to read, which is why the origin travels in the URL.
    throw redirect(303, originOf(event.url) ?? "/domains");
  },

  createCompany: createCompanyAction,
  createContact: createContactAction,
  createProvider: createProviderAction,
};
