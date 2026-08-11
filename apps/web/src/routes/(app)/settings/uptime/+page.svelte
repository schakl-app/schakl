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
  let addingProfile = $state(false);

  /** Field names as words. A drift that reads `interval_seconds` names a column, not a thing. */
  const fieldLabel = (f: string) =>
    ({
      name: t("uptime.field.name"),
      monitor_type: t("uptime.field.monitor_type"),
      target: t("uptime.field.target"),
      port: "Port",
      interval_seconds: t("uptime.field.interval"),
      retries: t("uptime.field.retries"),
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
            created: form.report.created,
            missing: form.report.missing,
          })
        : t(form.report.error ?? "errors.uptime_failed")}
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
              · {t("uptime.settings.monitor_count", { count: instance.monitor_count })}
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
                placeholder="CF-Access-Client-Id: ...&#10;CF-Access-Client-Secret: ..."
              ></textarea>
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
        <li class="rounded-xl border border-dashed border-border p-4 text-center text-sm text-muted">
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
          {t("uptime.settings.add")}
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
