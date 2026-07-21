/** The price-increase actions (#30, #231), shared by the subscriptions list and the
 * standard-subscriptions tab — one implementation, two surfaces (the manage.server.ts
 * pattern). Scope rides one field: `all`, `type:<id>`, `subscription:<id>` or
 * `template:<id>`; the single-row values are what a row's ⋮ shortcut posts. */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

const PRICE_MODES = ["percent", "amount", "set"] as const;

/** The fields preview and apply share. `null` = invalid. */
function priceIncreaseBody(form: FormData) {
  const mode = String(form.get("mode") ?? "");
  const value = String(form.get("value") ?? "").trim();
  const valid_from = String(form.get("valid_from") ?? "").trim();
  if (!PRICE_MODES.includes(mode as (typeof PRICE_MODES)[number])) return null;
  if (!value || Number.isNaN(Number(value)) || !valid_from) return null;
  const scope = String(form.get("scope") ?? "all");
  const [kind, id] = scope.includes(":") ? scope.split(":", 2) : [scope, ""];
  if (kind !== "all" && !id) return null;
  return {
    mode: mode as (typeof PRICE_MODES)[number],
    value,
    valid_from,
    subscription_type_id: kind === "type" ? id : null,
    subscription_id: kind === "subscription" ? id : null,
    subscription_template_id: kind === "template" ? id : null,
    // A single-row scope never drags templates along (#231): the API refuses the combo.
    include_templates:
      (kind === "all" || kind === "type") && form.get("include_templates") !== null,
  };
}

export const priceIncreaseActions = {
  /** Preview: every in-scope row with its would-be amount — nothing written. */
  previewPriceIncrease: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const body = priceIncreaseBody(form);
    if (!body) return fail(400, { priceError: "errors.required" });
    const { data, error } = await apiFor(event).POST(
      "/api/v1/subscriptions/price-increase/preview",
      { body },
    );
    if (error || !data) return fail(400, { priceError: apiErrorKey(error).key });
    // The scope echoes back so the modal only renders a preview made for what it shows.
    return { pricePreview: data, priceScope: String(form.get("scope") ?? "all") };
  },

  /** Apply: dated price-history rows per subscription, a direct default for a template. */
  applyPriceIncrease: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const body = priceIncreaseBody(form);
    if (!body) return fail(400, { priceError: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/subscriptions/price-increase", {
      body,
    });
    if (error || !data) return fail(400, { priceError: apiErrorKey(error).key });
    return { priceApplied: data.items.length, priceAppliedTemplates: data.templates.length };
  },
};
