/**
 * Company-page form actions the marketing panel posts to (issue #134).
 *
 * Spread into `companies/[id]/+page.server.ts` alongside the interactions/drive panel contracts —
 * a panel's edit mode posts to the *host* page's actions (docs/UX.md). Linking/unlinking is
 * gated on `marketing.link.manage` at the API; these just forward the form.
 */
import { fail } from "@sveltejs/kit";
import type { RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";
import type { MarketingSource } from "$lib/modules/marketing/types";

// The union used to be redeclared here and drifted the moment a fifth source landed (#300's
// prediction, missed again by `rankmath`): a local copy of a vocabulary the API owns is a
// second place to remember, and this one was never remembered. Imported instead, so a new
// source is one edit in `types.ts`.

function parseConfig(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(String(raw ?? "{}"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as Record<string, unknown>)
      : {};
  } catch {
    return {};
  }
}

/**
 * The one write behind every connect control (#338).
 *
 * `companyId` is how the two callers differ and the only way they differ. On a client's page the
 * route *is* the client, so it is read from `event.params.id` and a posted value would be a
 * second answer free to disagree with it. Away from one — `/marketing`, `/marketing/google-ads` —
 * the dialog asks, so the form carries it.
 */
async function link(event: RequestEvent, companyId: string) {
  const form = await event.request.formData();
  const source = String(form.get("source") ?? "") as MarketingSource;
  const external_id = String(form.get("external_id") ?? "").trim();
  const display_name = String(form.get("display_name") ?? "").trim();
  const website_id = String(form.get("website_id") ?? "").trim();
  const company_id = companyId || String(form.get("company_id") ?? "").trim();
  if (!company_id || !source || !external_id || !display_name) {
    return fail(400, { error: "errors.required" });
  }

  const { error } = await apiFor(event).POST("/api/v1/marketing/links", {
    body: {
      company_id,
      website_id: website_id || null,
      source,
      external_id,
      display_name,
      config: parseConfig(form.get("config")),
    },
  });
  if (error) return fail(400, { error: apiErrorKey(error).key });
  return { marketingLinked: true };
}

/**
 * Mounted by the pages that connect a source **without** a client in the route, beside
 * `createCompanyAction` so the dialog's ＋ can mint one (docs/UX.md's picker rule).
 */
export const marketingConnectActions = {
  marketingLink: (event: RequestEvent) => link(event, ""),
};

export const marketingActions = {
  marketingLink: (event: RequestEvent) => link(event, event.params.id as string),

  marketingUnlink: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const link_id = String(form.get("link_id") ?? "").trim();
    if (!link_id) return fail(400, { error: "errors.required" });
    await apiFor(event).DELETE("/api/v1/marketing/links/{link_id}", {
      params: { path: { link_id } },
    });
    return { marketingUnlinked: true };
  },

  marketingSettings: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const show_key_events = String(form.get("show_key_events") ?? "") === "true";
    const { error } = await apiFor(event).PUT("/api/v1/marketing/companies/{company_id}/settings", {
      params: { path: { company_id: event.params.id as string } },
      body: { show_key_events },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { marketingSettingsSaved: true };
  },
};
