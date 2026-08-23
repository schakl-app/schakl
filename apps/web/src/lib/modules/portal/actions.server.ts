/**
 * Client-portal form actions (issues #193, #296), contributed to whatever page hosts a portal
 * subject — the `interactionActions` pattern.
 *
 * They live in the module rather than on the contact route because the portal is the module
 * that owns them: a second kind of subject spreads them onto its own page by importing this,
 * not by copying four handlers. The subject type is a parameter for the same reason; only the
 * caller knows what kind of row its page is about.
 *
 * The API stays the boundary throughout — every one of these is a thin pass-through, and the
 * licence gate (402) and permission gate (403) both surface as an ordinary field error.
 */
import { fail, redirect } from "@sveltejs/kit";
import type { Actions, RequestEvent } from "@sveltejs/kit";

import { apiErrorKey } from "$lib/core/errors";
import { IMPERSONATION_COOKIE, PORTAL_RETURN_COOKIE } from "$lib/core/impersonation";
import { apiFor } from "$lib/core/session";

/** How long a "sign in as this client" session lasts. The API clamps it to its own maximum. */
const PORTAL_IMPERSONATION_MINUTES = 30;

/** Which login an action is about, and where stopping an impersonation of it should land. */
export interface PortalSubjectRef {
  /** The registered subject type — `contact` today. */
  entityType: string;
  subjectId: string;
  /** Where "Stoppen" should land the staff member when the impersonation ends. */
  returnPath: string;
}

export interface PortalActionOptions {
  /**
   * The subject this submit is about. One resolver rather than three fields, and it may be
   * async, because the two hosts learn it two different ways: a record page reads its own route
   * params, while the register (#406) lists many logins and reads the pressed row out of the
   * form body — and a body may be read **once**, so resolving it three times over would throw
   * on the second.
   */
  subject: (event: RequestEvent) => PortalSubjectRef | Promise<PortalSubjectRef>;
}

export function portalActions(options: PortalActionOptions): Actions {
  const path = (ref: PortalSubjectRef) => ({
    entity_type: ref.entityType,
    subject_id: ref.subjectId,
  });

  return {
    portalEnable: async (event) => {
      const ref = await options.subject(event);
      const { data, error: err } = await apiFor(event).POST(
        "/api/v1/portal/logins/{entity_type}/{subject_id}",
        { params: { path: path(ref) } },
      );
      if (err)
        return fail(400, { portalError: apiErrorKey(err).fields?.email ?? apiErrorKey(err).key });
      return { portalSaved: true, portalEmail: data?.invite_email_sent ?? null };
    },

    portalResend: async (event) => {
      const ref = await options.subject(event);
      const { data, error: err } = await apiFor(event).POST(
        "/api/v1/portal/logins/{entity_type}/{subject_id}/resend",
        { params: { path: path(ref) } },
      );
      if (err) return fail(400, { portalError: apiErrorKey(err).key });
      return { portalSaved: true, portalEmail: data?.invite_email_sent ?? null };
    },

    portalDisable: async (event) => {
      const ref = await options.subject(event);
      const { error: err } = await apiFor(event).DELETE(
        "/api/v1/portal/logins/{entity_type}/{subject_id}",
        { params: { path: path(ref) } },
      );
      if (err) return fail(400, { portalError: apiErrorKey(err).key });
      return { portalSaved: true };
    },

    // Sign in as this client (#296). The grant cookie sits *beside* the staff session — the API
    // swaps only the effective user, so stopping puts the staff member straight back. The return
    // path is stored with it so "Stoppen" lands on this record instead of the dashboard.
    portalImpersonate: async (event) => {
      const ref = await options.subject(event);
      const { data, error: err } = await apiFor(event).POST(
        "/api/v1/portal/logins/{entity_type}/{subject_id}/impersonate",
        { params: { path: path(ref) }, body: { minutes: PORTAL_IMPERSONATION_MINUTES } },
      );
      if (err || !data) {
        return fail(400, { portalError: err ? apiErrorKey(err).key : "errors.server" });
      }
      // The API clamps the window; mirror what it actually granted rather than what we asked for.
      const maxAge = Math.max(
        1,
        Math.floor((new Date(data.expires_at).getTime() - Date.now()) / 1000),
      );
      const cookieOptions = {
        path: "/",
        httpOnly: true,
        sameSite: "lax",
        secure: event.url.protocol === "https:",
        maxAge,
      } as const;
      event.cookies.set(IMPERSONATION_COOKIE, data.token, cookieOptions);
      event.cookies.set(PORTAL_RETURN_COOKIE, ref.returnPath, cookieOptions);
      // Home, because that is the portal's own landing page — the point is to see what they see.
      throw redirect(303, "/");
    },
  };
}
