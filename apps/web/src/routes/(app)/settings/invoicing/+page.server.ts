import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "invoicing.settings.manage")) throw redirect(303, "/");
  const api = apiFor(event);
  const [settings, taxRates, templates, providers, products] = await Promise.all([
    api.GET("/api/v1/invoicing/settings"),
    api.GET("/api/v1/invoicing/tax-rates", { params: { query: { include_inactive: true } } }),
    api.GET("/api/v1/invoicing/templates", { params: { query: { include_inactive: true } } }),
    api.GET("/api/v1/invoicing/providers"),
    api.GET("/api/v1/invoicing/products", { params: { query: { include_inactive: true } } }),
  ]);
  return {
    settings: settings.data ?? null,
    taxRates: taxRates.data ?? [],
    templates: templates.data ?? [],
    products: products.data ?? [],
    providers: (providers.data ?? []) as { key: string; label: string }[],
    locale: event.locals.locale,
  };
};

function text(form: FormData, key: string): string | undefined {
  const value = String(form.get(key) ?? "").trim();
  return value || undefined;
}

export const actions: Actions = {
  saveSeller: async (event) => {
    const form = await event.request.formData();
    const { error } = await apiFor(event).PUT("/api/v1/invoicing/settings", {
      body: {
        company_details: {
          name: text(form, "name") ?? null,
          address_line1: text(form, "address_line1") ?? null,
          address_line2: text(form, "address_line2") ?? null,
          postal_code: text(form, "postal_code") ?? null,
          city: text(form, "city") ?? null,
          country: text(form, "country")?.toUpperCase() ?? null,
          vat_number: text(form, "vat_number") ?? null,
          coc_number: text(form, "coc_number") ?? null,
          iban: text(form, "iban") ?? null,
          email: text(form, "email") ?? null,
          phone: text(form, "phone") ?? null,
        },
        tax_country: text(form, "tax_country")?.toUpperCase(),
      } as never,
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { saved: true };
  },
  saveDefaults: async (event) => {
    const form = await event.request.formData();
    const days = (value: string | undefined) => (value ? Number(value) : undefined);
    const { error } = await apiFor(event).PUT("/api/v1/invoicing/settings", {
      body: {
        default_due_days: days(text(form, "default_due_days")),
        quote_valid_days: days(text(form, "quote_valid_days")),
        default_tax_rate_id: text(form, "default_tax_rate_id") || null,
        default_template_id: text(form, "default_template_id") || null,
        default_hourly_rate: text(form, "default_hourly_rate") ?? null,
        prices_include_tax: form.get("prices_include_tax") === "1",
        invoice_number_format: text(form, "invoice_number_format"),
        quote_number_format: text(form, "quote_number_format"),
        invoice_next_seq: days(text(form, "invoice_next_seq")),
        quote_next_seq: days(text(form, "quote_next_seq")),
        number_reset_yearly: form.get("number_reset_yearly") === "1",
      } as never,
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { saved: true };
  },
  saveReminders: async (event) => {
    const form = await event.request.formData();
    const raw = String(form.get("reminder_days") ?? "");
    const reminder_days = raw
      .split(",")
      .map((part) => Number(part.trim()))
      .filter((n) => Number.isFinite(n) && n >= 0);
    const { error } = await apiFor(event).PUT("/api/v1/invoicing/settings", {
      body: {
        reminders_enabled: form.get("reminders_enabled") === "1",
        reminder_days,
      } as never,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { saved: true };
  },
  saveRate: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const body = {
      label_i18n: { nl: text(form, "label_nl") ?? "", en: text(form, "label_en") ?? "" },
      rate: text(form, "rate") ?? "0",
      category: text(form, "category") ?? "standard",
      ledger_code: text(form, "ledger_code") ?? null,
      is_default: form.get("is_default") === "1",
      active: form.get("active") !== "0",
    };
    const api = apiFor(event);
    const { error } = id
      ? await api.PATCH("/api/v1/invoicing/tax-rates/{tax_rate_id}", {
          params: { path: { tax_rate_id: id } },
          body: body as never,
        })
      : await api.POST("/api/v1/invoicing/tax-rates", { body: body as never });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { rateSaved: true };
  },
  // Default products (owner request): the tenant's line presets.
  saveProduct: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const body = {
      name: text(form, "name") ?? "",
      description: text(form, "description") ?? null,
      unit: text(form, "unit") ?? null,
      unit_price: text(form, "unit_price") ?? "0",
      tax_rate_id: text(form, "tax_rate_id") ?? null,
    };
    const api = apiFor(event);
    const { error } = id
      ? await api.PATCH("/api/v1/invoicing/products/{product_id}", {
          params: { path: { product_id: id } },
          body: body as never,
        })
      : await api.POST("/api/v1/invoicing/products", { body: body as never });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { productSaved: true };
  },
  toggleProduct: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const { error } = await apiFor(event).PATCH("/api/v1/invoicing/products/{product_id}", {
      params: { path: { product_id: id } },
      body: { active: form.get("active") === "1" } as never,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { productSaved: true };
  },
  deleteProduct: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const { error } = await apiFor(event).DELETE("/api/v1/invoicing/products/{product_id}", {
      params: { path: { product_id: id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { productDeleted: true };
  },
  toggleRate: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const { error } = await apiFor(event).PATCH("/api/v1/invoicing/tax-rates/{tax_rate_id}", {
      params: { path: { tax_rate_id: id } },
      body: { active: form.get("active") === "1" } as never,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { rateSaved: true };
  },
  deleteRate: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const { error } = await apiFor(event).DELETE("/api/v1/invoicing/tax-rates/{tax_rate_id}", {
      params: { path: { tax_rate_id: id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { rateDeleted: true };
  },
  saveTemplate: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    let config: unknown;
    try {
      config = JSON.parse(String(form.get("config") ?? "{}"));
    } catch {
      return fail(400, { error: "errors.validation" });
    }
    const body = {
      name: text(form, "name") ?? "",
      config,
      is_default: form.get("is_default") === "1",
      active: form.get("active") !== "0",
    };
    if (!body.name) return fail(400, { error: "errors.required" });
    const api = apiFor(event);
    const { error } = id
      ? await api.PATCH("/api/v1/invoicing/templates/{template_id}", {
          params: { path: { template_id: id } },
          body: body as never,
        })
      : await api.POST("/api/v1/invoicing/templates", { body: body as never });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { templateSaved: true };
  },
  deleteTemplate: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const { error } = await apiFor(event).DELETE("/api/v1/invoicing/templates/{template_id}", {
      params: { path: { template_id: id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { templateDeleted: true };
  },
};
