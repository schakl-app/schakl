<script lang="ts">
  /**
   * Which sections one client's report carries (issue #373).
   *
   * Sections were toggled per **template**, which is org-wide, so two clients sharing the house
   * template could not differ. In practice they always do — a client with no social presence got
   * a social section every month, a client who buys no ads got a paid-traffic paragraph — and
   * the only escape was authoring a second template for them, which then has to be kept in step
   * with the first one for ever.
   *
   * Three states per row, not two, and that is the whole design. A checkbox can say *on* and
   * *off*; it cannot say **"whatever the template says"**, which is what almost every row means
   * and what must keep meaning that when the template changes. So each row is a three-way select
   * whose inherit option *names the answer it inherits* — "Volg sjabloon (aan)" — because a
   * default nobody can see is a default nobody trusts.
   *
   * Each row also says **what feeds it**, and whether this client has it. Choosing what goes in
   * a document is a decision about sources: an agency switching a section off wants to know
   * whether it is empty because the client has no social traffic or because nobody ever linked
   * the property, and a list of nine names answers neither.
   */
  import { Check, Minus } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";

  import type { SectionCatalogEntry } from "./types";

  let {
    sections,
    /** The client's own diff: `{key: boolean}`. Absent key = inherit. Bound, so the hidden
        field below re-serialises as boxes are ticked. */
    overrides = $bindable({} as Record<string, boolean>),
    /** What the template says for each key, so "volg sjabloon" can name its own answer. */
    templateDefaults = {} as Record<string, boolean>,
    /** The keys that will actually print, straight from the API — never re-derived here. */
    effective = [] as string[],
    /** Sources this client has an active link to, for the "aangesloten" hint. */
    linkedSources = [] as string[],
    disabled = false,
  }: {
    sections: SectionCatalogEntry[];
    overrides?: Record<string, boolean>;
    templateDefaults?: Record<string, boolean>;
    effective?: string[];
    linkedSources?: string[];
    disabled?: boolean;
  } = $props();

  const clientSections = $derived(sections.filter((s) => s.audience !== "internal"));
  const printing = $derived(new Set(effective));

  /** Which source key a section's hint is about — `reporting.source.ga4` → `ga4`. */
  function sourceOf(entry: SectionCatalogEntry): string {
    return (entry.source_key ?? "").split(".").pop() ?? "";
  }

  /**
   * Whether this client has what the section needs. Two sections have two possible answers and
   * count as wired up if *either* is linked — `rankings` (#373: SE Ranking, or Search Console)
   * and `search_engines` (#381: SE Ranking's per-engine positions, or GA4's organic split).
   *
   * Listing both here rather than special-casing `rankings` is the point: a section whose data
   * has two possible sources is now a shape this product has twice, and the picker promising a
   * section the run then drops is the failure #373 was about.
   */
  const EITHER_OF: Record<string, string[]> = {
    rankings: ["seranking", "gsc"],
    search_engines: ["seranking", "ga4"],
  };

  function connected(entry: SectionCatalogEntry): boolean | null {
    const source = sourceOf(entry);
    if (!source) return null;
    const either = EITHER_OF[source];
    if (either) return either.some((name) => linkedSources.includes(name));
    return linkedSources.includes(source);
  }

  function value(key: string): string {
    const own = overrides[key];
    return own === undefined ? "" : String(own);
  }

  function choose(key: string, raw: string) {
    const next = { ...overrides };
    if (raw === "") delete next[key];
    else next[key] = raw === "true";
    overrides = next;
  }

  const selectClass =
    "rounded-lg border border-border bg-surface-raised px-2 py-1 text-xs text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<!-- One field, so the form posts the diff rather than nine checkboxes whose absence would be
     indistinguishable from "off" — the very thing this control exists to keep apart. -->
<input type="hidden" name="sections" value={JSON.stringify(overrides)} />

<ul class="divide-y divide-border rounded-xl border border-border">
  {#each clientSections as entry (entry.key)}
    {@const inheritsOn = templateDefaults[entry.key] !== false}
    {@const on = printing.has(entry.key)}
    {@const wired = connected(entry)}
    <li class="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
      <span class="shrink-0" aria-hidden="true">
        {#if on}
          <Check size={15} class="text-green-600 dark:text-green-400" />
        {:else}
          <Minus size={15} class="text-text-muted" />
        {/if}
      </span>

      <span class="min-w-0 flex-1">
        <span class="block text-sm text-text">{t(entry.title_key)}</span>
        {#if entry.source_key}
          <span class="block text-xs text-text-muted">
            {t(entry.source_key)}
            {#if wired === true}
              · <span class="text-green-700 dark:text-green-400"
                >{t("reporting.profile.source_linked")}</span
              >
            {:else if wired === false}
              · <span class="text-amber-700 dark:text-amber-400"
                >{t("reporting.profile.source_missing")}</span
              >
            {/if}
          </span>
        {/if}
      </span>

      <select
        class={selectClass}
        {disabled}
        aria-label={t(entry.title_key)}
        value={value(entry.key)}
        onchange={(event) => choose(entry.key, (event.currentTarget as HTMLSelectElement).value)}
      >
        <option value="">
          {inheritsOn
            ? t("reporting.profile.section_inherit_on")
            : t("reporting.profile.section_inherit_off")}
        </option>
        <option value="true">{t("reporting.profile.section_on")}</option>
        <option value="false">{t("reporting.profile.section_off")}</option>
      </select>
    </li>
  {/each}
</ul>
