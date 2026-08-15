/**
 * What a record nobody named is called, on screen (#350).
 *
 * Create-then-edit (#230) creates the row first, with a placeholder title, and lands the user on
 * its detail page in edit mode. When they never finish — close the tab, hit back, get
 * interrupted — the row survives, and the placeholder *is* an ordinary title, so it reads as
 * real work. Worse, it was written in the **creator's** locale, so one org held both "Naamloze
 * taak" and "Untitled task", alphabetised into two clumps that no search could gather.
 *
 * The API now marks the row (`unnamed`) instead of relying on what its title says, which is what
 * lets the screen do the two things a stored placeholder never could: name it in the *reader's*
 * language, and mark it as unfinished rather than passing it off as named.
 *
 * The stored title stays the fallback, so a surface that has not been taught about the flag
 * still prints something sensible — this is a display improvement, never a load-bearing one.
 */
import { t } from "$lib/core/i18n";

export function displayName(
  record: { unnamed?: boolean },
  stored: string,
  key: string,
): string {
  return record.unnamed ? t(key) : stored;
}

/** A task's title as the reader should see it. */
export function taskTitle(task: { unnamed?: boolean; title: string }): string {
  return displayName(task, task.title, "tasks.untitled");
}

/** A project's name as the reader should see it. */
export function projectName(project: { unnamed?: boolean; name: string }): string {
  return displayName(project, project.name, "projects.untitled");
}

/** The muted, italic treatment that marks an unnamed row as unfinished rather than named. */
export const UNNAMED_CLASS = "italic text-text-muted";
