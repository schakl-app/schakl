/** Shared parsing for the sidebar-layout editor's posted payload (#169). */

/** Parse the editor's JSON `items` field into the API shape; junk becomes an empty list. */
export function parseNavItems(raw: FormDataEntryValue | null): { key: string; hidden: boolean }[] {
  try {
    const parsed = JSON.parse(String(raw ?? "[]"));
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => item && typeof item.key === "string" && item.key)
      .map((item) => ({ key: String(item.key), hidden: Boolean(item.hidden) }));
  } catch {
    return [];
  }
}
