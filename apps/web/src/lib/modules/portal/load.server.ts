/**
 * What a page hosting a portal subject has to work out before it can draw the card (#137, #193).
 *
 * Three separate questions, deliberately not collapsed into one boolean, because they have
 * three different answers:
 *
 * - **May this user manage logins?** A permission (`members.member.write`). No → the card does
 *   not render at all; a lock they can never open would be a lie (docs/UX.md).
 * - **Does this workspace run the portal, and may it still write?** An entitlement. No → the
 *   card renders with a *locked* invite control, because this is something the agency can buy.
 * - **What is the login's current state?** One API call, and only worth making when the module
 *   is actually usable — a locked card has nothing to show and should cost nothing.
 *
 * That last line is the reason this is a function rather than three lines on the route: getting
 * the order wrong means an extra round-trip on every contact page of an instance that does not
 * even have the module (docs/PERFORMANCE.md).
 */
import { can } from "$lib/core/permissions";
import type { ApiClient } from "$lib/core/api/client";
import type { OrgTheme } from "$lib/core/theme";
import type { SessionUser } from "$lib/core/session";

import type { PortalCardData } from "./types";

const MANAGE = "members.member.write";
const IMPERSONATE = "portal.login.impersonate";

export async function loadPortalCard(
  api: ApiClient,
  {
    entityType,
    subjectId,
    user,
    theme,
  }: {
    entityType: string;
    subjectId: string;
    user: SessionUser | null;
    theme: OrgTheme | null | undefined;
  },
): Promise<PortalCardData | null> {
  if (!can(user, MANAGE)) return null;

  const enabled = theme?.enabledModules?.includes("portal") ?? false;
  // `entitledModules` lists what may still be *written*; a module past its grace window is
  // read-only, which for the portal means no new invites — exactly what the lock says.
  const entitled = theme?.entitledModules?.includes("portal") ?? false;
  const usable = enabled && entitled;

  const state = usable
    ? (
        await api.GET("/api/v1/portal/logins/{entity_type}/{subject_id}", {
          params: { path: { entity_type: entityType, subject_id: subjectId } },
        })
      ).data
    : null;

  return {
    entityType,
    subjectId,
    locked: !usable,
    state: state ?? null,
    canImpersonate: can(user, IMPERSONATE),
    deployment: theme?.deployment ?? "self_hosted",
    isInstanceOwner: user?.isInstanceOwner ?? false,
  };
}
