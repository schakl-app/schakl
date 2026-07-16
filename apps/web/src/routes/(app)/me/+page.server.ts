import { error, fail, redirect, type RequestEvent } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";
import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

// The personal page (employee dossier): my leave, my contract, my documents — and, for a
// dossier manager, any employee's via ?user=. Reached from the profile menu, not the nav:
// it is about *you*, not a module surface.
export const load: PageServerLoad = async (event) => {
  const me = event.locals.user;
  if (!me || !(event.locals.theme?.enabledModules ?? []).includes("hr")) {
    throw redirect(303, "/");
  }
  if (!can(me, "hr.dossier.read")) throw redirect(303, "/");
  const canAny = can(me, "hr.dossier.read", "any");
  const canManage = can(me, "hr.document.manage");
  const requested = event.url.searchParams.get("user");
  const userId = canAny && requested ? requested : me.id;
  const self = userId === me.id;

  const api = apiFor(event);
  const leaveEnabled = (event.locals.theme?.enabledModules ?? []).includes("leave");
  const [dossier, balance, contracts, types, members] = await Promise.all([
    api.GET("/api/v1/hr/dossier", { params: { query: { user_id: userId } } }),
    leaveEnabled
      ? api.GET("/api/v1/leave/balance", {
          params: { query: { user_id: userId, year: new Date().getFullYear() } },
        })
      : Promise.resolve({ data: null }),
    leaveEnabled
      ? api.GET("/api/v1/leave/contracts", { params: { query: { user_id: userId } } })
      : Promise.resolve({ data: null }),
    leaveEnabled ? api.GET("/api/v1/leave/types") : Promise.resolve({ data: null }),
    // The employee picker, only for someone who may read any dossier.
    canAny ? api.GET("/api/v1/members/lookup") : Promise.resolve({ data: null }),
  ]);
  if (!dossier.data) throw error(404, { code: "not_found", message: "errors.not_found" });

  return {
    dossier: dossier.data,
    balance: balance.data ?? null,
    contracts: contracts.data ?? [],
    leaveTypes: types.data ?? [],
    members: members.data ?? [],
    viewedUserId: userId,
    self,
    canAny,
    canManage,
    locale: event.locals.locale,
  };
};

async function uploadDossierFile(
  event: RequestEvent,
  upload: File,
  query: string,
): Promise<{ ok: true } | { error: string }> {
  const body = new FormData();
  body.append("file", upload, upload.name);
  const res = await event.fetch(`${apiBaseUrl()}/api/v1/hr/documents?${query}`, {
    method: "POST",
    headers: {
      cookie: event.request.headers.get("cookie") ?? "",
      "x-forwarded-host": event.request.headers.get("host") ?? "",
    },
    body,
  });
  if (!res.ok) {
    return { error: res.status === 413 ? "errors.upload_too_large" : "errors.upload_type" };
  }
  return { ok: true };
}

export const actions: Actions = {
  upload: async (event) => {
    const form = await event.request.formData();
    const file = form.get("file");
    const user_id = String(form.get("user_id") ?? "").trim();
    const category = String(form.get("category") ?? "").trim();
    const title = String(form.get("title") ?? "").trim();
    if (!(file instanceof File) || file.size === 0 || !user_id || !category) {
      return fail(400, { error: "errors.required" });
    }
    const query = new URLSearchParams({ user_id, category, title }).toString();
    const uploaded = await uploadDossierFile(event, file, query);
    if ("error" in uploaded) return fail(400, { error: uploaded.error });
    return { saved: true };
  },

  delete: async (event) => {
    const form = await event.request.formData();
    const document_id = String(form.get("id") ?? "");
    if (document_id) {
      const { error: err } = await apiFor(event).DELETE("/api/v1/hr/documents/{document_id}", {
        params: { path: { document_id } },
      });
      if (err) return fail(400, { error: apiErrorKey(err).key });
    }
    return { deleted: true };
  },
};
