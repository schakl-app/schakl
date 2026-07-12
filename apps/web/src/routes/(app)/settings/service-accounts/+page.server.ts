import { fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

export const load: PageServerLoad = async (event) => {
  if (!can(event.locals.user, "apikeys.service_account.manage")) throw redirect(303, "/settings");
  const api = apiFor(event);
  const [{ data: accounts }, { data: catalog }] = await Promise.all([
    api.GET("/api/v1/service-accounts"),
    api.GET("/api/v1/permissions/catalog"),
  ]);

  // One parallel fan-out for the keys — the list is small (a handful of automations), and the
  // page shows every account's keys, so there is nothing to defer (docs/PERFORMANCE.md).
  const keysByAccount = Object.fromEntries(
    await Promise.all(
      (accounts ?? []).map(async (account) => {
        const { data } = await api.GET("/api/v1/service-accounts/{account_id}/keys", {
          params: { path: { account_id: account.id } },
        });
        return [account.id, data ?? []] as const;
      }),
    ),
  );

  // A key's scopes are capped by the *creator's* grants (#20), so offer exactly what this
  // admin holds — the same rule the personal-keys form applies.
  const scopeOptions: { value: string; label_key: string }[] = [];
  for (const perm of catalog?.permissions ?? []) {
    const variants =
      perm.scopes.length > 0 ? perm.scopes.map((s) => `${perm.key}:${s}`) : [perm.key];
    for (const value of variants) {
      const [base, suffix] = value.split(":");
      if (can(event.locals.user, base, suffix as "own" | "any" | undefined)) {
        scopeOptions.push({ value, label_key: perm.label_key });
      }
    }
  }

  return { accounts: accounts ?? [], keysByAccount, scopeOptions };
};

export const actions: Actions = {
  createAccount: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/service-accounts", {
      body: { name },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { accountCreated: true };
  },

  deleteAccount: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("account_id") ?? "");
    if (id) {
      const { error } = await apiFor(event).DELETE("/api/v1/service-accounts/{account_id}", {
        params: { path: { account_id: id } },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { accountDeleted: true };
  },

  createKey: async (event) => {
    const form = await event.request.formData();
    const accountId = String(form.get("account_id") ?? "");
    const name = String(form.get("name") ?? "").trim();
    const scopes = form.getAll("scopes").map(String).filter(Boolean);
    const expires = String(form.get("expires_at") ?? "").trim();
    if (!accountId || !name || scopes.length === 0 || !expires)
      return fail(400, { error: "errors.required", keyError: true });
    // A date input gives a day; store it as end-of-day UTC so "expires 2026-08-01" lasts that day.
    const expires_at = new Date(`${expires}T23:59:59Z`).toISOString();
    const { data, error } = await apiFor(event).POST(
      "/api/v1/service-accounts/{account_id}/keys",
      {
        params: { path: { account_id: accountId } },
        body: { name, scopes, expires_at },
      },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key, keyError: true });
    // The full secret is returned exactly once — hand it straight to the page to reveal.
    return { createdSecret: data?.secret, createdName: data?.name };
  },

  revokeKey: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("key_id") ?? "");
    if (id) {
      // The shared revoke endpoint; the service refines the permission for service-account keys.
      const { error } = await apiFor(event).POST("/api/v1/api-keys/{key_id}/revoke", {
        params: { path: { key_id: id } },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { revoked: true };
  },
};
