<script lang="ts">
  /**
   * One live drill-down (top pages/queries/campaigns), lazy-loaded (issue #133).
   *
   * The tab renders its stored trends instantly, then each drill-down fetches from the
   * `/marketing/drilldown` proxy on mount so a slow/failing Google call never blocks the page. The
   * API caches ~1h and returns a labelled `unavailable` state (no scope / Ads token / revoked),
   * which we show with a deep link out to the real tool.
   *
   * With `edit` set and this being the GA4 key-events table, every row gets inline name fields —
   * the client-friendly label per key event (#192) is typed right where the event shows. Stored
   * labels whose event didn't appear in this range stay editable below, and an event can be
   * added by its raw GA4 name, so labeling never depends on a live Google call.
   */
  import { ExternalLink, Plus, X } from "@lucide/svelte";

  import { localeLabel, t } from "$lib/core/i18n";
  import { editLocale } from "$lib/core/i18n-edit.svelte";

  import {
    channelLabel,
    drilldownHelp,
    drilldownLabel,
    fmtMetric,
    metricHelp,
    metricLabel,
    sourceLabel,
  } from "./format";
  import type { DrilldownResponse, MarketingSource, SourceEditState } from "./types";

  let {
    companyId,
    linkId,
    source,
    kind,
    period,
    currency,
    edit = null,
    onchange,
  }: {
    companyId: string;
    linkId: string;
    source: MarketingSource;
    kind: string;
    /** The period token the dashboard is on (#316) — the API resolves it, not us. */
    period: string;
    currency: string | null;
    /** The source's edit state while edit mode is on — enables the key-event label fields. */
    edit?: SourceEditState | null;
    /** Called after every label mutation — the host persists. */
    onchange?: () => void;
  } = $props();

  let loading = $state(true);
  let data = $state<DrilldownResponse | null>(null);

  // Only the GA4 key-events table edits labels; every other drill-down renders as usual.
  const editsLabels = $derived(edit !== null && source === "ga4" && kind === "key_events");

  async function load() {
    loading = true;
    try {
      const params = new URLSearchParams({
        company_id: companyId,
        link_id: linkId,
        kind,
        period,
      });
      const res = await fetch(`/marketing/drilldown?${params}`);
      data = (await res.json()) as DrilldownResponse;
    } catch {
      data = {
        source,
        kind,
        columns: [],
        rows: [],
        available: false,
        unavailable_reason: "marketing.accounts_error",
        deep_link: "",
      };
    } finally {
      loading = false;
    }
  }

  // Re-fetch when the period changes (reactive via the key on the parent).
  $effect(() => {
    void period;
    void load();
  });

  // The language these name fields are written in — the dashboard's shared switcher (docs/UX.md),
  // so one choice covers every key event here and every tile above.
  const locale = $derived(editLocale.current);

  /** Pure read for the template. Creating a missing entry here would mutate state during
   *  render — Svelte 5 rejects that (state_unsafe_mutation), which froze this card on
   *  "Loading" the moment the fetch returned real rows. Entries are created in setLabel,
   *  which only ever runs from an input event. */
  function labelValue(key: string): string {
    return edit?.event_labels[key]?.[locale] ?? "";
  }
  function setLabel(key: string, value: string) {
    if (!edit) return;
    const entry = (edit.event_labels[key] ??= {});
    entry[locale] = value;
  }
  function removeLabel(key: string) {
    if (!edit) return;
    delete edit.event_labels[key];
    onchange?.();
  }

  // Stored labels whose event the current range didn't surface — still editable (never lose a
  // label to a quiet month), plus the adder for a not-yet-fetched event name.
  const fetchedKeys = $derived(new Set((data?.rows ?? []).map((r) => r.key).filter(Boolean)));
  const storedOnlyKeys = $derived(
    editsLabels && edit ? Object.keys(edit.event_labels).filter((k) => !fetchedKeys.has(k)) : [],
  );
  let newEventName = $state("");
  function addEvent() {
    const name = newEventName.trim();
    if (!edit || !name || edit.event_labels[name]) return;
    edit.event_labels[name] = {};
    newEventName = "";
  }
</script>

