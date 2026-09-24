/**
 * The form actions behind a one-time product sale, wherever its panel is mounted.
 *
 * A sale is recorded from the record it belongs to — the client hub or the project page —
 * through `SaleDialog`, and acted on from the same panel (`SalesPanel`): invoiced, edited,
 * removed. SvelteKit actions live on the page, so each host spreads `saleActions` into its own
 * `actions` (the `subscriptionActions` shape).
 *
 * **Host contract:** the dialog posts to `?/createSale` / `?/updateSale`, its product picker's
 * "＋ … toevoegen" to `?/createSaleProduct`, and the panel's row menu to `?/invoiceSale` and
 * `?/deleteSale`.
 */
import { fail, redirect, type RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { apiFor } from "$lib/core/session";

function text(form: FormData, name: string): string {
  return String(form.get(name) ?? "").trim();
}

/** The fields create and update share. Empty strings are `null` — the API's blank rule. */
function saleBody(form: FormData) {
  const unitPrice = text(form, "unit_price");
  return {
    project_id: text(form, "project_id") || null,
    name: text(form, "name") || null,
    description: text(form, "description") || null,
    quantity: text(form, "quantity") || "1",
    unit: text(form, "unit") || null,
    unit_price: unitPrice === "" ? null : unitPrice,
    tax_rate_id: text(form, "tax_rate_id") || null,
    sold_on: text(form, "sold_on") || null,
    notes: text(form, "notes") || null,
  };
}

export async function createSale(event: RequestEvent) {
  const form = await event.request.formData();
  const company_id = text(form, "company_id");
  const product_id = text(form, "product_id") || null;
  const body = saleBody(form);
  if (!company_id || (!product_id && !body.name)) {
    return fail(400, { saleError: "errors.required" });
  }
  const { error } = await apiFor(event).POST("/api/v1/invoicing/sales", {
    body: { ...body, company_id, product_id } as never,
  });
  if (error) {
    const e = apiErrorKey(error);
    return fail(400, { saleError: e.key, saleFields: e.fields });
  }
  return { saleSaved: true };
}

export async function updateSale(event: RequestEvent) {
  const form = await event.request.formData();
  const sale_id = text(form, "id");
  if (!sale_id) return fail(400, { saleError: "errors.required" });
  const body = saleBody(form);
  // A sale keeps its name: an emptied box is "leave alone", never "clear" (§18).
  const { error } = await apiFor(event).PATCH("/api/v1/invoicing/sales/{sale_id}", {
    params: { path: { sale_id } },
    body: {
      ...body,
      name: body.name ?? undefined,
      unit_price: body.unit_price ?? undefined,
    } as never,
  });
  if (error) {
    const e = apiErrorKey(error);
    return fail(400, { saleError: e.key, saleFields: e.fields });
  }
  return { saleSaved: true };
}

export async function deleteSale(event: RequestEvent) {
  const form = await event.request.formData();
  const sale_id = text(form, "id");
  if (!sale_id) return fail(400, { saleError: "errors.required" });
  const { error } = await apiFor(event).DELETE("/api/v1/invoicing/sales/{sale_id}", {
    params: { path: { sale_id } },
  });
  if (error) return fail(400, { saleError: apiErrorKey(error).key });
  return { saleDeleted: true };
}

/** Draft one invoice for this sale and land on it — the draft is where the work continues. */
export async function invoiceSale(event: RequestEvent) {
  const form = await event.request.formData();
  const sale_id = text(form, "id");
  if (!sale_id) return fail(400, { saleError: "errors.required" });
  const { data, error } = await apiFor(event).POST("/api/v1/invoicing/sales/{sale_id}/invoice", {
    params: { path: { sale_id } },
  });
  if (error || !data) return fail(400, { saleError: apiErrorKey(error).key });
  throw redirect(303, `/invoices/${data.id}`);
}

/** Inline product create from the sale dialog's picker (docs/UX.md — every entity-reference
 *  picker offers inline-create). The price list minus what a sale does not need; the row can
 *  be completed in Instellingen → Facturatie. Returns `inlineCreated` so the form auto-selects it. */
export async function createSaleProduct(event: RequestEvent) {
  const form = await event.request.formData();
  const name = text(form, "name");
  if (!name) return fail(400, { qcError: "errors.required" });
  const { data, error } = await apiFor(event).POST("/api/v1/invoicing/products", {
    body: {
      name,
      unit_price: text(form, "unit_price") || "0",
      unit: text(form, "unit") || null,
      active: true,
      position: 0,
    } as never,
  });
  if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
  return { inlineCreated: { slot: "product", id: data.id, name: data.name } };
}

/** What a host page spreads to mount `SalesPanel` + `SaleDialog`. */
export const saleActions = {
  createSale,
  updateSale,
  deleteSale,
  invoiceSale,
  createSaleProduct,
};
