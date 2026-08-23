import { error as httpError, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { originOf } from "$lib/core/origin";
import { can } from "$lib/core/permissions";
import { createContactAction } from "$lib/core/quickcreate.server";
import { entityPanelsFor } from "$lib/core/registry";
import { apiFor } from "$lib/core/session";
import "$lib/modules";
import { contactLookups, documentBody, processBody } from "$lib/modules/invoicing/form.server";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "invoicing.invoice.read")) throw redirect(303, "/");
  const api = apiFor(event);
  const invoice_id = event.params.id;
  const context = { entityId: invoice_id, periodStart: null };
  const enabled = event.locals.theme?.enabledModules ?? [];
  const canWrite = can(event.locals.user, "invoicing.invoice.write");
  const isPortal = event.locals.user?.isPortal ?? false;
  /**
   * The trail is the agency's own record of this document — who changed the total while it
   * was a draft, when it was issued and sent. A client never saw the draft (#266), so the
   * history of one is not theirs to read; the tasks detail page draws the same line
   * (`isPortal ? [] : data.panels`). `activity.read` is a client default, so the *panel*
   * gate is what stops it here.
   */
  const panels = isPortal ? [] : entityPanelsFor(enabled, "invoice", event.locals.user);
  /**
   * Coming back from a provider's checkout (#304). The API stamps `?return=1` on the URL it
   * hands the provider, so this runs on exactly that hop and on no ordinary view of an invoice.
   *
   * Before the fetch below, so the first paint is usually already right: a webhook is
   * asynchronous and routinely lands after the redirect, which is why a payer used to be shown
   * the word "open" seconds after paying. The API bounds the outbound call (non-final attempts
   * only, one per attempt per five seconds), so a reloaded return URL costs nothing and an
   * ordinary visit never reaches this at all.
   */
  const returning = event.url.searchParams.get("return") === "1";
  if (returning && can(event.locals.user, "invoicing.payment.link")) {
    await api.POST("/api/v1/invoicing/invoices/{invoice_id}/payment-intents/refresh", {
      params: { path: { invoice_id } },
    });
  }
  /**
   * Six lookups below exist for one consumer: `DocumentForm`. Loading them for a viewer who
   * cannot open it was always six wasted round-trips; since #266 four of them also answer 403
   * to a client (the price list, the template library, the tax rates and the seller's bank
   * details are `:any` now), so the load would be asking for what it may not have.
   * docs/UX.md: skip the fetch you would 403 on, and gate the read rather than only the write.
   *
   * Still **one** round of calls — a skipped lookup is `undefined` inside the same
   * `Promise.all` (the section layout's pattern), never a second await.
   */
  const [
    invoice,
    contacts,
    taxRates,
    products,
    templates,
    settings,
    contactDefinitions,
    ...panelData
  ] = await Promise.all([
    api.GET("/api/v1/invoicing/invoices/{invoice_id}", {
      params: { path: { invoice_id } },
    }),
    canWrite
      ? api.GET("/api/v1/contacts", {
          params: { query: { limit: 200, count: false, sort: "first_name" } },
        })
      : undefined,
    canWrite ? api.GET("/api/v1/invoicing/tax-rates") : undefined,
    canWrite ? api.GET("/api/v1/invoicing/products") : undefined,
    canWrite
      ? api.GET("/api/v1/invoicing/templates", { params: { query: { include_inactive: true } } })
      : undefined,
    canWrite ? api.GET("/api/v1/invoicing/settings") : undefined,
    canWrite
      ? api.GET("/api/v1/custom-fields/definitions", {
          params: { query: { entity_type: "contact" } },
        })
      : undefined,
    ...panels.map((panel) => panel.load(api, context)),
  ]);
  if (!invoice.data) throw httpError(404);

  return {
    invoice: invoice.data,
    contacts: contactLookups(contacts?.data?.items),
    taxRates: taxRates?.data ?? [],
    products: products?.data ?? [],
    templates: templates?.data ?? [],
    settings: settings?.data ?? null,
    contactDefinitions: contactDefinitions?.data ?? [],
    context,
    panels: panels.map((panel, i) => ({
      key: panel.key,
      titleKey: panel.titleKey,
      data: panelData[i],
    })),
    canWrite,
    canSend: can(event.locals.user, "invoicing.invoice.send"),
    canDelete: can(event.locals.user, "invoicing.invoice.delete"),
    canPay: can(event.locals.user, "invoicing.payment.write"),
    /**
     * Online payment (#267). Two questions, and they are genuinely different ones.
     *
     * *May you start a collection?* is the route's floor, because the `client` role holds
     * `invoicing.payment.link:own` — for a client in the portal that button is the entire
     * point of the screen, so gating it on `!isPortal` would hide the one control they came
     * for (docs/UX.md, the client-portal entry). *May you re-ask the provider?* is the same
     * key at `:any`: a repair action that spends an outbound call on every press, and a
     * client's status arrives by callback and, failing that, by the hourly reconcile.
     *
     * Neither costs a round-trip. The attempts themselves ride `InvoiceRead.intents` on the
     * detail read we already make, and whether one *can* be started rides `online_payment`
     * beside it — the portal must not be able to read which accounts the agency connected.
     */
    canStartPayment: can(event.locals.user, "invoicing.payment.link"),
    canSyncPayment: can(event.locals.user, "invoicing.payment.link", "any"),
    /** See above: drives the "we are confirming your payment" line and its bounded polling. */
    returning,
    /** See the list route: the agency's view of a document, or only your own copy (#266). */
    canReadRegister: can(event.locals.user, "invoicing.invoice.read", "any"),
    locale: event.locals.locale,
  };
};

