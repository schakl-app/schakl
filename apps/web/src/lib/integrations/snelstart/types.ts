/**
 * The shapes the SnelStart settings screen reads (epic #377, issue #31).
 *
 * Re-exported from the generated client rather than hand-written, so a schema change breaks the
 * build rather than the page (CLAUDE.md §3). They live in **one** file rather than inline in the
 * screen for the same reason: a shape scattered across a page and a load is a shape that drifts.
 */

import type { components } from "$lib/core/api/schema";

/** One connected administration, as the settings screen sees it. Never a credential. */
export type SnelstartAccount = components["schemas"]["SnelstartAccountRead"];
/** The outcome of testing a connection. Never an error status: a refusal is `ok: false`. */
export type SnelstartVerify = components["schemas"]["SnelstartVerifyResult"];
/** One revenue account an invoice line may book to. */
export type SnelstartLedger = components["schemas"]["SnelstartLedgerOption"];
/** What one sync did, and what it could not do (#31: failures are visible). */
export type SnelstartRun = components["schemas"]["SnelstartSyncRunRead"];
/** One SnelStart relation and what schakl thinks it is, before anybody agrees. */
export type SnelstartCandidate = components["schemas"]["SnelstartRelationCandidate"];

/** One row of a run's failure list. The API types `errors` as free-shaped JSON — a run's
 *  per-row failures differ by kind — so this names the shape the screen actually renders. */
export interface SnelstartRunError {
  local_id?: string;
  name?: string;
  key?: string;
  message?: string;
}


/**
 * How confident a proposed pairing is, which is the only thing that decides what a reviewer has
 * to actually read.
 *
 * A Chamber of Commerce or VAT number **is** the company; a client number is the agency's own
 * bookkeeping and equally hard; an e-mail address is usually right and occasionally a shared
 * `info@`; a name match is a guess and a null match is a relation nothing in schakl answers to.
 * The last two are the whole reason this screen exists rather than a silent merge.
 */
export type MatchConfidence = "linked" | "certain" | "likely" | "guess";

export function matchConfidence(candidate: SnelstartCandidate): MatchConfidence {
  if (candidate.linked) return "linked";
  switch (candidate.match_on) {
    case "linked":
      return "linked";
    case "coc":
    case "vat":
    case "client_number":
      return "certain";
    case "email":
      return "likely";
    default:
      // `name`, and `null` for "nothing matched". Both need a human, which is what `guess` means
      // here — an unmatched relation is not a lower-confidence match, it is the same decision
      // with an empty proposal, and splitting them into two tables would hide half the work.
      return "guess";
  }
}

/** The confidence groups a review table renders, worst-first: the leftovers are the work. */
export const MATCH_GROUPS: MatchConfidence[] = ["guess", "likely", "certain", "linked"];

/** Count keys a run reports that are a *list*, not a number, and so are rendered on their own. */
export const LIST_COUNTS = new Set(["guessed_rates"]);

/**
 * What each half of the integration needs the token to be allowed to do — a **mirror** of
 * `REQUIRED_SCOPES` in `app/integrations/snelstart/service.py`, and the API stays the authority.
 *
 * The copy earns itself by outliving the verify. `SnelstartVerifyResult.missing_scopes` is the
 * real answer and is preferred whenever one has just run, but it is a form result: it is gone on
 * the next navigation, while the reason it existed — a key that cannot write invoices — is still
 * true tomorrow. `scopes` is stored on the row, so the same sentence can be said on every page
 * load, and a warning nobody can make reappear is a warning nobody acts on.
 *
 * Five entries, and they change only when SnelStart changes its scope names — at which point the
 * screen saying the wrong thing is the loud failure, not a silent one.
 */
export const REQUIRED_SCOPES: Record<string, readonly string[]> = {
  relations: ["relaties:read", "relaties:write"],
  invoices: ["boekhouden:read", "boekhouden:write"],
  articles: ["artikelen:read", "artikelen:write"],
  attachments: ["documenten:write"],
  settings: ["settings:read"],
};

/**
 * Which halves this token cannot deliver. Empty is the happy answer — and so is an **unverified**
 * row, deliberately: with no scopes observed yet, "nothing is missing" and "we have not looked"
 * are the same empty list, and shouting about five missing capabilities on a key nobody has
 * tested yet is a screen crying wolf on its own first render.
 */
export function missingScopes(scopes: string[] | null | undefined): string[] {
  if (!scopes || scopes.length === 0) return [];
  const held = new Set(scopes);
  return Object.entries(REQUIRED_SCOPES)
    .filter(([, needed]) => !needed.every((scope) => held.has(scope)))
    .map(([name]) => name);
}
