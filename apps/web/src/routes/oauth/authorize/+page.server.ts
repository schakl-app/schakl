import { error as httpError, fail, redirect } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { loginPath } from "$lib/core/redirect";
import { apiFor } from "$lib/core/session";

import type { Actions, PageServerLoad } from "./$types";

/**
 * The OAuth consent screen (docs/MCP.md) — the one step of the flow a person performs.
 *
 * It lives in the **web app, outside `(app)`**, and both halves of that are deliberate. Outside,
 * because the shell's nav, tenant chrome and layout loads are noise on a page whose whole job is
 * one question; and in the web app rather than the API, because the authorization endpoint is
 * advertised at `/oauth/authorize` on the root of the host, which the edge routes here
 * (CLAUDE.md §12).
 *
 * **This screen authenticates nobody.** It runs on whatever session the app already holds — a
 * local password with 2FA, or this org's OIDC federation (§3) — and an anonymous visitor is
 * turned away to `/login` carrying the whole authorization request, so signing in finishes the
 * journey instead of restarting it. Building a login here would have meant a second password
 * path, a second 2FA decision and a second answer to "which org is this session for": three
 * copies of things that are already right once.
 */
export const load: PageServerLoad = async (event) => {
  if (!event.locals.user) throw redirect(303, loginPath(event.url));

  const query = event.url.searchParams;
  const request = {
    client_id: query.get("client_id") ?? "",
    redirect_uri: query.get("redirect_uri") ?? "",
    scope: query.get("scope") ?? "",
    state: query.get("state") ?? "",
    code_challenge: query.get("code_challenge") ?? "",
    code_challenge_method: query.get("code_challenge_method") ?? "S256",
    resource: query.get("resource") ?? "",
  };

  // PKCE is checked here as well as at approval, so a client that forgot it is told on the
  // screen rather than after the person has already pressed Toestaan. OAuth 2.1 drops `plain`,
  // and accepting it would make the verifier a value anyone who saw this URL already holds.
  if (!request.code_challenge || request.code_challenge_method !== "S256") {
    throw httpError(400, "errors.oauth_pkce_required");
  }
  // The one response type this server mints (#441). Absent is tolerated — every real client
  // sends it and refusing the ones that predate this check buys nothing — but a stated
  // `response_type` that is not `code` is a request for a flow that does not exist here.
  const responseType = query.get("response_type");
  if (responseType !== null && responseType !== "code") {
    throw httpError(400, "errors.oauth_unsupported_response_type");
  }

  const { data, error } = await apiFor(event).GET("/api/v1/oauth/consent", {
    params: {
      query: {
        client_id: request.client_id,
        redirect_uri: request.redirect_uri,
        scope: request.scope,
        resource: request.resource || undefined,
      },
    },
  });
  // An unknown client or an unregistered redirect target is refused *on this page*, never by
  // redirecting to the URI in question — that is the open redirector the exact-match list exists
  // to prevent, and it would hand an attacker the `state` as well.
  if (error || !data) throw httpError(400, apiErrorKey(error).key);

  return { request, consent: data };
};

export const actions: Actions = {
  approve: async (event) => {
    const form = await event.request.formData();
    const scopes = form.getAll("scopes").map(String).filter(Boolean);
    if (scopes.length === 0) return fail(400, { error: "errors.oauth_scope_empty" });

    const { data, error } = await apiFor(event).POST("/api/v1/oauth/consent", {
      body: {
        client_id: String(form.get("client_id") ?? ""),
        redirect_uri: String(form.get("redirect_uri") ?? ""),
        code_challenge: String(form.get("code_challenge") ?? ""),
        code_challenge_method: String(form.get("code_challenge_method") ?? "S256"),
        scopes,
        resource: String(form.get("resource") ?? "") || null,
        state: String(form.get("state") ?? "") || null,
      },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    // Off to the client's own registered callback. The API built this URL from the row it
    // matched, never from the form — so the redirect cannot be steered from here.
    throw redirect(303, data.redirect_to);
  },

  deny: async (event) => {
    const form = await event.request.formData();
    const client_id = String(form.get("client_id") ?? "");
    const redirect_uri = String(form.get("redirect_uri") ?? "");
    const state = String(form.get("state") ?? "");

    // RFC 6749 §4.1.2.1: a refusal *does* travel back to the client — a client left waiting on
    // a window the user closed is the worse outcome. But the target is re-validated first, and
    // that is not belt-and-braces: this is a POST body, so the URI in it is whatever was
    // submitted, and redirecting to it unchecked would be an open redirector on a page reached
    // *through the login screen* — the exact shape `safeInternalPath` exists to refuse one door
    // over. The API answers only for a redirect URI registered on this client, by equality.
    const { error } = await apiFor(event).GET("/api/v1/oauth/consent", {
      params: { query: { client_id, redirect_uri } },
    });
    if (error) throw httpError(400, apiErrorKey(error).key);

    const url = new URL(redirect_uri);
    url.searchParams.set("error", "access_denied");
    if (state) url.searchParams.set("state", state);
    throw redirect(303, url.toString());
  },
};
