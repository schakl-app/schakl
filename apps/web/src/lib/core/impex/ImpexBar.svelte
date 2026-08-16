<script lang="ts">
  /**
   * The Export/Import pair as it appears on **every** list (issue #77, second round).
   *
   * One component, so the twelve screens that offer import/export offer the same two controls
   * in the same place with the same gates — the alternative, which the first round shipped,
   * was the pair hand-written on companies and contacts and missing everywhere else, so a
   * user with a spreadsheet of domains had to know it lived in Instellingen.
   *
   * Two gates on each control, mirroring the API exactly (§15, and the API is the boundary):
   * the **bulk** permission (`impex.export` / `impex.import`, admin-only by default — bulk is
   * not the same act as opening a record) *and* the entity's own read/write. Either missing
   * hides the control rather than rendering a button that only 403s.
   *
   * `filters` is whatever the list is currently filtered by; it rides along to the export
   * proxy so the file holds exactly the list on screen — the whole set, not the loaded page.
   * Values that are empty, `false` or `undefined` are dropped, so a caller passes its state
   * straight in without spelling out which of it happens to be set.
   */
  import { page } from "$app/state";
  import { Download, Upload } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

  import ImportWizard from "./ImportWizard.svelte";
  import type { ImpexColumns, ImpexEntity, ImportReport, InspectReport } from "./actions.server";

  let {
    entity,
    readPermission,
    writePermission,
    filters = {},
    locale = "nl",
    importable = true,
    form = null,
  }: {
    entity: ImpexEntity;
    /** The entity's own read gate — the same key the API's export route declares. */
    readPermission: string;
    /** The entity's own write gate; omit nothing — an export-only entity sets `importable`. */
    writePermission: string;
    filters?: Record<string, string | number | boolean | null | undefined>;
    locale?: string;
    /** False for an export-only entity (no import route is mounted for it). */
    importable?: boolean;
    /** The page's `form` prop; the wizard reads its own action's result off it. */
    form?: {
      impex?: ImportReport | null;
      impexInspect?: InspectReport | null;
      impexColumns?: ImpexColumns | null;
      impexError?: string | null;
    } | null;
  } = $props();

  let showImport = $state(false);

  const canExport = $derived(
    can(page.data.user, "impex.export") && can(page.data.user, readPermission),
  );
  const canImport = $derived(
    importable && can(page.data.user, "impex.import") && can(page.data.user, writePermission),
  );

  const href = $derived.by(() => {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value === undefined || value === null || value === false || value === "") continue;
      params.set(key, value === true ? "1" : String(value));
    }
    const query = params.toString();
    return `/impex/${entity}/export${query ? `?${query}` : ""}`;
  });
</script>

{#if canExport}
  <!-- A plain link: the browser downloads through its own session, and
       data-sveltekit-reload keeps the stream out of the client router. -->
  <a
    {href}
    data-sveltekit-reload
    data-sveltekit-preload-data="off"
    class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:text-text"
  >
    <Download class="h-4 w-4" />
    {t("impex.export")}
  </a>
{/if}
{#if canImport}
  <button
    type="button"
    class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text-muted hover:text-text"
    onclick={() => (showImport = true)}
  >
    <Upload class="h-4 w-4" />
    {t("impex.import")}
  </button>
  <ImportWizard
    bind:open={showImport}
    {entity}
    {locale}
    report={form?.impex ?? null}
    inspect={form?.impexInspect ?? null}
    columns={form?.impexColumns ?? null}
    error={form?.impexError ?? null}
  />
{/if}
