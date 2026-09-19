/**
 * Company-page form actions the marketing panel posts to (issue #134).
 *
 * Spread into `companies/[id]/+page.server.ts` alongside the interactions/drive panel contracts —
 * a panel's edit mode posts to the *host* page's actions (docs/UX.md). Linking/unlinking is
 * gated on `marketing.link.manage` at the API; these just forward the form.
 */
import { fail } from "@sveltejs/kit";
import type { RequestEvent } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";
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

// --- the AI Search overview (docs/SERANKING.md) ------------------------------------------------ //
const AI_ENGINES = ["all", "ai-overview", "ai-mode", "chatgpt", "perplexity", "gemini"] as const;
type AiEngine = (typeof AI_ENGINES)[number];
const AI_SCOPES = ["base_domain", "domain", "url"] as const;
type AiScope = (typeof AI_SCOPES)[number];

/** The client these three writes are about: the route where it names one, else the form. */
function aiCompany(event: RequestEvent, form: FormData): string {
  return (event.params.id as string | undefined) || String(form.get("company_id") ?? "").trim();
}

/**
 * One client's diff over the house settings, **posted whole**: every blank is sent as `null`,
 * which is how "volg de standaard" is said — omitting it would mean "leave alone" and a field
 * somebody emptied would silently keep overriding (§18).
 */
async function aiSearchSettings(event: RequestEvent) {
  const form = await event.request.formData();
  const company_id = aiCompany(event, form);
  if (!company_id) return fail(400, { error: "errors.required" });
  const enabled = String(form.get("enabled") ?? "");
  const scope = String(form.get("scope") ?? "");
  // "Own engines" is its own switch: an unticked list must mean *follow the house*, never
  // "on, and asking nothing" — so the boxes only count while the switch says they do.
  const engines =
    form.get("engines_mode") === "own"
      ? form
          .getAll("engines")
          .map(String)
          .filter((value): value is AiEngine => (AI_ENGINES as readonly string[]).includes(value))
      : [];
  const { error } = await apiFor(event).PUT(
    "/api/v1/marketing/companies/{company_id}/ai-search/settings",
    {
      params: { path: { company_id } },
      body: {
        enabled: enabled === "on" ? true : enabled === "off" ? false : null,
        engines: engines.length ? engines : null,
        source:
          String(form.get("source") ?? "")
            .trim()
            .toLowerCase() || null,
        scope: (AI_SCOPES as readonly string[]).includes(scope) ? (scope as AiScope) : null,
        target: String(form.get("target") ?? "").trim() || null,
        brand: String(form.get("brand") ?? "").trim() || null,
      },
    },
  );
  if (error) return fail(400, { error: apiErrorKey(error).key });
  return { aiSearchSaved: true };
}

/** Ask SE Ranking again now — 800 units per engine choice, a manager's deliberate press. */
async function aiSearchRefresh(event: RequestEvent) {
  const form = await event.request.formData();
  const company_id = aiCompany(event, form);
  if (!company_id) return fail(400, { error: "errors.required" });
  const { data, error } = await apiFor(event).POST(
    "/api/v1/marketing/companies/{company_id}/ai-search/refresh",
    { params: { path: { company_id } } },
  );
  if (error) return fail(400, { error: apiErrorKey(error).key });
  // A refused re-read keeps the stored figures and says so on *this* response only, so it is
  // handed to the block here — the read that follows would show good numbers and no reason.
  return { aiSearchRefreshed: true, aiSearchNotice: data?.notice ?? null };
}

/** Which brand SE Ranking attributes to the target (100 units) — answered into the brand box. */
async function aiSearchBrand(event: RequestEvent) {
  const form = await event.request.formData();
  const company_id = aiCompany(event, form);
  if (!company_id) return fail(400, { error: "errors.required" });
  // What is in the editor's boxes, not what is stored: a manager correcting a domain looks up
  // the domain they typed.
  const { data, error } = await apiFor(event).POST(
    "/api/v1/marketing/companies/{company_id}/ai-search/brand",
    {
      params: { path: { company_id } },
      body: {
        target: String(form.get("target") ?? "").trim() || null,
        source:
          String(form.get("source") ?? "")
            .trim()
            .toLowerCase() || null,
      },
    },
  );
  if (error) return fail(400, { error: apiErrorKey(error).key });
  return { aiSearchBrands: data?.brands ?? [] };
}

const aiSearchActions = {
  marketingAiSearchSettings: aiSearchSettings,
  marketingAiSearchRefresh: aiSearchRefresh,
  marketingAiSearchBrand: aiSearchBrand,
};

/**
 * Mounted by the pages that connect a source **without** a client in the route, beside
 * `createCompanyAction` so the dialog's ＋ can mint one (docs/UX.md's picker rule).
 */
export const marketingConnectActions = {
  marketingLink: (event: RequestEvent) => link(event, ""),
  // The org-wide dashboard draws the same Search Console section, so the upload posts here too.
  marketingImportAiVisibility: importAiVisibility,
  // The AI Search block is drawn on the org-wide dashboard too; the client rides the form.
  ...aiSearchActions,
};

/**
 * Search Console's Generative AI export, uploaded onto one link (docs/GOOGLE_SEARCH_CONSOLE.md
 * §6a). Multipart goes through a plain fetch — the typed client has no serializer for it — and
 * the API's own refusal (a grouped export, a file with no dates) is relayed as its key, so the
 * card says *why* rather than "something went wrong".
 */
async function importAiVisibility(event: RequestEvent) {
  const form = await event.request.formData();
  const link_id = String(form.get("link_id") ?? "").trim();
  const file = form.get("file");
  if (!link_id || !(file instanceof File) || file.size === 0) {
    return fail(400, { error: "errors.required" });
  }
  const body = new FormData();
  body.append("file", file, file.name);
  const res = await event.fetch(
    `${apiBaseUrl()}/api/v1/marketing/links/${encodeURIComponent(link_id)}/ai-visibility/import`,
    {
      method: "POST",
      headers: {
        cookie: event.request.headers.get("cookie") ?? "",
        "x-forwarded-host": event.request.headers.get("host") ?? "",
      },
      body,
    },
  );
  if (!res.ok) {
    if (res.status === 413) return fail(413, { error: "errors.upload_too_large" });
    const payload: unknown = await res.json().catch(() => null);
    return fail(res.status >= 500 ? 500 : 400, { error: apiErrorKey(payload).key });
  }
  const result = (await res.json()) as {
    days: number;
    date_from: string;
    date_to: string;
    total: number;
    imported: { at: string; date_from: string; date_to: string; days: number };
  };
  return { aiImported: result };
}

export const marketingActions = {
  marketingLink: (event: RequestEvent) => link(event, event.params.id as string),
  marketingImportAiVisibility: importAiVisibility,
  ...aiSearchActions,

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
