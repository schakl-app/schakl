<script lang="ts">
  /**
   * The WordPress panel on a website's page (docs/WORDPRESS.md).
   *
   * Reads the **stored row**, never the client's site: a website page must not wait on somebody
   * else's WordPress to render (docs/PERFORMANCE.md), and the point of storing what we last
   * observed is that an unreachable site still leaves something true on the screen.
   *
   * Two things this panel exists to say, which a boolean "connected" could not:
   *
   * - **What the credential reaches, per surface.** Rank Math absent on a healthy site and MCP
   *   absent where Rank Math works are ordinary states, not faults, so each is its own line
   *   with its own verdict.
   * - **Why a ✗ is a ✗.** The site's own words ride beside every refusal, because
   *   `rest_no_route` (the plugin is not installed), `rest_forbidden` (this user is not an
   *   administrator) and `aiv_unauthorized` (Rank Math has no Content AI subscription) are
   *   three different jobs for three different people, and a bare cross is none of them.
   *
   * Every observed line carries *when* it was observed. A capability list with no timestamp
   * invites the reader to treat a month-old probe as current.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";

  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  type Site = {
    id: string;
    base_url: string;
    username: string;
    active: boolean;
    status: string;
    last_error: string | null;
    capabilities: Record<string, boolean>;
    capability_errors: Record<string, string>;
    capabilities_checked_at: string | null;
    mcp_server_path: string | null;
    rankmath_version: string | null;
    rankmath_ai_visibility: boolean;
    last_verified_at: string | null;
  };

  // `data: unknown` and narrow here, matching every other entity panel: the registry types the
  // component contract, and a narrower prop type makes the component unassignable to it.
  let { data }: { data: unknown } = $props();

  const form = $derived(page.form);
  // A write hands the fresh row back, so the panel updates without a `load` that would have to
  // go and look at the site again.
  const site = $derived(
    (form?.wpSite ?? (data as { site?: Site | null })?.site ?? null) as Site | null,
  );

  const canManage = $derived(can(page.data.user, "wordpress.site.manage"));

  let connecting = $state(false);
  let editing = $state(false);
  let confirmDisconnect = $state(false);
  const busy = new InFlight();

  /**
   * The surfaces one Application Password reaches, in the order they matter. `rest` first
   * because everything else is meaningless without it; `mcp` last because it is the one nothing
   * in schakl uses yet.
   */
  const SURFACES = ["rest", "admin", "abilities", "rankmath_aiv", "mcp"] as const;

  /**
   * Glyph + word, never colour alone: the dev tenant's brand colour is gold, so a coloured dot
   * on its own is unreadable as state. `undefined` is "not probed", which is emphatically not
   * "refused" — nobody having looked and having looked and been told no are different facts.
   */
  function look(value: boolean | undefined): { glyph: string; cls: string; key: string } {
    if (value === undefined)
      return { glyph: "○", cls: "text-muted", key: "wordpress.capability.unknown" };
    return value
      ? { glyph: "✓", cls: "text-emerald-600", key: "wordpress.capability.yes" }
      : { glyph: "✗", cls: "text-amber-700", key: "wordpress.capability.no" };
  }

  const statusKey = $derived(site ? `wordpress.status.${site.status}` : "");
</script>

