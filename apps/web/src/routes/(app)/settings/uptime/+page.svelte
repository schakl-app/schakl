<script lang="ts">
  /**
   * Instellingen → Uptime (docs/UPTIME.md).
   *
   * One row per Uptime Kuma instance, because an agency runs one for itself and clients bring
   * theirs — a single "the Uptime Kuma URL" setting would have been wrong on the first day.
   *
   * Three things this screen exists to say out loud. **Which mode an instance is in**, because
   * `linked` is not a broken `managed`: it is the answer to a client who will not hand over the
   * only administrator account of their own monitoring, and it still delivers the status
   * timeline. **What is wrong, in words an admin can act on** — a revoked token, a rate limiter,
   * a target that answers but is not Kuma each read differently and each needs a different fix.
   * And **that TLS verification is off**, badged rather than buried, because that means we send
   * an administrator credential to whoever answers the address.
   */
  import { Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  let { data, form } = $props();

  const instances = $derived(data.instances ?? []);
  const profiles = $derived(data.profiles ?? []);
  const drifted = $derived(data.drifted ?? []);
  /** What the last sync thinks each unlinked monitor watches, waiting for somebody (#321). */
  const proposed = $derived(data.proposed ?? []);
  const groups = $derived(data.groups ?? []);
  let addingProfile = $state(false);
  let addingGroup = $state(false);

  /** Instance names for the group rows, off the list this page already loaded. */
  const instanceName = $derived(
    new Map(instances.map((i: { id: string; name: string }) => [i.id, i.name])),
  );

  /** A candidate's kind as a word: "website" and "domein" are different promises. */
  const kindLabel = (kind: string) =>
    ({
      website: t("uptime.link.kind_website"),
      domain: t("uptime.link.kind_domain"),
      hosting: t("uptime.link.kind_hosting"),
    })[kind] ?? kind;

  /** Field names as words. A drift that reads `interval_seconds` names a column, not a thing. */
  const fieldLabel = (f: string) =>
    ({
      name: t("uptime.field.name"),
      monitor_type: t("uptime.field.monitor_type"),
      target: t("uptime.field.target"),
      port: "Port",
      interval_seconds: t("uptime.field.interval"),
      retries: t("uptime.field.retries"),
      // A monitor somebody moved into another group in Uptime Kuma (#321). Drift like any
      // other field, and it needs a word here or the row reads `parent_id` at an admin.
      parent_id: t("uptime.field.parent"),
    })[f] ?? f;

  const busy = new InFlight();
  let adding = $state(false);
  let enrolling = $state<string | null>(null);
  let deleteTarget = $state<{ id: string; name: string } | null>(null);
  let confirmDelete = $state(false);

  const inputClass =
    "w-full min-w-0 rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const labelClass = "mb-1 block text-sm font-medium text-text";

  /**
   * Status as a glyph plus a word, never as a colour alone: the dev tenant's brand colour is
   * gold, so `text-brand` renders identically to an amber warning. The glyph carries the state.
   */
  function statusLook(status: string): { glyph: string; cls: string; key: string } {
    switch (status) {
      case "active":
        return { glyph: "●", cls: "text-emerald-600", key: "uptime.status.active" };
      case "needs_reauth":
        return { glyph: "▲", cls: "text-amber-600", key: "uptime.status.needs_reauth" };
      case "error":
        return { glyph: "■", cls: "text-red-600", key: "uptime.status.error" };
      default:
        return { glyph: "○", cls: "text-muted", key: "uptime.status.pending" };
    }
  }
</script>

<svelte:head><title>{pageTitle(t("uptime.settings.title"))}</title></svelte:head>

<div class="mx-auto w-full max-w-4xl px-4 py-6">
  <header class="mb-6">
    <h1 class="text-xl font-semibold text-text">{t("uptime.settings.title")}</h1>
    <p class="mt-1 text-sm text-muted">{t("uptime.settings.intro")}</p>
  </header>

  {#if form?.error}
    <p class="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{t(form.error)}</p>
  {/if}
  {#if form?.report}
    <p class="mb-4 rounded-lg bg-surface-2 px-3 py-2 text-sm text-text">
      {form.report.ok
        ? t("uptime.sync.done", {
            seen: form.report.seen,
            groups: form.report.groups,
            created: form.report.created,
            missing: form.report.missing,
          })
        : t(form.report.error ?? "errors.uptime_failed")}
      {#if form.report.ok && (form.report.matched || form.report.ambiguous)}
        <!-- Said in the same breath as the read, because "34 gelezen" answers nothing about
             whether they landed anywhere. Nothing has been linked: these are proposals. -->
        {t("uptime.sync.links", {
          matched: form.report.matched,
          ambiguous: form.report.ambiguous,
        })}
      {/if}
    </p>
  {/if}
  {#if form?.applied}
    <p class="mb-4 rounded-lg bg-surface-2 px-3 py-2 text-sm text-text">
      {t("uptime.links.applied", {
        linked: form.applied.linked,
        skipped: form.applied.skipped,
      })}
    </p>
  {/if}

  <ul class="space-y-3">
    {#each instances as instance (instance.id)}
      {@const look = statusLook(instance.status)}
      <li class="rounded-xl border border-border bg-surface p-4">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <span class={look.cls} aria-hidden="true">{look.glyph}</span>
              <span class="font-medium text-text">{instance.name}</span>
              <span class="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-muted">
                {t(`uptime.mode.${instance.mode}`)}
              </span>
              {#if instance.insecure}
                <!-- Badged, never buried: TLS off means we hand an admin credential to
                     whoever answers this address. -->
                <span class="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-800">
                  {t("uptime.settings.insecure_badge")}
                </span>
              {/if}
            </div>
            <p class="mt-1 truncate text-sm text-muted">
              {instance.base_url ?? t("uptime.settings.no_url")}
            </p>
            <p class="mt-1 text-xs text-muted">
              {t(look.key)}
              {#if instance.server_version}· {instance.server_version}{/if}
              {#if instance.last_synced_at}
                · {t("uptime.settings.last_synced", {
                  when: fmtDateTime(instance.last_synced_at),
                })}
              {/if}
              ·
              {instance.monitor_count === 1
                ? t("uptime.settings.monitor_count_one")
                : t("uptime.settings.monitor_count", { count: instance.monitor_count })}
              {#if instance.group_count}
                <!-- Only when there are any: "0 groepen" beside every ungrouped instance is
                     noise, and the absence of the phrase already says it. -->
                ·
                {instance.group_count === 1
                  ? t("uptime.settings.group_count_one")
                  : t("uptime.settings.group_count", { count: instance.group_count })}
              {/if}
            </p>
            {#if instance.last_error}
              <p class="mt-1 text-xs text-red-700">{instance.last_error}</p>
            {/if}
            {#if (instance.connect_header_names ?? []).length}
              <!-- Names only. Seeing `CF-Access-Client-Id` listed is how an admin confirms the
                   tunnel is wired; the value is a credential with no read shape. -->
              <p class="mt-1 text-xs text-muted">
                {t("uptime.settings.headers_set", {
                  names: (instance.connect_header_names ?? []).join(", "),
                })}
              </p>
            {/if}
          </div>

          <div class="flex shrink-0 flex-wrap items-center gap-2">
            {#if instance.mode === "managed"}
              <form method="POST" action="?/probe" use:enhance={busy.wrap(`probe-${instance.id}`)}>
                <input type="hidden" name="id" value={instance.id} />
                <Button type="submit" variant="secondary" disabled={busy.active}>
                  {t("uptime.settings.probe")}
                </Button>
              </form>
              <form method="POST" action="?/sync" use:enhance={busy.wrap(`sync-${instance.id}`)}>
                <input type="hidden" name="id" value={instance.id} />
                <Button
                  type="submit"
                  variant="secondary"
                  disabled={busy.active || !instance.token_configured}
                >
                  {t("uptime.settings.sync")}
                </Button>
              </form>
              <!-- Confirms every proposal with exactly one answer, on this instance. An
                   instance-level act, so it sits with this instance's buttons rather than
                   above a cross-instance list. The ambiguous ones stay below, unconfirmed. -->
              <form
                method="POST"
                action="?/applyLinks"
                use:enhance={busy.wrap(`links-${instance.id}`)}
              >
                <input type="hidden" name="id" value={instance.id} />
                <Button type="submit" variant="secondary" disabled={busy.active}>
                  {t("uptime.links.apply")}
                </Button>
              </form>
              <Button
                variant="secondary"
                onclick={() => (enrolling = enrolling === instance.id ? null : instance.id)}
              >
                {instance.token_configured
                  ? t("uptime.settings.reconnect")
                  : t("uptime.settings.connect")}
              </Button>
            {/if}
            <Button
              variant="secondary"
              aria-label={t("common.delete")}
              onclick={() => {
                deleteTarget = { id: instance.id, name: instance.name };
                confirmDelete = true;
              }}
            >
              <Trash2 class="h-4 w-4" />
            </Button>
          </div>
        </div>

        {#if enrolling === instance.id}
          <!-- The one form that takes a password. It is exchanged for a token and never
               stored, which is why the hint says so in words rather than leaving an admin to
               wonder where their administrator password went.

               `clear()`, not `keep()`: this form starts something new each time, and must not
               leave a password sitting in the box after a successful enrolment. -->
          <form
            method="POST"
            action="?/enrol"
            class="mt-4 grid gap-3 border-t border-border pt-4 sm:grid-cols-2"
            use:enhance={busy.clear(`enrol-${instance.id}`)}
          >
            <input type="hidden" name="id" value={instance.id} />
            <div>
              <label class={labelClass} for="u-{instance.id}">{t("uptime.field.username")}</label>
              <input
                id="u-{instance.id}"
                name="username"
                class={inputClass}
                value={instance.username ?? ""}
                required
              />
            </div>
            <div>
              <label class={labelClass} for="p-{instance.id}">{t("uptime.field.password")}</label>
              <input
                id="p-{instance.id}"
                name="password"
                type="password"
                class={inputClass}
                autocomplete="off"
                required
              />
            </div>
            <div>
              <label class={labelClass} for="t-{instance.id}">{t("uptime.field.totp")}</label>
              <input
                id="t-{instance.id}"
                name="totp"
                class={inputClass}
                autocomplete="off"
                inputmode="numeric"
              />
            </div>
            <div class="sm:col-span-2">
              <label class={labelClass} for="h-{instance.id}">{t("uptime.field.headers")}</label>
              <textarea
                id="h-{instance.id}"
                name="connect_headers"
                class={inputClass}
                rows="2"
                placeholder="CF-Access-Client-Id: ...&#10;CF-Access-Client-Secret: ..."></textarea>
              <p class="mt-1 text-xs text-muted">{t("uptime.field.headers_hint")}</p>
            </div>
            <p class="text-xs text-muted sm:col-span-2">{t("uptime.settings.password_hint")}</p>
            <div class="sm:col-span-2">
              <Button type="submit" disabled={busy.active}>{t("uptime.settings.connect")}</Button>
            </div>
          </form>
        {/if}
      </li>
    {:else}
      <li class="rounded-xl border border-dashed border-border p-6 text-center text-sm text-muted">
        {t("uptime.settings.empty")}
      </li>
    {/each}
  </ul>

  {#if drifted.length}
    <!-- The drift queue. Two buttons per row and no default: an agency editing a monitor in
         Uptime Kuma because that screen was closer to hand is the normal case, so a reconcile
         that could only overwrite would teach people to stop using the tool they already had. -->
    <section class="mt-6">
      <h2 class="mb-2 text-sm font-semibold text-text">{t("uptime.drift.title")}</h2>
      <ul class="space-y-2">
        {#each drifted as m (m.id)}
          <li
            class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-300 bg-amber-50 p-3"
          >
            <div class="min-w-0">
              <p class="text-sm font-medium text-text">{m.name}</p>
              <p class="text-xs text-muted">
                {t("uptime.drift.fields", {
                  fields: (m.drift_fields ?? []).map(fieldLabel).join(", "),
                })}
              </p>
            </div>
            <div class="flex shrink-0 gap-2">
              <form method="POST" action="?/reconcile" use:enhance={busy.wrap(`push-${m.id}`)}>
                <input type="hidden" name="id" value={m.id} />
                <input type="hidden" name="direction" value="push" />
                <Button type="submit" variant="secondary" disabled={busy.active}>
                  {t("uptime.drift.push")}
                </Button>
              </form>
              <form method="POST" action="?/reconcile" use:enhance={busy.wrap(`adopt-${m.id}`)}>
                <input type="hidden" name="id" value={m.id} />
                <input type="hidden" name="direction" value="adopt" />
                <Button type="submit" variant="secondary" disabled={busy.active}>
                  {t("uptime.drift.adopt")}
                </Button>
              </form>
            </div>
          </li>
        {/each}
      </ul>
    </section>
  {/if}

  {#if proposed.length}
    <!-- What a sync found and nobody has confirmed (#321). A proposal, never a link: attaching
         a client's monitoring to another client's record is invisible afterwards, because every
         row is still valid. One candidate gets one button; several get one button each and no
         default, for the reason the drift rows above have two — picking for somebody is how a
         screen makes a decision it cannot explain later. -->
    <section class="mt-6">
      <h2 class="mb-1 text-sm font-semibold text-text">{t("uptime.links.title")}</h2>
      <p class="mb-2 text-xs text-muted">{t("uptime.links.intro")}</p>
      <ul class="space-y-2">
        {#each proposed as m (m.id)}
          <li class="rounded-xl border border-border bg-surface p-3">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <p class="text-sm font-medium text-text">{m.name}</p>
                <p class="truncate text-xs text-muted">{m.target ?? ""}</p>
                {#if m.link_status === "ambiguous"}
                  <p class="mt-1 text-xs text-amber-700">{t("uptime.links.ambiguous")}</p>
                {/if}
              </div>
              <div class="flex shrink-0 flex-wrap gap-2">
                {#each m.link_candidates ?? [] as c (c.entity_id)}
                  <form
                    method="POST"
                    action="?/link"
                    use:enhance={busy.wrap(`link-${m.id}-${c.entity_id}`)}
                  >
                    <input type="hidden" name="id" value={m.id} />
                    <!-- The candidate's own values, not anything typed above: the obvious
                         press must answer with the row it was drawn from. -->
                    <input type="hidden" name="entity_type" value={c.entity_type} />
                    <input type="hidden" name="entity_id" value={c.entity_id} />
                    <Button type="submit" variant="secondary" disabled={busy.active}>
                      {t("uptime.links.confirm", {
                        kind: kindLabel(c.entity_type),
                        label: c.label,
                      })}
                    </Button>
                  </form>
                {/each}
              </div>
            </div>
          </li>
        {/each}
      </ul>
      {#if data.proposedTotal > proposed.length}
        <p class="mt-2 text-xs text-muted">
          {t("uptime.links.more", { count: data.proposedTotal - proposed.length })}
        </p>
      {/if}
    </section>
  {/if}

  <!-- Groups. A group *is* a monitor here (`type = "group"`), because Uptime Kuma has no group
       entity — so this section creates, renames and deletes monitors of that one type rather
       than a second concept the far end does not have. -->
  <section class="mt-8">
    <h2 class="mb-1 text-sm font-semibold text-text">{t("uptime.group.title")}</h2>
    <p class="mb-2 text-xs text-muted">{t("uptime.group.pause_cascade")}</p>
    <ul class="space-y-2">
      {#each groups as g (g.id)}
        <li class="rounded-xl border border-border bg-surface p-3">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <!-- `keep()`: a rename edits the thing in front of you, so the field keeps what you
                 just saved instead of blanking (docs/UX.md, enforced by `pnpm forms:check`). -->
            <form
              method="POST"
              action="?/renameGroup"
              class="flex min-w-0 flex-1 items-center gap-2"
              use:enhance={busy.keep(`rename-${g.id}`)}
            >
              <input type="hidden" name="id" value={g.id} />
              <input
                name="name"
                class={inputClass}
                value={g.name}
                required
                aria-label={t("uptime.field.name")}
              />
              <Button type="submit" variant="secondary" disabled={busy.active}>
                {t("common.save")}
              </Button>
            </form>
            <div class="flex shrink-0 items-center gap-3">
              <span class="text-xs text-muted">
                {instanceName.get(g.instance_id) ?? ""}
                · {g.child_count === 1
                  ? t("uptime.group.child_count_one")
                  : t("uptime.group.child_count", { count: g.child_count })}
              </span>
              <!-- Offered only while it can succeed: a group with children is refused by the
                   API (the local FK would silently un-nest them while Kuma keeps the tree), and
                   a button that always answers an error is a broken control (#253). -->
              {#if !g.child_count}
                <form method="POST" action="?/deleteGroup" use:enhance={busy.clear(`dg-${g.id}`)}>
                  <input type="hidden" name="id" value={g.id} />
                  <Button type="submit" variant="secondary" disabled={busy.active}>
                    {t("common.delete")}
                  </Button>
                </form>
              {/if}
            </div>
          </div>
        </li>
      {:else}
        <li
          class="rounded-xl border border-dashed border-border p-4 text-center text-sm text-muted"
        >
          {t("uptime.group.empty")}
        </li>
      {/each}
    </ul>

    {#if addingGroup}
      <form
        method="POST"
        action="?/createGroup"
        class="mt-3 grid gap-3 rounded-xl border border-border bg-surface p-4 sm:grid-cols-2"
        use:enhance={busy.clear("new-group")}
      >
        <div>
          <label class={labelClass} for="g-name">{t("uptime.field.name")}</label>
          <input id="g-name" name="name" class={inputClass} required />
        </div>
        <div>
          <label class={labelClass} for="g-instance">{t("uptime.group.instance")}</label>
          <select id="g-instance" name="instance_id" class={inputClass} required>
            {#each instances as i (i.id)}
              <option value={i.id}>{i.name}</option>
            {/each}
          </select>
        </div>
        <div class="flex gap-2 sm:col-span-2">
          <Button type="submit" disabled={busy.active}>{t("common.save")}</Button>
          <Button variant="secondary" onclick={() => (addingGroup = false)}>
            {t("common.cancel")}
          </Button>
        </div>
      </form>
    {:else if instances.length}
      <div class="mt-3">
        <Button variant="secondary" onclick={() => (addingGroup = true)}>
          {t("uptime.group.add")}
        </Button>
      </div>
    {/if}
  </section>

  <section class="mt-8">
    <h2 class="mb-2 text-sm font-semibold text-text">{t("uptime.profile.title")}</h2>
    <ul class="space-y-2">
      {#each profiles as p (p.id)}
        <li
          class="flex items-center justify-between gap-3 rounded-xl border border-border bg-surface p-3"
        >
          <div class="min-w-0">
            <div class="flex items-center gap-2">
              <span class="text-sm font-medium text-text">{p.name}</span>
              {#if p.is_default}
                <span class="rounded bg-surface-2 px-1.5 py-0.5 text-xs text-muted">
                  {t("uptime.profile.default_badge")}
                </span>
              {/if}
            </div>
            <p class="text-xs text-muted">
              {p.monitor_type}
              {#if p.defaults?.interval_seconds}
                · {t("uptime.field.interval")}: {p.defaults.interval_seconds}
              {/if}
              {#if p.defaults?.retries !== undefined}
                · {t("uptime.field.retries")}: {p.defaults.retries}
              {/if}
            </p>
          </div>
          <form method="POST" action="?/deleteProfile" use:enhance={busy.clear(`dp-${p.id}`)}>
            <input type="hidden" name="id" value={p.id} />
            <Button type="submit" variant="secondary" disabled={busy.active}>
              {t("common.delete")}
            </Button>
          </form>
        </li>
      {:else}
        <li
          class="rounded-xl border border-dashed border-border p-4 text-center text-sm text-muted"
        >
          {t("uptime.field.profile_inherit")}
        </li>
      {/each}
    </ul>

    {#if addingProfile}
      <form
        method="POST"
        action="?/createProfile"
        class="mt-3 grid gap-3 rounded-xl border border-border bg-surface p-4 sm:grid-cols-2"
        use:enhance={busy.clear("new-profile")}
      >
        <div>
          <label class={labelClass} for="p-name">{t("uptime.field.name")}</label>
          <input id="p-name" name="name" class={inputClass} required />
        </div>
        <div>
          <label class={labelClass} for="p-type">{t("uptime.field.monitor_type")}</label>
          <input id="p-type" name="monitor_type" class={inputClass} value="http" />
        </div>
        <div>
          <label class={labelClass} for="p-interval">{t("uptime.field.interval")}</label>
          <!-- Blank means inherit, which is why this is not : a prefilled box is a
               decision the tenant did not make. -->
          <input id="p-interval" name="interval_seconds" class={inputClass} inputmode="numeric" />
        </div>
        <div>
          <label class={labelClass} for="p-retries">{t("uptime.field.retries")}</label>
          <input id="p-retries" name="retries" class={inputClass} inputmode="numeric" />
        </div>
        <label class="flex items-center gap-2 text-sm text-text sm:col-span-2">
          <input type="checkbox" name="is_default" value="true" />
          {t("uptime.profile.default_badge")}
        </label>
        <div class="flex gap-2 sm:col-span-2">
          <Button type="submit" disabled={busy.active}>{t("common.save")}</Button>
          <Button variant="secondary" onclick={() => (addingProfile = false)}>
            {t("common.cancel")}
          </Button>
        </div>
      </form>
    {:else}
      <div class="mt-3">
        <Button variant="secondary" onclick={() => (addingProfile = true)}>
          {t("uptime.profile.add")}
        </Button>
      </div>
    {/if}
  </section>

  {#if adding}
    <form
      method="POST"
      action="?/create"
      class="mt-4 grid gap-3 rounded-xl border border-border bg-surface p-4 sm:grid-cols-2"
      use:enhance={busy.clear()}
    >
      <div>
        <label class={labelClass} for="new-name">{t("uptime.field.name")}</label>
        <input id="new-name" name="name" class={inputClass} required />
      </div>
      <div>
        <label class={labelClass} for="new-mode">{t("uptime.field.mode")}</label>
        <select id="new-mode" name="mode" class={inputClass}>
          <option value="managed">{t("uptime.mode.managed")}</option>
          <option value="linked">{t("uptime.mode.linked")}</option>
        </select>
        <p class="mt-1 text-xs text-muted">{t("uptime.field.mode_hint")}</p>
      </div>
      <div class="sm:col-span-2">
        <label class={labelClass} for="new-url">{t("uptime.field.base_url")}</label>
        <input
          id="new-url"
          name="base_url"
          class={inputClass}
          placeholder="https://kuma.voorbeeld.nl"
        />
        <p class="mt-1 text-xs text-muted">{t("uptime.field.base_url_hint")}</p>
      </div>
      <label class="flex items-center gap-2 text-sm text-text sm:col-span-2">
        <input type="checkbox" name="allow_insecure" value="true" />
        {t("uptime.field.allow_insecure")}
      </label>
      <div class="flex gap-2 sm:col-span-2">
        <Button type="submit" disabled={busy.active}>{t("common.save")}</Button>
        <Button variant="secondary" onclick={() => (adding = false)}>{t("common.cancel")}</Button>
      </div>
    </form>
  {:else}
    <div class="mt-4">
      <Button onclick={() => (adding = true)}>{t("uptime.settings.add")}</Button>
    </div>
  {/if}
</div>

<!-- The dialog owns the form: it posts `action` with `fields`, so there is no hidden form to
     keep in step and no second place that knows how a delete is submitted. -->
<ConfirmDialog
  bind:open={confirmDelete}
  title={t("uptime.settings.delete_title")}
  message={t("uptime.settings.delete_confirm", { name: deleteTarget?.name ?? "" })}
  action="?/remove"
  fields={{ id: deleteTarget?.id ?? "" }}
  confirmLabel={t("common.delete")}
/>
