/**
 * What a list tells the shared bulk dialog it can change (the API's `BulkDescriptor`, mirrored).
 *
 * The definitions live in web code rather than being fetched, for the same reason `columns.ts`
 * does: the API is the authority and re-checks every value, so this is a *rendering* contract,
 * not a security one — and a `/fields` round trip on every list load would buy nothing but a
 * request (docs/PERFORMANCE.md). What it does buy is the option lists: a client picker needs the
 * clients the page already loaded for its own filter, which no generic endpoint could hand back
 * without shipping the whole tenant.
 *
 * `key` must match the entity's `editable` entry in `apps/api/app/modules/<m>/bulk.py`; the API
 * answers 422 `impex.errors.unknown_column` if it drifts, which is the drift showing up as a
 * failed save rather than a silent no-op.
 */
import type { paths } from "$lib/core/api/schema";

/** Entity slugs with a bulk update route — read off the generated client, never re-typed. */
type UpdateEntityOf<T> = T extends `/api/v1/bulk/${infer E}/update` ? E : never;
export type BulkEntity = UpdateEntityOf<keyof paths>;

export interface BulkOption {
  value: string;
  label: string;
  hint?: string;
}

export interface BulkFieldDef {
  /** The API column key — the same stable key the entity's CSV export uses. */
  key: string;
  /** Already translated: some of these are tenant data (a subscription category). */
  label: string;
  /**
   * `select` and `fk` both render the house type-ahead (docs/UX.md — a native `<select>` is
   * never the answer, #256); they differ only in where the options come from, which matters
   * to the caller and not to the control.
   *
   * There is no `party` here even though the engine resolves party tokens: a party control
   * always holds a type, so it has no "leave this one alone" state, and every control in this
   * dialog must have one (see `BulkEditDialog`).
   */
  type: "select" | "fk" | "bool" | "date" | "text" | "number";
  /** `select` / `fk` only. */
  options?: BulkOption[];
  /**
   * Whether emptying this field is a thing the user can ask for. Off by default: over a
   * selection that disagrees with itself, "I left it blank" means *leave them alone*, so
   * clearing has to be a separate, deliberate control (see `BulkEditDialog`).
   */
  clearable?: boolean;
  /**
   * What clearing this field actually means, when "empty" is not the whole story — a domain
   * with no `invoiceable` decision follows its register (#298), it is not "not invoiced".
   */
  clearLabel?: string;
  /** Placeholder for the free-text / number / date control. */
  placeholder?: string;
}

/**
 * What a batch actually did — the shape every bulk form action answers with, so one banner
 * renders every outcome.
 *
 * `kind` is a plain string because it names a *verb*, and a module with review actions of its
 * own has its own ("approve", "reject"). Which verbs are legal follows from the namespace the
 * banner is pointed at (`BulkResult`'s `prefix`), not from this type; the shared actions below
 * narrow it to their own two.
 */
export interface BulkOutcome {
  kind: string;
  succeeded: number;
  failed: number;
  /** The distinct i18n keys the API gave for the rows it skipped. */
  reasons: string[];
}
