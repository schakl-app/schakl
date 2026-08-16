<script lang="ts">
  /**
   * One sync run, as a report rather than a status.
   *
   * A run that answers `{"ok": true}` is a run nobody can check. What a person needs is four
   * things, and the order is the order they are asked in: **when and over what window**, **what
   * changed**, **what was deliberately not touched**, and **what failed**.
   *
   * The window is on the card rather than in a tooltip, because it is a real horizon: Timeon's
   * hour rows carry no modified timestamp, so a change outside it was never looked at. A run that
   * says nothing about what it did not read reads as one that read everything.
   *
   * Warnings and errors are separate, because they need different reactions. A warning is the run
   * telling somebody what it needs from them — an unmapped person, a client Timeon has and schakl
   * does not — and must not make a run red. An error is something that went wrong.
   */
  import { AlertTriangle, CheckCircle2, Info, XCircle } from "@lucide/svelte";

  import { fmtDateTime, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  import { RUN_TILES, runChanged, type TimeonRun } from "./types";

  let { run, compact = false }: { run: TimeonRun; compact?: boolean } = $props();

  const counts = $derived((run.counts ?? {}) as Record<string, number>);
  const tiles = $derived(RUN_TILES.filter((tile) => (counts[tile.key] ?? 0) > 0));
  const changed = $derived(runChanged(run));
  const warnings = $derived((run.warnings ?? []) as { code: string; [key: string]: unknown }[]);
  const errors = $derived((run.errors ?? []) as { code: string; [key: string]: unknown }[]);

  const toneClass = {
    good: "text-emerald-700 dark:text-emerald-400",
    warn: "text-amber-700 dark:text-amber-500",
    plain: "text-text",
  } as const;

  /** The detail beside a warning's own sentence — a name, an address, an id. Whatever the run
   * put there, minus the code itself, so a warning kind added at the API still reads. */
  function detailOf(entry: Record<string, unknown>): string {
    return Object.entries(entry)
      .filter(([key, value]) => key !== "code" && value !== null && value !== undefined)
      .map(([, value]) => String(value))
      .join(" · ");
  }

  /** How many rows a listed warning kind is left showing, and how many there really were.
   *
   * Two numbers, not one, and they come from different places: the *list* is bounded by the API
   * (`MAX_REPORTED`, or a broken mapping writes one entry per row in the organisation), while
   * `counts["warn_<code>"]` stays exact. A screen that printed only the list would say "60
   * klanten" about 108 — which is #373's rule from the other end: an open-ended list folds its
   * tail into one row that **names the size of what it is not showing**.
   */
  const PREVIEW = 4;

  const warningGroups = $derived.by(() => {
    // A plain object, not a `Map`: this is a *derived* value rebuilt from scratch on every
    // change, so it is never a reactive container and `svelte/prefer-svelte-reactivity` is
    // right to insist that a mutable `Map` here would be one by accident.
    const groups: Record<string, { code: string; rows: typeof warnings; total: number }> = {};
    for (const warning of warnings) {
      const group = (groups[warning.code] ??= {
        code: warning.code,
        rows: [] as typeof warnings,
        total: counts[`warn_${warning.code}`] ?? 0,
      });
      group.rows.push(warning);
    }
    // The counter is authoritative where it exists; the list length is the floor.
    return Object.values(groups).map((group) => ({
      ...group,
      total: Math.max(group.total, group.rows.length),
    }));
  });

  /** Which kinds the reader asked to see in full. Keyed, because a run has several. */
  let expanded = $state<Record<string, boolean>>({});
</script>

<div class="rounded-lg border border-border bg-surface p-3">
  <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
    {#if run.ok}
      <CheckCircle2 size={16} class="shrink-0 text-emerald-600" aria-hidden="true" />
    {:else}
      <XCircle size={16} class="shrink-0 text-red-600" aria-hidden="true" />
    {/if}
    <span class="text-sm font-medium text-text">{t(`timeon.kind.${run.kind}`)}</span>
    {#if run.dry_run}
      <!-- Not a badge colour alone: "proefrun" is the whole difference between a report and a
           change, and a viewer who cannot tell two greys apart must still read it. -->
      <span
        class="rounded bg-sky-100 px-1.5 py-0.5 text-xs text-sky-800 dark:bg-sky-500/15 dark:text-sky-400"
      >
        {t("timeon.run.dry")}
      </span>
    {/if}
    <span class="text-xs text-text-muted">{fmtDateTime(run.created_at)}</span>
    {#if run.actor_name}
      <span class="text-xs text-text-muted">· {run.actor_name}</span>
    {:else}
      <span class="text-xs text-text-muted">· {t("timeon.run.by_schedule")}</span>
    {/if}
  </div>

  {#if run.window_from && run.window_to}
    <p class="mt-1 text-xs text-text-muted">
      {t("timeon.run.window", {
        from: fmtNumericDate(run.window_from),
        to: fmtNumericDate(run.window_to),
      })}
    </p>
  {/if}

  {#if run.message}
    <p class="mt-2 flex items-start gap-1.5 break-words text-xs text-red-600">
      <XCircle size={14} class="mt-0.5 shrink-0" aria-hidden="true" />
      <!-- Timeon's own untranslatable words, verbatim: they name the actual problem, and a house
           sentence in their place would say less. Never in an error envelope (§9). -->
      <span>{run.message}</span>
    </p>
  {/if}

  {#if tiles.length > 0}
    <dl class="mt-2 flex flex-wrap gap-x-4 gap-y-1">
      {#each tiles as tile (tile.key)}
        <div class="flex items-baseline gap-1.5">
          <dd class={`text-sm font-medium tabular-nums ${toneClass[tile.tone]}`}>
            {counts[tile.key]}
          </dd>
          <dt class="text-xs text-text-muted">{t(`timeon.count.${tile.key}`)}</dt>
        </div>
      {/each}
    </dl>
  {:else if !run.message}
    <!-- A state, not an absence: "niets te doen" and "we hebben niet gekeken" are different
         answers and an empty strip cannot tell them apart. -->
    <p class="mt-2 text-xs text-text-muted">{t("timeon.run.nothing")}</p>
  {/if}

  {#if run.dry_run && changed}
    <p class="mt-2 text-xs text-sky-700 dark:text-sky-400">{t("timeon.run.dry_note")}</p>
  {/if}

  {#if !compact}
    {#if warnings.length > 0}
      <div class="mt-3">
        <p class="flex items-center gap-1.5 text-xs font-medium text-text">
          <Info size={14} class="shrink-0 text-text-muted" aria-hidden="true" />
          {t("timeon.run.warnings")}
        </p>
        <ul class="mt-1 space-y-1.5">
          {#each warningGroups as group (group.code)}
            {@const shown = expanded[group.code] ? group.rows : group.rows.slice(0, PREVIEW)}
            <li class="flex items-start gap-1.5 text-xs">
              <AlertTriangle size={12} class="mt-0.5 shrink-0 text-amber-600" aria-hidden="true" />
              <div class="min-w-0">
                <p class="text-text">
                  {t(`timeon.warning.${group.code}`)}
                  <span class="tabular-nums text-text-muted">({group.total})</span>
                </p>
                <ul class="mt-0.5 space-y-0.5 text-text-muted">
                  {#each shown as warning, index (index)}
                    <li class="truncate">{detailOf(warning) || "—"}</li>
                  {/each}
                </ul>
                {#if group.rows.length > PREVIEW || group.total > group.rows.length}
                  <button
                    type="button"
                    class="mt-0.5 text-brand hover:underline"
                    onclick={() => (expanded[group.code] = !expanded[group.code])}
                  >
                    {expanded[group.code]
                      ? t("timeon.run.show_less")
                      : t("timeon.run.show_more", {
                          count: Math.max(0, group.total - shown.length),
                        })}
                  </button>
                {/if}
                {#if group.total > group.rows.length && expanded[group.code]}
                  <!-- The list is capped at the API; the count is not. Saying so is the
                       difference between a short list and a wrong one. -->
                  <p class="mt-0.5 text-text-muted">
                    {t("timeon.run.list_capped", { shown: group.rows.length, total: group.total })}
                  </p>
                {/if}
              </div>
            </li>
          {/each}
        </ul>
      </div>
    {/if}

    {#if errors.length > 0}
      <div class="mt-3">
        <p class="text-xs font-medium text-red-600">{t("timeon.run.errors")}</p>
        <ul class="mt-1 space-y-0.5">
          {#each errors as failure, index (index)}
            <li class="break-words text-xs text-red-600">
              {t(`timeon.error.${failure.code}`)}
              {#if detailOf(failure)}
                <span class="opacity-80">— {detailOf(failure)}</span>
              {/if}
            </li>
          {/each}
        </ul>
      </div>
    {/if}
  {/if}
</div>
