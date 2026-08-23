import type { components } from "$lib/core/api/schema";

export type PortalLoginState = components["schemas"]["PortalLoginState"];
export type PortalLoginRow = components["schemas"]["PortalLoginRow"];

/** Everything `PortalCard` needs, resolved server-side by `loadPortalCard`. */
export interface PortalCardData {
  entityType: string;
  subjectId: string;
  /**
   * The workspace does not run the portal, or is past its licence window: the card renders with
   * a locked invite control and the upgrade dialog behind it, rather than not at all. Distinct
   * from "no permission", which hides the card entirely — a lock is only ever shown for
   * something the org itself can change.
   */
  locked: boolean;
  /** Null while locked: a card with nothing to manage costs no API call. */
  state: PortalLoginState | null;
  canImpersonate: boolean;
  /** Instance posture; decides what "upgrade" means in the dialog. */
  deployment: string;
  isInstanceOwner: boolean;
}

/** Everything the Klantlogins register needs, resolved server-side by `loadPortalLogins`. */
export interface PortalRegisterData {
  /**
   * The workspace runs the portal but may no longer write to it: the section still renders,
   * with the upgrade path where the actions would be. Unlike the card, a workspace that does
   * **not run** the module gets no section at all rather than a locked one — a card is an
   * affordance on a record somebody is already looking at, while a section is a whole area of a
   * screen, and one that exists only to say "not for you" is a worse screen than none (#137).
   */
  locked: boolean;
  /** Empty while locked: a section with nothing to manage costs no API call. */
  logins: PortalLoginRow[];
  canImpersonate: boolean;
  /** Instance posture; decides what "upgrade" means in the dialog. */
  deployment: string;
  isInstanceOwner: boolean;
}
