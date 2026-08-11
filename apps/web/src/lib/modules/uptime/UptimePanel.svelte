<script lang="ts">
  /**
   * The uptime panel on a website's page (docs/UPTIME.md §6).
   *
   * Reads the **mirror**, never Uptime Kuma: a website page must not wait on an outside
   * service to render (docs/PERFORMANCE.md), and the whole point of storing what we last
   * observed is that a failed connection still leaves something true on the screen.
   *
   * So every row carries *when* it was observed. A status with no timestamp beside it invites
   * the reader to treat a week-old reading as current, which is the one thing a monitoring
   * panel must never do.
   */
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";

  type Monitor = {
    id: string;
    name: string;
    monitor_type: string;
    target: string | null;
    sync_status: string;
    remote_active: boolean | null;
    last_observed_at: string | null;
    last_error: string | null;
  };

  // `data: unknown` and narrow here, matching every other entity panel: the registry types the
  // component contract, and a narrower prop type makes the component unassignable to it.
  let { data }: { data: unknown } = $props();
  const monitors = $derived(((data as { monitors?: Monitor[] })?.monitors ?? []) as Monitor[]);

  /**
   * Glyph + word, never colour alone: the dev tenant's brand colour is gold, so a coloured dot
   * on its own is unreadable as state. `null` is "never observed", which is emphatically not
   * "down" — an unmonitored site and a broken one must not look the same.
   */
  function look(m: Monitor): { glyph: string; cls: string; key: string } {
    if (m.sync_status === "missing")
      return { glyph: "⊘", cls: "text-muted", key: "uptime.monitor.missing" };
    if (m.remote_active === null)
      return { glyph: "○", cls: "text-muted", key: "uptime.monitor.unknown" };
    return m.remote_active
      ? { glyph: "●", cls: "text-emerald-600", key: "uptime.monitor.up" }
      : { glyph: "■", cls: "text-red-600", key: "uptime.monitor.paused" };
  }
</script>

{#if monitors.length === 0}
  <p class="text-sm text-muted">{t("uptime.panel.empty")}</p>
{:else}
  <ul class="divide-y divide-border">
    {#each monitors as m (m.id)}
      {@const l = look(m)}
      <li class="flex items-start justify-between gap-3 py-2">
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <span class={l.cls} aria-hidden="true">{l.glyph}</span>
            <span class="truncate text-sm font-medium text-text">{m.name}</span>
            <span class="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-muted">
              {m.monitor_type}
            </span>
          </div>
          {#if m.target}
            <p class="truncate text-xs text-muted">{m.target}</p>
          {/if}
          {#if m.last_error}
            <p class="text-xs text-amber-700">{t(m.last_error)}</p>
          {/if}
        </div>
        <div class="shrink-0 text-right">
          <p class="text-xs text-text">{t(l.key)}</p>
          <p class="text-xs text-muted">
            {m.last_observed_at
              ? t("uptime.panel.observed", { when: fmtDateTime(m.last_observed_at) })
              : t("uptime.panel.never_observed")}
          </p>
        </div>
      </li>
    {/each}
  </ul>
{/if}
