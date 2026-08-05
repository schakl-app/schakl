import type { components } from "$lib/core/api/schema";

export type PortalLoginState = components["schemas"]["PortalLoginState"];

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
