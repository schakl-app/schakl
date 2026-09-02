<script lang="ts">
  /**
   * Every active hour budget, sorted into the three bands of the one burn scale — over budget,
   * almost spent, within budget — with how far over, in hours.
   *
   * This replaced the budgets donut (#437). A donut answers "what share of the hours we logged
   * went to each project", and nobody had asked that: the slice percentages were shares of the
   * *logged* total, so a project 300 % over its budget read `10,7 %` beside one comfortably
   * inside its budget reading `17,3 %`, and the only thing on the tile that said anything about
   * budgets was the chip under it. The question the tile exists for is "which budgets are in
   * trouble, how badly, and which are fine" — a partition, not a share.
   *
   * Four decisions, three of them borrowed.
   *
   * **The partition is drawn as shape** (`MyTasksWidget`, #438): a band per section for the
   * two that are claims, a hairline for the quiet one, the heading carrying the state's colour
   * and glyph, the rows quiet inside it. The strip at the top is the same partition as one
   * bar — how much of the book is red is the first thing a manager wants to know, and eight
   * headings cannot say it at a glance.
   *
   * **Every heading opens the list it counts** (Principle 7). The counts are the API's, over
   * the whole set (#407), and each opens `?burn=<level>&status=active`, which the list page
   * filters on the same scale — so "4 over budget" is never a figure of 4 over a list of 5.
   *
   * **A bar here may spill.** The one clamped bar (`BudgetBar`) is right for a cell beside a
   * number and wrong for a tile about *how far* over: every over-budget row drew the same full
   * red bar. `burnOverflowBar` puts the budget line at two thirds of the track, so a row that is
   * over visibly runs past the line every row draws — the amount is the picture, and the
   * unclamped number beside it says the rest (docs/UX.md: clamp the bar, never the number).
   *
   * **The amount over is a figure, not only a verdict.** "4 over budget" is *that* something is
   * wrong; `samen 46,5 u eroverheen` is what the agency has to decide about, so the over section
   * carries the sum and every over row leads with its own remainder in words (`core/hours`,
   * #340).
   *
   * Nothing on it is a dead end (#15): a name opens its project, a figure opens the report rows
   * behind it, and a heading, a strip segment and the fold all open the filtered list.
   */
  import { burnOverflowBar, burnPct, type BurnLevel } from "$lib/core/burn";
  import { fmtNumber } from "$lib/core/format";
  import { hoursBurn, type HoursFields } from "$lib/core/hours";
  import { t, tn } from "$lib/core/i18n";
  import { stateBandClass, stateFillClass, stateTextClass } from "$lib/core/state";
  import DashboardWidgetCard from "$lib/core/ui/DashboardWidgetCard.svelte";
  import PanelRows from "$lib/core/ui/PanelRows.svelte";
  import StateMark from "$lib/core/ui/StateMark.svelte";
  import { stateIcon } from "$lib/core/ui/state-icons";

  import {
    BURN_GROUP_STATE,
    BURN_GROUPS,
    burnGroupHref,
    burnGroupLabelKey,
    groupByBurn,
  } from "./burn-groups";

  let { data }: { data: unknown } = $props();

  interface ProjectRow {
    id: string;
    name: string;
    company_name?: string | null;
    hours?: HoursFields | null;
  }
  interface Payload {
    items?: ProjectRow[];
    total?: number;
    over_budget?: number;
    almost_budget?: number;
    within_budget?: number;
    over_budget_hours?: number;
  }
  const payload = $derived((data ?? {}) as Payload);
  const items = $derived(payload.items ?? []);
  const total = $derived(payload.total ?? items.length);
  const groups = $derived(groupByBurn(items));
  // The numbers beside the headings are the **whole** set's (#407), never the fetched page's:
  // the rows are the hottest ten, so the "within budget" band is mostly what fell past the cut.
  const whole = $derived<Record<BurnLevel, number>>({
    over: payload.over_budget ?? groups.over.length,
    warn: payload.almost_budget ?? groups.warn.length,
    ok: payload.within_budget ?? groups.ok.length,
  });
  const overHours = $derived(payload.over_budget_hours ?? 0);

  function bandLabel(level: BurnLevel): string {
    return tn(`projects.widget.band.${level}`, whole[level]);
  }

  /** Band width on the strip, as a share of the whole budgeted set. */
  function stripWidth(level: BurnLevel): number {
    return (whole[level] / Math.max(1, total)) * 100;
  }

  /**
   * The partition as *shape* (#438): a faint band behind the two sections that are claims,
   * a hairline over the quiet one. "Within budget" is drawn `ok` in its heading and nowhere
   * else — a green wash behind the section with nothing to say is the amber-cards mistake in a
   * new key, spending attention on exactly the rows a manager may skip.
   */
  function sectionClass(level: BurnLevel): string {
    const band = level === "ok" ? "" : stateBandClass(BURN_GROUP_STATE[level]);
    return band
      ? `-mx-2.5 mt-3 rounded-lg px-2.5 py-2 first:mt-0 ${band}`
      : "mt-3 border-t border-border pt-2.5 first:mt-0 first:border-t-0 first:pt-0";
  }

  function rowOf(project: ProjectRow) {
    return {
      burn: hoursBurn(project.hours),
      bar: burnOverflowBar(
        burnPct(project.hours?.spent_hours ?? 0, project.hours?.budget_hours ?? null),
      ),
    };
  }