type InvoicePath = { params: { path: { invoice_id: string } } };

function pathFor(event: { params: { id: string } }): InvoicePath {
  return { params: { path: { invoice_id: event.params.id } } };
}

export const actions: Actions = {
  createContact: createContactAction,
  save: async (event) => {
    const form = await event.request.formData();
    const draft = String(form.get("_status") ?? "") === "draft";
    const body: Record<string, unknown> = draft ? documentBody(form) : processBody(form);
    // Only fields the form actually posted may change — the sidebar's reminders toggle,
    // for example, must not clear the due date it never carried.
    if (form.has("due_date")) {
      body.due_date = String(form.get("due_date") ?? "").trim() || null;
    }
    if (form.has("reminders_paused")) {
      body.reminders_paused = form.get("reminders_paused") === "1";
    }
    if (!form.has("lines")) delete body.lines;
    if (!form.has("contact_id")) delete body.contact_id;
    if (!form.has("locale")) delete body.locale;
    if (!form.has("template_id")) delete body.template_id;
    if (!form.has("reference")) delete body.reference;
    if (!form.has("intro")) delete body.intro;
    if (!form.has("notes")) delete body.notes;
    if (!form.has("exchange_rate")) delete body.exchange_rate;
    const { error } = await apiFor(event).PATCH("/api/v1/invoicing/invoices/{invoice_id}", {
      ...pathFor(event),
      body: body as never,
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, { error: e.key, fields: e.fields });
    }
    return { saved: true };
  },
  issue: async (event) => {
    const { error } = await apiFor(event).POST("/api/v1/invoicing/invoices/{invoice_id}/issue", {
      ...pathFor(event),
      body: {},
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { issued: true };
  },
  send: async (event) => {
    const form = await event.request.formData();
    const to = String(form.get("to") ?? "").trim();
    const { error } = await apiFor(event).POST("/api/v1/invoicing/invoices/{invoice_id}/send", {
      ...pathFor(event),
      body: {
        to: to || null,
        message: String(form.get("message") ?? "").trim() || null,
        email: form.get("email") !== "0",
      } as never,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { sent: true };
  },
  remind: async (event) => {
    const { error } = await apiFor(event).POST(
      "/api/v1/invoicing/invoices/{invoice_id}/remind",
      pathFor(event),
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { reminded: true };
  },
  cancel: async (event) => {
    const { error } = await apiFor(event).POST(
      "/api/v1/invoicing/invoices/{invoice_id}/cancel",
      pathFor(event),
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { cancelled: true };
  },
  credit: async (event) => {
    const { data, error } = await apiFor(event).POST(
      "/api/v1/invoicing/invoices/{invoice_id}/credit",
      pathFor(event),
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    throw redirect(303, `/invoices/${data.id}`);
  },
  payment: async (event) => {
    const form = await event.request.formData();
    const paid_on = String(form.get("paid_on") ?? "");
    const amount = String(form.get("amount") ?? "");
    if (!paid_on || !amount) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/invoicing/invoices/{invoice_id}/payments", {
      ...pathFor(event),
      body: {
        paid_on,
        amount,
        method: String(form.get("method") ?? "bank"),
        note: String(form.get("note") ?? "").trim() || null,
      } as never,
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { paymentSaved: true };
  },
  /**
   * Open a checkout for this invoice (#267).
   *
   * The body is empty **on purpose**: the API charges the invoice's outstanding balance,
   * recomputed at creation time, and a client-supplied amount is the one thing a payment
   * endpoint must never accept. There is no account picker either, and there does not need to
   * be one: the API resolves a single credential, and prefers the live one over a test one
   * when an agency is running both (`docs/PAYMENTS.md` §2 — a test key was never a candidate
   * for a client's money). Two *live* credentials is the one case it refuses to guess at
   * (`errors.invoicing.payment_account_ambiguous`), and the agency resolves that by switching
   * one off in Instellingen → Mollie rather than by answering a prompt on every invoice.
   *
   * Failures report as `paymentError` rather than the page's generic `error`, so the refusal
   * lands in the card that produced it instead of at the top of a long document page.
   */
  startPayment: async (event) => {
    const { error } = await apiFor(event).POST(
      "/api/v1/invoicing/invoices/{invoice_id}/payment-intents",
      { ...pathFor(event), body: {} },
    );
    if (error) return fail(400, { paymentError: apiErrorKey(error).key });
    return { paymentStarted: true };
  },
  /** Re-ask the provider about one attempt. The repair path for a callback that never arrived
   *  — a firewall, a Zero Trust rule in front of the webhook path, an outage — so that a
   *  payment already made can be settled without waiting for the next reconcile pass. */
  syncPayment: async (event) => {
    const form = await event.request.formData();
    const intent_id = String(form.get("intent_id") ?? "");
    if (!intent_id) return fail(400, { paymentError: "errors.required" });
    const { error } = await apiFor(event).POST(
      "/api/v1/invoicing/invoices/{invoice_id}/payment-intents/{intent_id}/sync",
      { params: { path: { invoice_id: event.params.id, intent_id } } },
    );
    if (error) return fail(400, { paymentError: apiErrorKey(error).key });
    return { paymentSynced: true };
  },
  deletePayment: async (event) => {
    const form = await event.request.formData();
    const payment_id = String(form.get("payment_id") ?? "");
    const { error } = await apiFor(event).DELETE(
      "/api/v1/invoicing/invoices/{invoice_id}/payments/{payment_id}",
      { params: { path: { invoice_id: event.params.id, payment_id } } },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { paymentDeleted: true };
  },
  delete: async (event) => {
    const { error } = await apiFor(event).DELETE("/api/v1/invoicing/invoices/{invoice_id}", {
      params: { path: { invoice_id: event.params.id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    // Back where the detour started (#408); the register only when nothing said otherwise. This
    // is the case the browser-only breadcrumb trail can never serve — a server-side redirect has
    // no `sessionStorage` to read, which is why the origin travels in the URL.
    throw redirect(303, originOf(event.url) ?? "/invoices");
  },
};
