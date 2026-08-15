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
import type { Component } from "svelte";

import type { paths } from "$lib/core/api/schema";

/**
 * Entity slugs with a bulk route — read off the generated client, never re-typed.
 *
 * The two are not the same set, and saying so in the types is what makes a mistake a compile
 * error: an invoice and a contact moment can be deleted in bulk but have no fields a selection
 * could share, so they mount `/delete` and no `/update`.
 */
type UpdateEntityOf<T> = T extends `/api/v1/bulk/${infer E}/update` ? E : never;
type DeleteEntityOf<T> = T extends `/api/v1/bulk/${infer E}/delete` ? E : never;
export type BulkUpdateEntity = UpdateEntityOf<keyof paths>;
export type BulkDeleteEntity = DeleteEntityOf<keyof paths>;

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

/** One module-specific bulk action, contributed by the list (the interacties review trio). */
export interface BulkAction {
  label: string;
  icon?: Component;
  /** What the action does. Omit only when it is a `href` navigation instead. */
  onclick?: () => void;
  /**
   * Renders the action as a real `<a>` rather than a button, for the one kind of bulk action
   * that is a **navigation and not a mutation**: a download (the invoice zip, #307). It then
   * behaves like every other link here — middle-click, right-click → save as — instead of
   * being a click handler that happens to set `location`. A disabled action falls back to the
   * button, because there is no such thing as a disabled anchor.
   */
  href?: string;
  danger?: boolean;
  /**
   * How many of the selected rows this action can actually do. Rendered beside the label
   * whenever it is fewer than the selection, and disables the button at zero — a control that
   * silently did less than it said is the failure this prevents (docs/UX.md, #299).
   */
  eligible?: number;
  /**
   * Disables the action and says why, for a limit the selection can exceed rather than a
   * subset it can miss. `eligible` cannot express this: "Download (50)" over 120 picked rows
   * would state a number and still leave *which* fifty to chance.
   */
  disabledReason?: string;
}

/**
 * One list's whole bulk configuration.
 *
 * `BulkToggle` and `BulkBar` render in different places — the switch belongs with the list's
 * controls, the actions belong with the rows — but they answer to the same settings, so they
 * take the same props and a page spreads one object into both. Each ignores what it does not
 * need, which is what keeps the two from drifting into two half-configurations.
 */
export interface BulkConfig {
  /** The fields this entity can be bulk-edited on; empty means no Bewerken. */
  fields?: BulkFieldDef[];
  /** The entity's own write key. Omit together with `fields` for a delete-only list. */
  writePermission?: string;
  /** The entity's own delete key. Omit for a list with no bulk delete. */
  deletePermission?: string;
  /** The confirmation copy — entity-specific, because "12 clients" is not "12 rows". */
  deleteMessage?: string;
  /**
   * How many of the selected rows a delete would actually remove. Same contract as
   * `BulkAction.eligible`, and it exists because the generic delete button had no way to say
   * it. On `interactions` a page of Gmail email is refused per row by the service, so the bar
   * offered an ordinary "Verwijderen" over fifty rows and the banner then answered "0
   * verwijderd · 50 overgeslagen". The three review actions beside it had carried a count all
   * along — "a subset of none has to say so out loud" (#299) is not the review trio's rule, it
   * is the bar's, and the one button core owns itself was the one that could not obey it.
   *
   * Omit where every selectable row is deletable; the button then reads exactly as it did.
   */
  deleteEligible?: number;
  /** Why a delete can do nothing with this selection. Paired with `deleteEligible: 0`. */
  deleteDisabledReason?: string;
  /** Module-specific actions, shown before the generic pair. */
  items?: BulkAction[];
  updateAction?: string;
  deleteAction?: string;
  /** Per-field message keys from a rejected save (the API's 422 `fields`). */
  fieldErrors?: Record<string, string> | null;
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