</script>

{#snippet heading(level: BurnLevel)}
  {@const state = BURN_GROUP_STATE[level]}
  {@const Mark = stateIcon(state)}
  <div class="mb-1 flex items-baseline justify-between gap-2">
    <a
      href={burnGroupHref(level)}
      class="flex items-center gap-1.5 text-sm font-semibold hover:underline {stateTextClass(
        state,
      )}"
    >
      {#if Mark}<Mark size={14} aria-hidden="true" class="shrink-0" />{/if}
      {t(burnGroupLabelKey(level))}
      <span class="text-xs font-normal tabular-nums opacity-80">({whole[level]})</span>
    </a>
    {#if level === "over" && overHours > 0}
      <!-- The sum the verdict is about. Over the whole set, like the count beside it. -->
      <span class="shrink-0 text-xs tabular-nums {stateTextClass('late')}">
        {t("projects.widget.over_hours_total", { hours: fmtNumber(overHours) })}
      </span>
    {/if}
  </div>
{/snippet}

{#snippet projectRows(level: BurnLevel, collapsed?: number)}
  {@const state = BURN_GROUP_STATE[level]}
  <PanelRows
    rows={groups[level]}
    {collapsed}
    total={whole[level]}
    href={burnGroupHref(level)}
    alwaysLink={groups[level].length === 0}
  >
    {#snippet children(shown)}
      <ul class="divide-y divide-border">
        {#each shown as project (project.id)}
          {@const row = rowOf(project)}
          <li class="py-1.5">
            <div class="flex items-baseline justify-between gap-3">
              <a href={`/projects/${project.id}`} class="group min-w-0 flex-1">
                <span class="block truncate text-sm text-text group-hover:text-brand">
                  {project.name}
                </span>
                <!-- Whose budget this is: "Onderhoud" is four indistinguishable rows on a tile
                     spanning four clients. -->
                {#if project.company_name}
                  <span class="block truncate text-xs text-text-muted">{project.company_name}</span>
                {/if}
              </a>
              <!-- The remainder leads, in words (`32 u over budget` / `3 u over`), because it is
                   the number a decision is made on; the spend-of-budget sits under it as the
                   arithmetic behind it. Both are the one wording every surface uses (#340), and
                   the figure opens the report rows it is a total of (#15). Loud only when it is
                   gone (Principle 4): the section already says which band this is. -->
              <a
                href={`/overview?project_id=${project.id}`}
                title={row.burn?.title}
                class="shrink-0 text-right hover:underline"
              >
                <span
                  class="block text-sm font-semibold tabular-nums {level === 'over'
                    ? stateTextClass('late')
                    : 'text-text'}">{row.burn?.remainingText ?? row.burn?.spentText}</span
                >
                {#if row.burn?.remainingText}
                  <span class="block text-[11px] tabular-nums text-text-muted"
                    >{row.burn.spentText}</span
                  >
                {/if}
              </a>
            </div>
            {#if row.bar}
              <!-- The spilling bar (`burnOverflowBar`): the line at two thirds is the budget on
                   every row, so a fill that crosses it is visibly *over*, and how far. The spill
                   is hatched as well as red — colour alone is barred (#404), and a hatch says
                   "past the line" to a reader who cannot tell the two reds apart. -->
              <div class="relative mt-1.5 h-1.5 rounded-full bg-surface">
                <div
                  class="absolute inset-y-0 left-0 rounded-full {stateFillClass(state)}"
                  style="width: {row.bar.fill}%"
                ></div>
                {#if row.bar.spill > 0}
                  <div
                    class="absolute inset-y-0 rounded-r-full {stateFillClass('late')}"
                    style="left: {row.bar.mark}%; width: {row.bar
                      .spill}%; background-image: repeating-linear-gradient(135deg, transparent 0 3px, rgba(255, 255, 255, 0.55) 3px 5px)"
                  ></div>
                {/if}
                <div
                  class="absolute inset-y-0 w-px bg-text/60"
                  style="left: {row.bar.mark}%"
                  aria-hidden="true"
                ></div>
              </div>
            {/if}
          </li>
        {/each}
      </ul>
    {/snippet}
  </PanelRows>
{/snippet}

<DashboardWidgetCard
  title={t("dashboard.widget.projects.budgets_overview")}
  href="/projects"
  linkLabel={t("nav.projects")}
>
  {#if total === 0}
    <p class="text-sm text-text-muted">{t("projects.widget.no_budgets")}</p>
  {:else}
    <!-- The whole book as one strip: the three bands by count, each segment opening the list
         it is a share of. A partition needs saying as well as drawing, and the headings below
         are its legend — the strip is what lets "half of it is red" arrive before a single
         name is read. -->
    <div class="mb-3 flex h-2 gap-px overflow-hidden rounded-full bg-surface">
      {#each BURN_GROUPS as level (level)}
        {#if whole[level] > 0}
          <a
            href={burnGroupHref(level)}
            title={bandLabel(level)}
            aria-label={bandLabel(level)}
            class="block h-full {stateFillClass(BURN_GROUP_STATE[level])} hover:opacity-80"
            style="width: {stripWidth(level)}%"
          ></a>
        {/if}
      {/each}
    </div>

    <!-- Over budget renders even when empty: it is the heading the tile exists for, and an
         absent section is a different sentence from "none" (MyTasksWidget's rule for
         *vandaag*). Empty, it is one quiet line in the palette's `ok` — a red heading over a
         count of zero would be a warning about nothing. -->
    {#if whole.over > 0}
      <section class={sectionClass("over")}>
        {@render heading("over")}
        {@render projectRows("over")}
      </section>
    {:else}
      <p class="py-1">
        <StateMark state="ok" label={t("projects.widget.none_over")} />
      </p>
    {/if}

    {#if whole.warn > 0}
      <section class={sectionClass("warn")}>
        {@render heading("warn")}
        {@render projectRows("warn")}
      </section>
    {/if}

    {#if whole.ok > 0}
      <section class={sectionClass("ok")}>
        {@render heading("ok")}
        <!-- The quiet band starts folded: the tile is about trouble, and a scroll of projects
             with room to spare is what made the donut's list read as uniform. -->
        {@render projectRows("ok", 3)}
      </section>
    {/if}
  {/if}
</DashboardWidgetCard>
