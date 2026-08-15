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

  type InstanceOption = { id: string; name: string; mode: string; writable: boolean };
  type Group = { id: string; name: string; instance_id: string };
  type Profile = { id: string; name: string; monitor_type: string; is_default: boolean };
  type CreateForm = { instances: InstanceOption[]; groups: Group[]; profiles: Profile[] };

  // `data: unknown` and narrow here, matching every other entity panel: the registry types the
  // component contract, and a narrower prop type makes the component unassignable to it.
  let { data }: { data: unknown } = $props();
  const panel = $derived(
    (data ?? {}) as {
      monitors?: Monitor[];
      /** Streamed: the options arrive after the page, because most visits never open the picker. */
      attachable?: Promise<Monitor[]>;
      /** Streamed for the same reason: the create form's three pickers (#366). */
      createForm?: Promise<CreateForm>;
      /** `website` or `domain` — what a link posted from here attaches to. */
      anchorType?: string;
    },
  );
  const monitors = $derived(panel.monitors ?? []);
  const attachable = $derived(panel.attachable ?? Promise.resolve([] as Monitor[]));
  const empty: CreateForm = { instances: [], groups: [], profiles: [] };
  const createForm = $derived(panel.createForm ?? Promise.resolve(empty));
  const anchorType = $derived(panel.anchorType ?? "website");

  // The key the call actually makes, not the one the screen is about (#310): the link route
  // declares `uptime.monitor.write`, which is not what this panel *reads* on.
  const canLink = $derived(can(page.data.user, "uptime.monitor.write"));

  const busy = new InFlight();
  let picked = $state("");
  let attaching = $state(false);
  let creating = $state(false);

  /**
   * The hostname this record *is*, for the create form's suggestion (#366).
   *
   * A website has no URL column of its own: its host is the apex or `www.` plus the apex,
   * depending on `websites.root` — which is exactly what `matching.build_index` derives on the
   * API side when it decides which website a found monitor belongs to. Mirrored here rather than
   * fetched, because the host page has already loaded the record and one more request per website
   * and domain page load, to serve a form most visits never open, is the trade docs/PERFORMANCE.md
   * bans. The suggestion is put **in a visible field** the user can correct, so a drift between
   * the two is something you see rather than something you find out about later.
   */
  const anchorHost = $derived.by(() => {
    if (anchorType === "domain") return String(page.data.domain?.name ?? "").toLowerCase();
    const site = page.data.website;
    const apex = String(site?.domain_name ?? "").toLowerCase();
    if (!apex) return "";
    return site?.root ? apex : `www.${apex}`;
  });

  const inputClass =
    "w-full min-w-0 rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const labelClass = "mb-1 block text-sm font-medium text-text";

  /** The types worth offering: one target field each, and nothing needing a payload we cannot ask
   *  for. Everything else stays Uptime Kuma's own screen, which is better at it than a panel. */
  const TYPES = ["http", "keyword", "ping", "port", "dns"] as const;

  /** What a type wants in the target box — a URL for the HTTP family, a bare host for the rest.
   *  `profiles.target_field` says the same thing on the API side. */
  const suggestFor = (type: string) =>
    !anchorHost ? "" : type === "http" || type === "keyword" ? `https://${anchorHost}` : anchorHost;

  let monitorType = $state("http");
  let target = $state("");
  let instanceId = $state("");

  /**
   * Open the form with everything already filled in that we can honestly fill in.
   *
   * The instance defaults to the only writable one when there *is* only one, and is left unchosen
   * otherwise: picking one of several on the tenant's behalf would be deciding which client's
   * Uptime Kuma a monitor lands on.
   */
  function openCreate(form: CreateForm) {
    const writable = form.instances.filter((i) => i.writable);
    monitorType = "http";
    target = suggestFor("http");
    instanceId = writable.length === 1 ? writable[0].id : "";
    creating = true;
  }

  /**
   * Re-suggest the target when the type changes — but only while the box still holds *our* last
   * suggestion. Once somebody has typed in it, that is the answer, and overwriting it because a
   * dropdown moved is the form throwing away the one field it cannot guess.
   */
  function onTypeChange(next: string) {
    if (target === suggestFor(monitorType)) target = suggestFor(next);
    monitorType = next;
  }

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
  {:else if creating}
    <!--
      Create a monitor for this record (#366). `keep()`: the fields stay filled after a save, so a
      failed push is something you retry rather than retype — SvelteKit's default reset would blank
      the box the user just filled in (docs/UX.md, enforced by `pnpm forms:check`).
    -->
    {#await createForm then form}
      {@const writable = form.instances.filter((i) => i.writable)}
      {@const groups = form.groups.filter((g) => g.instance_id === instanceId)}
      <form
        method="POST"
        action="?/uptimeCreateMonitor"
        class="mt-3 grid gap-3 border-t border-border pt-3 sm:grid-cols-2"
        use:enhance={busy.keep("uptime-create")}
      >
        <input type="hidden" name="entity_type" value={anchorType} />

        <div class="sm:col-span-2">
          <label class={labelClass} for="uptime-new-name">{t("uptime.field.name")}</label>
          <input
            id="uptime-new-name"
            name="name"
            class={inputClass}
            required
            value={anchorHost}
            maxlength="255"
          />
        </div>

        <div>
          <label class={labelClass} for="uptime-new-type">{t("uptime.field.monitor_type")}</label>
          <select
            id="uptime-new-type"
            name="monitor_type"
            class={inputClass}
            value={monitorType}
            onchange={(e) => onTypeChange(e.currentTarget.value)}
          >
            {#each TYPES as type (type)}
              <option value={type}>{t(`uptime.type.${type}`)}</option>
            {/each}
          </select>
        </div>

        <div>
          <label class={labelClass} for="uptime-new-target">{t("uptime.field.target")}</label>
          <input id="uptime-new-target" name="target" class={inputClass} bind:value={target} />
        </div>

        {#if monitorType === "port"}
          <div>
            <label class={labelClass} for="uptime-new-port">{t("uptime.field.port")}</label>
            <input
              id="uptime-new-port"
              name="port"
              type="number"
              min="1"
              max="65535"
              class={inputClass}
              required
            />
          </div>
        {/if}

        <div>
          <label class={labelClass} for="uptime-new-instance">{t("uptime.group.instance")}</label>
          <select
            id="uptime-new-instance"
            name="instance_id"
            class={inputClass}
            required
            bind:value={instanceId}
          >
            <option value="">{t("uptime.create.pick_instance")}</option>
            {#each writable as i (i.id)}
              <option value={i.id}>{i.name}</option>
            {/each}
          </select>
        </div>

        <div>
          <!-- The group is Uptime Kuma's own folder and a group *is* a monitor (doc §7). Only the
               selected instance's groups are offered: a monitor cannot sit in a folder on a
               different server, and offering one would be a choice that can only fail. -->
          <label class={labelClass} for="uptime-new-group">{t("uptime.field.parent")}</label>
          <select id="uptime-new-group" name="parent_id" class={inputClass}>
            <option value="">{t("uptime.create.no_group")}</option>
            {#each groups as g (g.id)}
              <option value={g.id}>{g.name}</option>
            {/each}
          </select>
        </div>

        <div>
          <label class={labelClass} for="uptime-new-profile">{t("uptime.field.profile")}</label>
          <select id="uptime-new-profile" name="profile_id" class={inputClass}>
            <!-- An empty value is a real answer and the default one: `null` means *follow the
                 tenant's default profile*, resolved at read time (`profiles.pick_profile`). -->
            <option value="">{t("uptime.field.profile_inherit")}</option>
            {#each form.profiles as p (p.id)}
              <option value={p.id}>{p.name}</option>
            {/each}
          </select>
        </div>

        <!-- Left blank on purpose: an empty box is not a zero. Absent means inherit, from the
             profile and then from the built-in defaults, which is what "volg de standaard" is. -->
        <div>
          <label class={labelClass} for="uptime-new-interval">{t("uptime.field.interval")}</label>
          <input
            id="uptime-new-interval"
            name="interval_seconds"
            type="number"
            min="20"
            max="86400"
            class={inputClass}
            placeholder={t("uptime.create.inherit")}
          />
        </div>

        <div>
          <label class={labelClass} for="uptime-new-retries">{t("uptime.field.retries")}</label>
          <input
            id="uptime-new-retries"
            name="retries"
            type="number"
            min="0"
            max="10"
            class={inputClass}
            placeholder={t("uptime.create.inherit")}
          />
        </div>

        {#if writable.length === 0}
          <!-- Said in words rather than left as an empty dropdown: no Uptime Kuma at all and one
               that only reports to us are different problems with different next steps, and a
               `linked` instance can never be written to (doc §4). -->
          <p class="text-xs text-muted sm:col-span-2">{t("uptime.create.no_instance")}</p>
        {/if}

        <div class="flex gap-2 sm:col-span-2">
          <Button type="submit" disabled={busy.active || !instanceId}>
            {t("uptime.create.submit")}
          </Button>
          <Button variant="secondary" onclick={() => (creating = false)}>
            {t("common.cancel")}
          </Button>
        </div>
      </form>
    {/await}
  {:else}
    <div class="mt-3 flex flex-wrap gap-2">
      <Button variant="secondary" onclick={() => (attaching = true)}>
        {t("uptime.link.add")}
      </Button>
      <!-- The other half of the same question. Attaching answers "which existing monitor watches
           this", creating answers "nothing does yet" — and until now only the first had a
           control, on a record whose whole point is that it may not be monitored at all. -->
      {#await createForm then form}
        <Button variant="secondary" onclick={() => openCreate(form)}>
          {t("uptime.create.add")}
        </Button>
      {/await}
    </div>
  {/if}
{/if}
