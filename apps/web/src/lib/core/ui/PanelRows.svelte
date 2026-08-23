<script lang="ts" generics="T">
  /**
   * The rows a panel or a dashboard tile draws, and the one sentence it owes about the rest
   * (#407).
   *
   * Measured before this existed: **seven** hand-picked caps on one client hub — 5, 5, 5, 6, 8,
   * 10 and 50 — five panels with no cap at all, and four different sentences for the same fact,
   * of which only three panels out of twenty said anything. A cap without a notice reads as the
   * complete answer, which is the failure `docs/PERFORMANCE.md` calls silent truncation; a cap
   * nobody chose is how a card's length comes to be decided by whichever module was written that
   * week.
   *
   * **Two affordances, and which one a panel gets is decided by where the rest of the rows are.**
   * They are not interchangeable, and picking the wrong one is most of why the hub was
   * inconsistent.
   *
   * - The rest is **already on the page** (the API sent 8, we draw 3) → *expand in place*. No
   *   navigation, no request, no losing the client's page. `collapsed` turns this on.
   * - The rest is **not on the page** (the API capped at 5 of 23) → *hand over*, and the
   *   hand-over must satisfy what `docs/UX.md` already demands of one: the **whole** count, a
   *   deep link **carrying the filter**, and a destination that applies it.
   *
   * A panel may need both — expand the fetched set, then hand over for the rest — and the honest
   * sentence when both apply is one line, not two.
   *
   * `total` is the whole count. Where an endpoint cannot cheaply produce one, ask it for one row
   * more than you keep and pass `hasMore` instead (the `comments_truncated` pattern, CLAUDE.md
   * §10): "there are more" is a weaker claim than "23", and both beat silence.
   *
   * A panel whose rows all fit draws no expander and no notice — the footer is absent, not empty.
   */
  import type { Snippet } from "svelte";

  import { t } from "$lib/core/i18n";

  let {
    rows,
    collapsed,
    total,
    hasMore = false,
    href,
    linkLabel,
    alwaysLink = false,
    class: extra = "",
    children,
    actions,
  }: {
    rows: T[];
    /** Draw this many until the reader asks for the rest. Omit to draw every fetched row. */
    collapsed?: number;
    /** How many rows exist behind the fetched page. Omit when the endpoint cannot say. */
    total?: number;
    /** "There are more, and we do not know how many" — the limit+1 probe. */
    hasMore?: boolean;
    /** Where the rest of the rows live. Must carry this record's filter. */
    href?: string;
    /** Overrides the generic "Alle {count} bekijken →" where a module has a noun worth using. */
    linkLabel?: string;
    /** Draw the hand-over even when nothing is hidden — for a panel that is always a window. */
    alwaysLink?: boolean;
    class?: string;
    children: Snippet<[T[]]>;
    /**
     * The panel's own footer controls — a `＋ nieuw`, a "log hours". They share the line with
     * the hand-over rather than sitting under it: a card whose last two rows are both footer
     * is more chrome than content, and it is one line the reader scans, not two.
     */
    actions?: Snippet;
  } = $props();

  let expanded = $state(false);

  const expandable = $derived(collapsed != null && rows.length > collapsed);
  const shown = $derived(expandable && !expanded ? rows.slice(0, collapsed) : rows);
  /** Rows the API did not send. `total` wins over the probe when both are known. */
  const beyond = $derived(total != null ? Math.max(0, total - rows.length) : 0);
  const truncated = $derived(beyond > 0 || (total == null && hasMore));
  const showLink = $derived(Boolean(href) && (truncated || alwaysLink));
  /** A notice with nowhere to go: still said, because the alternative is claiming completeness. */
  const showNotice = $derived(truncated && !showLink);

  const handoverLabel = $derived(
    linkLabel ??
      (total != null ? t("common.panel.view_all", { count: total }) : t("common.panel.view_more")),
  );
</script>

{@render children(shown)}

{#if expandable || showLink || showNotice || actions}
  <!-- One row, never two: "Nog 5 tonen   Alle 23 projecten bekijken →" is one fact about this
       card, and splitting it across two is how a footer becomes more chrome than content.
       Separated by space rather than by a `·`, because on a phone the row wraps and a bullet
       then either ends a line or begins one — both read as a typo. The gap is the house
       footer's own (`gap-4`), which is what these links wore before this component existed. -->
  <div class="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs {extra}">
    {#if expandable}
      <button
        type="button"
        class="font-medium text-brand hover:underline"
        aria-expanded={expanded}
        onclick={() => (expanded = !expanded)}
      >
        {expanded
          ? t("common.show_less")
          : t("common.panel.show_more", { count: rows.length - (collapsed ?? 0) })}
      </button>
    {/if}
    {#if showLink}
      <!-- The notice *is* the link (#323): a navigation is an `<a href>`, so it previews, opens
           in a tab and survives a middle click like every other "see the rest" control. -->
      <a {href} data-sveltekit-preload-data="hover" class="text-brand hover:underline"
        >{handoverLabel}</a
      >
    {:else if showNotice}
      <span class="text-text-muted">
        {total != null
          ? t("common.panel.truncated", { shown: rows.length, total })
          : t("common.panel.truncated_unknown", { shown: rows.length })}
      </span>
    {/if}
    {#if actions}{@render actions()}{/if}
  </div>
{/if}
