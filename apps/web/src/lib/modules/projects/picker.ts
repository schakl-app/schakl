/**
 * What a project picker offers, and what each row says about itself.
 *
 * Same failure as the client pickers (`modules/companies/picker.ts`) and the same fix: a project
 * that finished last spring was offered beside this week's work, spelled identically, so hours
 * got logged against it and interactions were filed under it. It is not removed — people do book
 * a forgotten hour on a project they have just closed, and a picker that cannot name it sends
 * them to another screen — it moves behind the search and says what it is.
 *
 * Where the client rule ends at the archive, a project has two finished states: `completed` is
 * the work being over and `archived` is the record being put away. Neither is something to
 * suggest. `on_hold` is not finished — the work is paused, it resumes, and a picker that hid it
 * would be hiding live work — so it stays on offer wearing its status.
 */

import {
  splitLifecycle,
  type LifecycleSplit,
  type LifecycleVocabulary,
  type StatusedOption,
} from "$lib/core/picker";
import { t } from "$lib/core/i18n";

/** The shape every project lookup already returns; extra fields are ignored. */
export interface PickerProject {
  id: string;
  name: string;
  status?: string | null;
  company_id?: string | null;
}

export const PROJECT_RETIRED_STATUSES = ["completed", "archived"] as const;

const QUIET = ["active"] as const;

export interface ProjectPickerOptions {
  /** The project(s) currently held by the field(s) — always offered, finished or not. */
  selectedId?: string | readonly (string | null | undefined)[];
  /**
   * Narrow to one client, the way every form's client→project cascade does.
   *
   * A project attached to no client belongs to no client in particular, so it stays offered
   * under every one of them — the rule the task picker already follows.
   */
  companyId?: string;
  /** An extra line per row (the client's name). The status is prefixed to it. */
  hint?: (project: PickerProject) => string | undefined;
}

export function splitProjectOptions(
  projects: readonly PickerProject[],
  { selectedId = [], companyId = "", hint }: ProjectPickerOptions = {},
): LifecycleSplit {
  const options: StatusedOption[] = projects
    .filter((project) => !companyId || !project.company_id || project.company_id === companyId)
    .map((project) => ({
      value: project.id,
      label: project.name,
      status: project.status ?? null,
      hint: hint?.(project),
    }));
  return splitLifecycle(options, { ...rules(), selectedId });
}

function rules() {
  return {
    retired: PROJECT_RETIRED_STATUSES,
    quiet: QUIET,
    statusLabel: (status: string) => t(`projects.status.${status}`),
  };
}

/**
 * The whole rule in one value, for a core picker that must not import this module. A function
 * rather than a constant because two of its fields are translations.
 */
export function projectLifecycle(): LifecycleVocabulary {
  return { ...rules(), archivedLabel: projectArchivedLabel() };
}

/** The heading `Combobox` draws above the search-only rows. */
export function projectArchivedLabel(): string {
  return t("projects.picker.archived");
}
