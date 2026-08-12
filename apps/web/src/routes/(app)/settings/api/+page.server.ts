import { error as httpError, fail } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { can } from "$lib/core/permissions";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * Instellingen → API en MCP: a personal key, and what to do with it.
 *
 * This was a card at the bottom of Mijn account — a name box, a date box and a scrolling list of
 * every permission the holder happens to carry — that handed back a secret and said nothing more.
 * The instance has shipped an MCP server since P4 (`docs/MCP.md`) and the product mentioned it
 * nowhere: the only occurrence of the word in the whole web app was a hidden search keyword. So
 * the flow ends where it used to begin — with the connection, spelled out for the client the user
 * actually has.
 *
 * Its own screen rather than a bigger card, for the reason Instellingen → Domein is its own screen
 * (#292): this is a staged flow with a one-shot reveal in the middle, not a field you fill in and
 * save. Mijn account keeps everything that is genuinely about the person.
 */
export const load: PageServerLoad = async (event) => {
  // Same key the API demands to mint one (#310: mirror the key the call actually makes), and the
  // same one the settings-nav entry hides the card on — so a deep link refuses here rather than
  // rendering a form whose only button 403s.
  if (!can(event.locals.user, "apikeys.personal.manage")) {
    throw httpError(403, "errors.forbidden");
  }

  const api = apiFor(event);
  // The catalog is already on the settings layout for exactly this screen's sake (#290) — it
  // lists `apikeys.personal.manage` among its consumers — so this load asks for two things.
  const parent = await event.parent();
  const [keys, modules, mcp, connections] = await Promise.all([
    api.GET("/api/v1/api-keys"),
    api.GET("/api/v1/meta/modules"),
    // The section catalog (docs/MCP.md). Its own endpoint rather than four more fields on
    // `/meta/modules`, which the app layout loads on every navigation — thirty section rows
    // there would be paid for by every page in the product to serve this one screen.
    api.GET("/api/v1/meta/mcp"),
    api.GET("/api/v1/oauth/connections"),
  ]);

  // A key can never grant more than its owner holds, so the offerable scopes are the catalog
  // narrowed to this member — the API enforces the same cap on mint.
  const scopeOptions: { value: string; label_key: string; read: boolean }[] = [];
  for (const perm of parent.permissionCatalog?.permissions ?? []) {
    const variants =
      perm.scopes.length > 0 ? perm.scopes.map((s) => `${perm.key}:${s}`) : [perm.key];
    for (const value of variants) {
      const [base, suffix] = value.split(":");
      if (can(event.locals.user, base, suffix as "own" | "any" | undefined)) {
        // What the "read-only" preset means, decided once here rather than in the browser: the
        // permission catalog's own naming convention is `<module>.<resource>.<action>`
        // (CLAUDE.md §15), so the action segment is the whole question.
        scopeOptions.push({ value, label_key: perm.label_key, read: base.endsWith(".read") });
      }
    }
  }

  return {
    apiKeys: keys.data ?? [],
    scopeOptions,
    // The host the user is on *is* the host their client must point at: this app and the API
    // share it, the edge routing `/api/` and `/mcp` to the API service (docs/MCP.md). Deriving
    // it from `base_domain` would get a custom-domain tenant wrong.
    origin: event.url.origin,
    // Whether the surface exists and answers (#253). Without these the page would print a
    // `claude mcp add` line that fails in a terminal with no screen having warned anyone.
    mcpEnabled: modules.data?.mcp_enabled ?? false,
    mcpEntitled: modules.data?.mcp_entitled ?? false,
    // What the whole surface costs, and what each section costs instead. The number is the
    // argument: `/mcp` is 623 tools and a chat client's ceiling is ~5,000 tokens for all of
    // them together, so "pick a section" is not a preference — it is the difference between a
    // connector that adds and one that does not.
    mcpTotalTools: mcp.data?.total_tools ?? 0,
    mcpSections: mcp.data?.sections ?? [],
    // Clients this user has connected over OAuth. Listed beside the keys because they are the
    // same thing: an OAuth session *is* an api_keys row, so revoking either is one act.
    connections: connections.data ?? [],
  };
};

export const actions: Actions = {
  createKey: async (event) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    const scopes = form.getAll("scopes").map(String).filter(Boolean);
    const expires = String(form.get("expires_at") ?? "").trim();
    // Which client the guide should show next. Presentation only — it never reaches the API,
    // and it is echoed back rather than kept in a cookie so a second tab cannot steal it.
    const target = String(form.get("target") ?? "mcp");
    if (!name || scopes.length === 0) return fail(400, { error: "errors.required", target });
    // A date input gives a day; store it as end-of-day UTC so "expires 2026-08-01" lasts that
    // day. Left empty, the key never expires (an explicit choice; revoke stays the kill switch).
    const expires_at = expires ? new Date(`${expires}T23:59:59Z`).toISOString() : null;
    const { data, error } = await apiFor(event).POST("/api/v1/api-keys", {
      body: { name, scopes, expires_at },
    });
    if (error) {
      // Prefer the field message over the envelope's. The API caps a key's life at a year and
      // says so per field (`errors.apikey_expiry_too_far`); collapsing that to the generic
      // "some fields are invalid" leaves a guided flow answering a fixable mistake with a
      // sentence that names neither the field nor the rule.
      const e = apiErrorKey(error);
      return fail(400, { error: e.fields?.expires_at ?? e.fields?.scopes ?? e.key, target });
    }
    // The full secret is returned exactly once — hand it straight to the page to reveal.
    return { createdSecret: data?.secret, createdName: data?.name, target };
  },

  disconnect: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("client_pk") ?? "");
    if (id) {
      // Revoking the *client*, not this user's keys: a connector that has been disconnected
      // must not be able to refresh its way back in, and a refresh presented against a revoked
      // client is refused before any key is looked at.
      const { error } = await apiFor(event).DELETE("/api/v1/oauth/connections/{client_pk}", {
        params: { path: { client_pk: id } },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { disconnected: true };
  },

  revokeKey: async (event) => {
    const form = await event.request.formData();
    const id = String(form.get("key_id") ?? "");
    if (id) {
      const { error } = await apiFor(event).POST("/api/v1/api-keys/{key_id}/revoke", {
        params: { path: { key_id: id } },
      });
      if (error) return fail(400, { error: apiErrorKey(error).key });
    }
    return { revoked: true };
  },
};