<!-- min-w-0: this root is a grid item (the drill-down grid in MarketingSourceSection); without
     it the item's automatic min-width is the table's min-content width, so a wide table grows
     the page sideways on mobile instead of scrolling inside the overflow-x-auto wrapper below
     (docs/UX.md, "a flex or grid item without min-w-0", #36 / #195). -->
<div class="min-w-0">
  <div class="mb-2 flex items-center justify-between gap-2">
    <h4 class="text-sm font-semibold text-text">{drilldownLabel(kind)}</h4>
    {#if data?.deep_link && !edit}
      <a
        href={data.deep_link}
        target="_blank"
        rel="noopener noreferrer"
        class="flex items-center gap-1 text-xs text-text-muted hover:text-brand"
      >
        {t("marketing.open_in", { source: sourceLabel(source) })}
        <ExternalLink size={12} />
      </a>
    {/if}
  </div>

  {#if editsLabels}
    <p class="mb-2 text-xs text-text-muted">{t("marketing.layout.key_events_hint")}</p>
  {:else if drilldownHelp(kind)}
    <!-- Above the table, not inside it: what "Prompts" and "Concurrenten" are is a question the
         reader has before the first row, and the answer is the same whether the fetch found
         rows, found none, or could not run at all. -->
    <p class="mb-2 text-xs leading-relaxed text-text-muted">{drilldownHelp(kind)}</p>
  {/if}

  {#if loading}
    <p class="text-sm text-text-muted">{t("marketing.loading")}</p>
  {:else if data && !data.available && !editsLabels}
    <p class="text-sm text-text-muted">
      {t("marketing.drilldown_unavailable", {
        reason: t(data.unavailable_reason ?? "marketing.no_data"),
      })}
    </p>
  {:else if (!data || data.rows.length === 0) && !editsLabels}
    <p class="text-sm text-text-muted">{t("marketing.no_data")}</p>
  {:else}
    {#if data && data.rows.length > 0}
      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="border-b border-border text-left text-xs text-text-muted">
              <th class="py-1.5 pr-2 font-medium">{drilldownLabel(kind)}</th>
              {#each data.columns as col (col)}
                <!-- A column header has no room for the sentence, so it carries it as a hover.
                     The tiles above are where a touch reader gets it written out. -->
                <th class="py-1.5 pl-2 text-right font-medium" title={metricHelp(col) || undefined}>
                  {metricLabel(col)}
                </th>
              {/each}
            </tr>
          </thead>
          <tbody>
            {#each data.rows as row (row.label)}
              <tr class="border-b border-border/50">
                <td class="max-w-[16rem] py-1.5 pr-2 text-text">
                  {#if editsLabels && row.key}
                    <div class="space-y-1">
                      <code class="block break-all text-xs text-text-muted">{row.key}</code>
                      <input
                        value={labelValue(row.key)}
                        oninput={(e) => setLabel(row.key!, e.currentTarget.value)}
                        onchange={() => onchange?.()}
                        aria-label={t("marketing.layout.label_in", {
                          language: localeLabel(locale),
                        })}
                        placeholder={row.key}
                        maxlength="80"
                        class="w-full min-w-32 rounded border border-border bg-surface px-1.5 py-0.5 text-xs text-text outline-none focus:border-brand"
                      />
                    </div>
                  {:else if row.href}
                    <a
                      href={row.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      class="block truncate hover:text-brand"
                    >
                      {row.label}
                    </a>
                  {:else}
                    <!-- Only the channel drill-down carries Google's own fixed vocabulary; a
                         page path or an event name is the client's and stays as written. -->
                    <span class="block truncate">
                      {kind === "channels" ? channelLabel(row.label) : row.label}
                    </span>
                  {/if}
                </td>
                {#each data.columns as col (col)}
                  <td class="py-1.5 pl-2 text-right align-top tabular-nums text-text">
                    {fmtMetric(col, row.metrics[col] ?? 0, currency)}
                  </td>
                {/each}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else if editsLabels}
      <p class="text-sm text-text-muted">{t("marketing.layout.no_key_events")}</p>
    {/if}

    {#if editsLabels && edit}
      {#if storedOnlyKeys.length > 0}
        <ul class="mt-2 space-y-1.5">
          {#each storedOnlyKeys as key (key)}
            <li
              class="flex flex-wrap items-center gap-1.5 rounded-lg border border-dashed border-border px-2 py-1.5"
            >
              <code class="min-w-24 break-all text-xs text-text-muted">{key}</code>
              <span class="ml-auto flex min-w-0 flex-wrap items-center gap-1">
                <input
                  value={labelValue(key)}
                  oninput={(e) => setLabel(key, e.currentTarget.value)}
                  onchange={() => onchange?.()}
                  aria-label={t("marketing.layout.label_in", { language: localeLabel(locale) })}
                  placeholder={key}
                  maxlength="80"
                  class="w-36 min-w-0 rounded border border-border bg-surface px-1.5 py-0.5 text-xs text-text outline-none focus:border-brand"
                />
                <button
                  type="button"
                  class="rounded p-1 text-text-muted hover:text-red-600 dark:hover:text-red-400"
                  aria-label={t("marketing.layout.remove_event", { event: key })}
                  onclick={() => removeLabel(key)}
                >
                  <X size={14} />
                </button>
              </span>
            </li>
          {/each}
        </ul>
      {/if}
      <div class="mt-2 flex items-center gap-1.5">
        <input
          bind:value={newEventName}
          placeholder={t("marketing.layout.add_event_placeholder")}
          maxlength="100"
          class="w-44 rounded border border-border bg-surface px-2 py-1 text-xs text-text outline-none focus:border-brand"
          onkeydown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addEvent();
            }
          }}
        />
        <button
          type="button"
          onclick={addEvent}
          class="flex items-center gap-1 rounded-lg border border-border px-2 py-1 text-xs text-text hover:border-brand hover:text-brand"
        >
          <Plus size={12} />
          {t("marketing.layout.add_event")}
        </button>
      </div>
    {/if}
  {/if}
</div>
