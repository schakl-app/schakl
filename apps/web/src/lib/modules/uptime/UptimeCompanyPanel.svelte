<script lang="ts">
  /**
   * The uptime panel on a client's page (docs/UPTIME.md §6, `uptime/panels.py`).
   *
   * A **summary**, not a list, because the API panel is one: a client with forty monitors would
   * push every other panel off the company page, and the question this page asks is "is anything
   * wrong here", which is a count.
   *
   * It exists at all because the API had been contributing this panel with nothing registered to
   * render it, so `companies/[id]` fell through to its `<pre>{JSON.stringify(...)}</pre>` escape
   * hatch and printed `{"total": 2, "by_status": {"active": 2}, "visible": true}` on every
   * client's page. That fallback is a developer's affordance; reaching it on a screen an agency
   * shows its own staff is a bug, and the panel it stands in for cannot be told apart from one
   * that was never finished.
   *
   * `visible: false` is the API's answer to a caller without `uptime.monitor.read` — it renders
   * nothing at all rather than "0 monitors", because a reader who may not look must not be told
   * a number, and "none" is a different fact from "not for you".
   */
  import { t } from "$lib/core/i18n";

  let { data }: { data: Record<string, unknown> } = $props();

  const summary = $derived(
    (data ?? {}) as { total?: number; by_status?: Record<string, number>; visible?: boolean },
  );
  const total = $derived(summary.total ?? 0);
  const byStatus = $derived(summary.by_status ?? {});

  /**
   * The states worth a line, in the order an agency triages them, and each with its own glyph.
   * Never colour alone: the dev tenant's brand colour is gold, so an amber warning and a branded
   * accent render identically.
   *
   * `active` is deliberately not in this list — it is the ordinary state and the total already
   * counts it, so giving it a row of its own would bury the two rows somebody is looking for.
   */
  const ATTENTION = [
    { key: "error", glyph: "■", cls: "text-red-600" },
    { key: "drift", glyph: "▲", cls: "text-amber-600" },
    { key: "missing", glyph: "⊘", cls: "text-muted" },
    { key: "pending", glyph: "○", cls: "text-muted" },
  ] as const;

  const rows = $derived(
    ATTENTION.filter((s) => (byStatus[s.key] ?? 0) > 0).map((s) => ({
      ...s,
      count: byStatus[s.key] ?? 0,
    })),
  );
  const healthy = $derived(byStatus["active"] ?? 0);
</script>

{#if summary.visible === false}
  <p class="text-sm text-muted">{t("uptime.company.no_access")}</p>
{:else if total === 0}
  <p class="text-sm text-muted">{t("uptime.company.empty")}</p>
{:else}
  <p class="text-sm text-text">
    {total === 1 ? t("uptime.company.total_one") : t("uptime.company.total", { count: total })}
  </p>
  {#if rows.length}
    <ul class="mt-2 space-y-1">
      {#each rows as s (s.key)}
        <li class="flex items-center gap-2 text-sm text-text">
          <span class={s.cls} aria-hidden="true">{s.glyph}</span>
          <span>{t(`uptime.company.status.${s.key}`, { count: s.count })}</span>
        </li>
      {/each}
    </ul>
  {:else}
    <!-- Every monitor active is worth saying out loud rather than leaving as an absence of
         warnings: "nothing is wrong" and "nothing has been checked" look the same otherwise. -->
    <p class="mt-1 text-sm text-muted">
      {healthy === 1
        ? t("uptime.company.all_ok_one")
        : t("uptime.company.all_ok", { count: healthy })}
    </p>
  {/if}
{/if}
