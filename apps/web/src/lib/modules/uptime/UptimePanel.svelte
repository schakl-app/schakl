<script lang="ts">
  /**
   * The uptime panel on a website's or a domain's page (docs/UPTIME.md §6, §9).
   *
   * Reads the **mirror**, never Uptime Kuma: a website page must not wait on an outside
   * service to render (docs/PERFORMANCE.md), and the whole point of storing what we last
   * observed is that a failed connection still leaves something true on the screen.
   *
   * So every row carries *when* it was observed. A status with no timestamp beside it invites
   * the reader to treat a week-old reading as current, which is the one thing a monitoring
   * panel must never do.
   *
   * **This is also where a monitor is attached and detached**, and that is the point rather than
   * a convenience. Confirming a matcher's proposal on the settings screen was the only way to
   * link anything, which meant a monitor the matcher found nothing for — a host inside no zone we
   * hold, a bare IP, a client's Kuma naming things its own way — could never be attached at all,
   * and a wrong link could never be undone. docs/UPTIME.md §7 already said where the control
   * belongs: with the thing that has the monitoring, not bolted onto a settings page.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";

  type Monitor = {
    id: string;
    name: string;
    monitor_type: string;
    target: string | null;
    sync_status: string;
    remote_active: boolean | null;
    last_observed_at: string | null;
    last_error: string | null;
    /** The Uptime Kuma group this monitor sits in. `null` is top-level, a real answer. */
    parent_name: string | null;
    /** Which Kuma this came from — drawn only when the tenant has more than one. */
    instance_name?: string | null;
  };

  // `data: unknown` and narrow here, matching every other entity panel: the registry types the
  // component contract, and a narrower prop type makes the component unassignable to it.
  let { data }: { data: unknown } = $props();
  const panel = $derived(
    (data ?? {}) as {
      monitors?: Monitor[];
      /** Streamed: the options arrive after the page, because most visits never open the picker. */
      attachable?: Promise<Monitor[]>;
      /** `website` or `domain` — what a link posted from here attaches to. */
      anchorType?: string;
    },
  );
  const monitors = $derived(panel.monitors ?? []);
  const attachable = $derived(panel.attachable ?? Promise.resolve([] as Monitor[]));
  const anchorType = $derived(panel.anchorType ?? "website");

  // The key the call actually makes, not the one the screen is about (#310): the link route
  // declares `uptime.monitor.write`, which is not what this panel *reads* on.
  const canLink = $derived(can(page.data.user, "uptime.monitor.write"));

  const busy = new InFlight();
  let picked = $state("");
  let attaching = $state(false);

  /** One monitor as a picker option. The instance only disambiguates when there are several. */
  function options(rows: Monitor[]) {
    const many = new Set(rows.map((m) => m.instance_name).filter(Boolean)).size > 1;
    return rows.map((m) => ({
      value: m.id,
      label: m.name,
      // The target is what tells two monitors called "Website" apart, so it is the hint rather
      // than decoration; the instance rides along only when there is a choice to disambiguate.
      hint: [m.target, many ? m.instance_name : null].filter(Boolean).join(" · "),
    }));
  }

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

{#if page.form?.uptimeError}
  <p class="mb-3 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
    {t(page.form.uptimeError)}
  </p>
{/if}

{#if monitors.length === 0}
  <!-- Named per anchor: "geen monitors voor deze website" on a domain page would be describing
       the wrong record, and this panel now renders on both. -->
  <p class="text-sm text-muted">
    {anchorType === "domain" ? t("uptime.panel.empty_domain") : t("uptime.panel.empty")}
  </p>
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
          {#if m.parent_name}
            <!-- The group is context, not a link: it is Uptime Kuma's folder, and this panel
                 mirrors it rather than owning it. The glyph carries the meaning so the line
                 does not read as a second address. -->
            <p class="truncate text-xs text-muted">
              <span aria-hidden="true">🗀</span>
              {m.parent_name}
            </p>
          {/if}
          {#if m.target}
            <p class="truncate text-xs text-muted">{m.target}</p>
          {/if}
          {#if m.last_error}
            <p class="text-xs text-amber-700">{t(m.last_error)}</p>
          {/if}
        </div>
        <div class="flex shrink-0 items-start gap-3">
          <div class="text-right">
            <p class="text-xs text-text">{t(l.key)}</p>
            <p class="text-xs text-muted">
              {m.last_observed_at
                ? t("uptime.panel.observed", { when: fmtDateTime(m.last_observed_at) })
                : t("uptime.panel.never_observed")}
            </p>
          </div>
          {#if canLink}
            <!-- Detaching writes nothing to Uptime Kuma: the monitor keeps running and keeps
                 being mirrored, it simply stops claiming to be this website's. That is why it
                 is an ordinary button and not a confirm dialog — nothing is destroyed, and the
                 same picker below puts it back. `clear()`: the picker underneath starts a new
                 choice, so nothing should be left selected afterwards. -->
            <form
              method="POST"
              action="?/uptimeUnlink"
              use:enhance={busy.clear(`uptime-unlink-${m.id}`)}
            >
              <input type="hidden" name="monitor_id" value={m.id} />
              <Button type="submit" variant="secondary" disabled={busy.active}>
                {t("uptime.link.detach")}
              </Button>
            </form>
          {/if}
        </div>
      </li>
    {/each}
  </ul>
{/if}

{#if canLink}
  {#if attaching}
    <form
      method="POST"
      action="?/uptimeLink"
      class="mt-3 flex flex-wrap items-end gap-2 border-t border-border pt-3"
      use:enhance={busy.clear("uptime-link")}
    >
      <input type="hidden" name="entity_type" value={anchorType} />
      <div class="min-w-0 flex-1">
        <label class="mb-1 block text-sm font-medium text-text" for="uptime-monitor-pick">
          {t("uptime.link.pick")}
        </label>
        {#await attachable}
          <p class="text-xs text-muted">{t("common.loading")}</p>
        {:then rows}
          {#if rows.length}
            <Combobox
              id="uptime-monitor-pick"
              name="monitor_id"
              bind:value={picked}
              items={options(rows)}
              placeholder={t("uptime.link.pick_placeholder")}
              ariaLabel={t("uptime.link.pick")}
            />
          {:else}
            <!-- Said in words rather than left as an empty dropdown: "every monitor is already
                 attached" and "no Uptime Kuma is connected" are different problems with
                 different next steps, and an empty control is silent about both. -->
            <p class="text-xs text-muted">{t("uptime.link.none_attachable")}</p>
          {/if}
        {/await}
      </div>
      <Button type="submit" disabled={busy.active || !picked}>{t("uptime.link.attach")}</Button>
      <Button variant="secondary" onclick={() => (attaching = false)}>{t("common.cancel")}</Button>
    </form>
  {:else}
    <div class="mt-3">
      <Button variant="secondary" onclick={() => (attaching = true)}>
        {t("uptime.link.add")}
      </Button>
    </div>
  {/if}
{/if}
