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

type MarketingSource = "ga4" | "gsc" | "gads";

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

export const marketingActions = {
  marketingLink: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const source = String(form.get("source") ?? "") as MarketingSource;
    const external_id = String(form.get("external_id") ?? "").trim();
    const display_name = String(form.get("display_name") ?? "").trim();
    if (!source || !external_id || !display_name) return fail(400, { error: "errors.required" });

    const { error } = await apiFor(event).POST("/api/v1/marketing/links", {
      body: {
        company_id: event.params.id as string,
        source,
        external_id,
        display_name,
        config: parseConfig(form.get("config")),
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { marketingLinked: true };
  },

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
    const { error } = await apiFor(event).PUT(
      "/api/v1/marketing/companies/{company_id}/settings",
      { params: { path: { company_id: event.params.id as string } }, body: { show_key_events } },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { marketingSettingsSaved: true };
  },
};