{#if !site}
  <p class="text-sm text-muted">{t("wordpress.panel.empty")}</p>
  {#if canManage}
    {#if !connecting}
      <button
        type="button"
        class="mt-3 text-sm text-brand hover:underline"
        onclick={() => (connecting = true)}>{t("wordpress.connect")}</button
      >
    {:else}
      <form
        method="POST"
        action="?/wpConnect"
        class="mt-3 space-y-3"
        use:enhance={busy.wrap("connect", () => ({ result, update }) => {
          if (result.type === "success") connecting = false;
          // A new connection replaces the empty state, so the form is finished with — but the
          // page keeps its other fields, so `reset: false` (docs/UX.md, `pnpm forms:check`).
          void update({ reset: false });
        })}
      >
        <p class="text-xs text-muted">{t("wordpress.connect_help")}</p>
        <div>
          <label for="wp-url" class="mb-1 block text-sm text-text">
            {t("wordpress.field.base_url")}
          </label>
          <input
            id="wp-url"
            name="base_url"
            type="url"
            required
            placeholder="https://klant.nl"
            class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label for="wp-user" class="mb-1 block text-sm text-text">
            {t("wordpress.field.username")}
          </label>
          <input
            id="wp-user"
            name="username"
            required
            autocomplete="off"
            class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label for="wp-pw" class="mb-1 block text-sm text-text">
            {t("wordpress.field.app_password")}
          </label>
          <input
            id="wp-pw"
            name="app_password"
            type="password"
            required
            autocomplete="new-password"
            class="w-full rounded-lg border border-border bg-surface px-3 py-2 font-mono text-sm"
          />
          <p class="mt-1 text-xs text-muted">{t("wordpress.field.app_password_help")}</p>
        </div>
        {#if form?.wpError}
          <p class="text-sm text-red-600 dark:text-red-400">{t(form.wpError)}</p>
        {/if}
        <div class="flex justify-end gap-2">
          <button
            type="button"
            class="rounded-lg border border-border px-3 py-1.5 text-sm text-text"
            onclick={() => (connecting = false)}>{t("common.cancel")}</button
          >
          <Button loading={busy.is("connect")} disabled={busy.active}>
            {t("wordpress.connect")}
          </Button>
        </div>
      </form>
    {/if}
  {/if}
{:else}
  <dl class="space-y-2 text-sm">
    <div class="flex justify-between gap-4">
      <dt class="text-muted">{t("wordpress.field.base_url")}</dt>
      <dd class="truncate">
        <a href={site.base_url} target="_blank" rel="noreferrer" class="text-brand hover:underline">
          {site.base_url}
        </a>
      </dd>
    </div>
    <div class="flex justify-between gap-4">
      <dt class="text-muted">{t("wordpress.field.username")}</dt>
      <dd class="truncate text-text">{site.username}</dd>
    </div>
    <div class="flex justify-between gap-4">
      <dt class="text-muted">{t("wordpress.field.status")}</dt>
      <dd class="text-text">
        {t(statusKey)}
        {#if site.last_error}
          <span class="text-amber-700"> · {t(`wordpress.issue.${site.last_error}`)}</span>
        {/if}
      </dd>
    </div>
    {#if site.rankmath_version}
      <div class="flex justify-between gap-4">
        <dt class="text-muted">{t("wordpress.field.rankmath")}</dt>
        <dd class="text-text">
          {site.rankmath_version}
          {#if !site.rankmath_ai_visibility}
            <span class="text-amber-700"> · {t("wordpress.rankmath_too_old")}</span>
          {/if}
        </dd>
      </div>
    {/if}
    {#if site.mcp_server_path}
      <div class="flex justify-between gap-4">
        <dt class="text-muted">{t("wordpress.field.mcp_path")}</dt>
        <dd class="truncate font-mono text-xs text-text">{site.mcp_server_path}</dd>
      </div>
    {/if}
  </dl>

  <!-- What the credential was observed to reach, per surface, with the site's own words for
       every refusal. -->
  <div class="mt-4 border-t border-border pt-3">
    <div class="mb-2 flex items-baseline justify-between gap-3">
      <h3 class="text-sm font-medium text-text">{t("wordpress.capabilities")}</h3>
      <span class="text-xs text-muted">
        {site.capabilities_checked_at
          ? t("wordpress.checked_at", { at: fmtDateTime(site.capabilities_checked_at) })
          : t("wordpress.never_checked")}
      </span>
    </div>
    <ul class="space-y-1">
      {#each SURFACES as key (key)}
        {@const l = look(site.capabilities[key])}
        <li class="text-sm">
          <div class="flex items-center gap-2">
            <span class={l.cls} aria-hidden="true">{l.glyph}</span>
            <span class="text-text">{t(`wordpress.surface.${key}`)}</span>
            <span class="sr-only">{t(l.key)}</span>
          </div>
          {#if site.capability_errors[key]}
            <!-- The site's own text, untranslated on purpose: it is a quote, and translating a
                 quote is how a diagnosis stops matching the log line an admin is reading. -->
            <p class="ml-6 truncate font-mono text-xs text-muted" title={site.capability_errors[key]}>
              {site.capability_errors[key]}
            </p>
          {/if}
        </li>
      {/each}
    </ul>
  </div>

  {#if canManage}
    <div class="mt-4 flex flex-wrap items-center gap-2 border-t border-border pt-3">
      <form
        method="POST"
        action="?/wpVerify"
        use:enhance={busy.wrap("verify", () => ({ update }) => {
          // Stay on the record: the verdict lands in the panel above.
          void update({ reset: false });
        })}
      >
        <input type="hidden" name="site_id" value={site.id} />
        <Button loading={busy.is("verify")} disabled={busy.active} variant="secondary">
          {t("wordpress.verify")}
        </Button>
      </form>
      <button
        type="button"
        class="text-sm text-brand hover:underline"
        onclick={() => (editing = !editing)}
      >
        {editing ? t("common.cancel") : t("wordpress.rotate")}
      </button>
      <button
        type="button"
        class="ml-auto text-sm text-red-600 hover:underline dark:text-red-400"
        onclick={() => (confirmDisconnect = true)}
      >
        {t("wordpress.disconnect")}
      </button>
    </div>

    {#if editing}
      <form
        method="POST"
        action="?/wpUpdate"
        class="mt-3 space-y-3 border-t border-border pt-3"
        use:enhance={busy.wrap("save", () => ({ result, update }) => {
          if (result.type === "success") editing = false;
          // Keep what was just saved on screen (docs/UX.md).
          void update({ reset: false });
        })}
      >
        <input type="hidden" name="site_id" value={site.id} />
        <div>
          <label for="wp-url-edit" class="mb-1 block text-sm text-text">
            {t("wordpress.field.base_url")}
          </label>
          <input
            id="wp-url-edit"
            name="base_url"
            type="url"
            value={site.base_url}
            class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label for="wp-user-edit" class="mb-1 block text-sm text-text">
            {t("wordpress.field.username")}
          </label>
          <input
            id="wp-user-edit"
            name="username"
            value={site.username}
            autocomplete="off"
            class="w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label for="wp-pw-edit" class="mb-1 block text-sm text-text">
            {t("wordpress.field.app_password")}
          </label>
          <input
            id="wp-pw-edit"
            name="app_password"
            type="password"
            autocomplete="new-password"
            placeholder={t("wordpress.field.app_password_keep")}
            class="w-full rounded-lg border border-border bg-surface px-3 py-2 font-mono text-sm"
          />
          <p class="mt-1 text-xs text-muted">{t("wordpress.rotate_help")}</p>
        </div>
        <label class="flex items-center gap-2 text-sm text-text">
          <input type="checkbox" name="active" value="true" checked={site.active} />
          {t("wordpress.field.active")}
        </label>
        {#if form?.wpError}
          <p class="text-sm text-red-600 dark:text-red-400">{t(form.wpError)}</p>
        {/if}
        <div class="flex justify-end">
          <Button loading={busy.is("save")} disabled={busy.active}>{t("common.save")}</Button>
        </div>
      </form>
    {/if}
  {/if}

  <ConfirmDialog
    bind:open={confirmDisconnect}
    title={t("wordpress.disconnect")}
    message={t("wordpress.disconnect_confirm")}
    action="?/wpDisconnect"
    fields={{ site_id: site.id }}
    confirmLabel={t("wordpress.disconnect")}
  />
{/if}
