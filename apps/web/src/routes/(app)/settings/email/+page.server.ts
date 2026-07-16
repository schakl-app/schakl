import { fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// Org e-mail transport (#17): DB-stored, UI-configured — the official Brevo/SendGrid/SMTP2GO
// APIs or a plain SMTP relay. Admin-only (the API enforces `settings.email.manage`).
export const load: PageServerLoad = async (event) => {
  const api = apiFor(event);
  // Transport config + the tenant's auth-mail templates (#161 tier 2), both admin-gated.
  const [settings, templates, modules] = await Promise.all([
    api.GET("/api/v1/settings/email"),
    api.GET("/api/v1/settings/email/templates"),
    api.GET("/api/v1/meta/modules"),
  ]);
  return {
    settings: settings.data ?? null,
    templates: templates.data ?? null,
    locale: event.locals.locale,
    // The operator-provided transport (epic #199): offered as an explicit choice when set.
    instanceEmailAvailable: modules.data?.instance_email_available ?? false,
  };
};

export const actions: Actions = {
  save: async (event) => {
    const form = await event.request.formData();
    const provider = String(form.get("provider") ?? "") as "smtp" | "instance";
    const from_email = String(form.get("from_email") ?? "").trim();
    const from_name = String(form.get("from_name") ?? "").trim();
    // The instance transport (epic #199) sends from the instance's own address — no
    // from_email to enter; every bring-your-own provider still requires one.
    if (!provider || !from_name || (provider !== "instance" && !from_email)) {
      return fail(400, { error: "errors.required" });
    }

    const text = (name: string) => String(form.get(name) ?? "").trim() || null;
    const { error } = await apiFor(event).PUT("/api/v1/settings/email", {
      body: {
        provider,
        from_email: from_email || null,
        from_name,
        reply_to: text("reply_to"),
        host: text("host"),
        port: Number(form.get("port")) || null,
        security: (text("security") ?? undefined) as "starttls" | undefined,
        username: text("username"),
        // Empty secrets mean "keep what is stored" — the API never plays them back.
        password: text("password"),
        api_key: text("api_key"),
      },
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { saved: true };
  },

  delete: async (event) => {
    const { error } = await apiFor(event).DELETE("/api/v1/settings/email");
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { deleted: true };
  },

  test: async (event) => {
    const { data, error } = await apiFor(event).POST("/api/v1/settings/email/test");
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { test: data };
  },

  // --- tenant auth-mail templates (#161 tier 2) ---------------------------------- //
  saveTemplate: async (event) => {
    const form = await event.request.formData();
    const kind = String(form.get("kind") ?? "") as "reset" | "invite";
    const locale = String(form.get("locale") ?? "");
    const subject = String(form.get("subject") ?? "").trim();
    const body_html = String(form.get("body_html") ?? "").trim();
    const { error } = await apiFor(event).PUT("/api/v1/settings/email/templates", {
      body: { kind, locale, subject: subject || null, body_html: body_html || null },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { templateSaved: { kind, locale } };
  },

  testTemplate: async (event) => {
    const form = await event.request.formData();
    const kind = String(form.get("kind") ?? "") as "reset" | "invite";
    const locale = String(form.get("locale") ?? "");
    const subject = String(form.get("subject") ?? "").trim();
    const body_html = String(form.get("body_html") ?? "").trim();
    const { data, error } = await apiFor(event).POST("/api/v1/settings/email/templates/test", {
      body: { kind, locale, subject: subject || null, body_html: body_html || null },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { templateTest: data, templateKind: kind, templateLocale: locale };
  },
};
