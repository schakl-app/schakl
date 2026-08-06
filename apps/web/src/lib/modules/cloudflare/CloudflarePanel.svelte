<script lang="ts">
  /**
   * The Cloudflare panel on a domain's detail page (epic #278).
   *
   * Two states, and the difference matters: what the page loads is what schakl *stored* — no
   * Cloudflare call, so opening a domain is as fast as it was and still works when Cloudflare
   * is down (docs/PERFORMANCE.md). "Controleren bij Cloudflare" is the explicit action that
   * goes and looks, and it is the only thing that can answer *"this already redirects, but not
   * through us"*: a forwarding Page Rule, a redirect rule above ours, an apex with no proxied
   * record so the rule never fires.
   *
   * Every write control gates on the API's own key (docs/UX.md, the client-portal entry) —
   * `cloudflare.zone.manage`, base key. A domain page is client-reachable through the portal,
   * and none of this is a client's to touch.
   *
   * The zone decides what *this* Cloudflare account can be asked about the domain, so the
   * redirect and the DNS table live behind it. **Pages does not**: a custom hostname is
   * registered on a project, and the project names its own account (`docs/CLOUDFLARE.md` §6).
   * It therefore renders whether or not the domain is connected here.
   *
   * **Host contract:** `?/cfConnect`, `?/cfCheck`, `?/cfSaveRedirect`, `?/cfRemoveRedirect`,
   * `?/cfLinkPages`, `?/cfUnlinkPages` plus the DNS actions used by `CloudflareDns` (spread
   * `cloudflareActions`).
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  import CloudflareDns from "./CloudflareDns.svelte";
  import type { AccountOption, DomainStatus, PagesProject } from "./types";

  // `context` (the domain id) is part of the panel contract but unread here: every action is
  // the host page's own, so the domain comes from the route, never from a hidden field.
  let { data }: { data: unknown } = $props();

  const panel = $derived(
    (data ?? { status: null, projects: [], accounts: [] }) as {
      status: DomainStatus | null;
      projects: PagesProject[];
      accounts: AccountOption[];
    },
  );

  // A check returns its report to the page rather than through `load` (the load is deliberately
  // Cloudflare-free), so the freshest answer wins while the page lives.
  const live = $derived((page.form?.cfStatus ?? null) as DomainStatus | null);
  const status = $derived(live ?? panel.status);
  const canManage = $derived(can(page.data.user, "cloudflare.zone.manage"));

  const busy = new InFlight();
  let confirmRemoveRedirect = $state(false);
  let confirmUnlink = $state(false);
  let unlinkTarget = $state<{ id: string; hostname: string; project: string } | null>(null);

  const activeAccounts = $derived(panel.accounts.filter((a) => a.active));
  const zone = $derived(status?.zone ?? null);
  const redirect = $derived(status?.redirect ?? null);
  const issues = $derived(status?.issues ?? []);

  // What the page draws is what schakl stored, so the one thing it cannot leave unsaid is how
  // old that is: "no conflicts" from a check that ran in March is not the same sentence as
  // "no conflicts" from one that ran a minute ago, and without a date they read identically.
  // It sits with the button that changes it, in both branches that have one.
  const checked = $derived(
    status?.checked_at
      ? t("cloudflare.panel.checked_at", { when: fmtDateTime(status.checked_at) })
      : t("cloudflare.panel.never_checked"),
  );

  const inputClass =
    "w-full min-w-0 rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const labelClass = "mb-1 block text-sm font-medium text-text";

  /** Cloudflare's own zone vocabulary, translated where we know it; raw where we don't. */
  function zoneStatus(value: string): string {
    const key = `cloudflare.zone_status.${value}`;
    const label = t(key);
    return label === key ? value : label;
  }

  const statusCodes = [301, 302, 307, 308];

  // Form state, seeded from the stored redirect so editing starts from what is live.
  let target = $state("");
  let statusCode = $state(301);
  let preservePath = $state(true);
  let preserveQuery = $state(true);
  let includeSubdomains = $state(true);
  let seeded = $state<string | null>(null);
  $effect(() => {
    const key = redirect?.id ?? "none";
    if (seeded === key) return;
    seeded = key;
    target = redirect?.target_url ?? "";
    statusCode = redirect?.status_code ?? 301;
    preservePath = redirect?.preserve_path ?? true;
    preserveQuery = redirect?.preserve_query ?? true;
    includeSubdomains = redirect?.include_subdomains ?? true;
  });
</script>

{#if page.form?.cfError}
  <p class="mb-3 text-sm text-red-600 dark:text-red-400">{t(page.form.cfError)}</p>
{/if}

{#if !zone}
  <!-- Not connected. -->
  <p class="text-sm text-text-muted">{t("cloudflare.panel.not_connected")}</p>
  {#if canManage}
    {#if activeAccounts.length === 0}
      <p class="mt-2 text-sm text-text-muted">{t("cloudflare.issue.no_account")}</p>
    {:else}
      <form
        method="POST"
        action="?/cfConnect"
        use:enhance={busy.clear("connect")}
        class="mt-3 space-y-3"
      >
        {#if activeAccounts.length > 1}
          <div class="max-w-sm">
            <label class={labelClass} for="cf-account">{t("cloudflare.panel.account")}</label>
            <select id="cf-account" name="account_id" class={inputClass}>
              {#each activeAccounts as account (account.id)}
                <option value={account.id}>{account.name}</option>
              {/each}
            </select>
          </div>
        {/if}
        <label class="flex items-center gap-2 text-sm text-text">
          <input type="checkbox" name="adopt_only" class="rounded border-border" />
          {t("cloudflare.panel.adopt_only")}
        </label>
        <p class="text-xs text-text-muted">{t("cloudflare.panel.connect_help")}</p>
        <Button type="submit" loading={busy.is("connect")} disabled={busy.active}>
          {t("cloudflare.panel.connect")}
        </Button>
      </form>
    {/if}
  {/if}
{:else}
  <!-- Connected: the zone, its delegation, and what a check last found. -->
  <div class="flex flex-wrap items-baseline justify-between gap-2">
    <div class="min-w-0">
      <p class="truncate text-sm font-medium text-text">{zone.name}</p>
      <p class="text-xs text-text-muted">
        {zone.account_name ?? ""} · {zoneStatus(zone.status)}
      </p>
    </div>
    <div class="flex flex-none flex-col items-end gap-1">
      <form method="POST" action="?/cfCheck" use:enhance={busy.wrap("check")}>
        <Button variant="secondary" size="xs" loading={busy.is("check")} disabled={busy.active}>
          {t("cloudflare.panel.check")}
        </Button>
      </form>
      <p class="text-xs text-text-muted">{checked}</p>
    </div>
  </div>

  <dl class="mt-3 grid gap-3 text-sm sm:grid-cols-2">
    <div class="min-w-0">
      <dt class="text-xs text-text-muted">{t("cloudflare.panel.expected_nameservers")}</dt>
      <dd class="break-words text-text">
        {status?.expected_nameservers?.join(", ") || "—"}
      </dd>
    </div>
    <div class="min-w-0">
      <dt class="text-xs text-text-muted">{t("cloudflare.panel.observed_nameservers")}</dt>
      <dd class="break-words text-text">
        {status?.observed_nameservers?.join(", ") || "—"}
      </dd>
    </div>
  </dl>
  <p class="mt-1 text-xs {status?.nameservers_delegated ? 'text-text-muted' : 'text-amber-600'}">
    {status?.nameservers_delegated
      ? t("cloudflare.panel.delegated")
      : t("cloudflare.panel.not_delegated")}
  </p>

  {#if issues.length > 0}
    <div class="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:bg-amber-950/30">
      <p class="mb-1 text-xs font-medium text-text">{t("cloudflare.issues.title")}</p>
      <ul class="list-inside list-disc space-y-1 text-sm text-text">
        {#each issues as issue (issue)}
          <li>{t(`cloudflare.issue.${issue}`)}</li>
        {/each}
      </ul>
      {#if status?.unavailable?.length}
        <p class="mt-2 text-xs text-text-muted">
          {t("cloudflare.unavailable.title", {
            items: status.unavailable.map((k) => t(`cloudflare.unavailable.${k}`)).join(", "),
          })}
        </p>
      {/if}
    </div>
  {/if}

  {#if status?.conflicts?.length}
    <div class="mt-4">
      <p class="text-xs font-medium text-text">{t("cloudflare.conflicts.title")}</p>
      <p class="mb-1 text-xs text-text-muted">{t("cloudflare.conflicts.intro")}</p>
      <ul class="space-y-1 text-sm text-text">
        {#each status.conflicts as conflict, i (i)}
          <li class="min-w-0 break-words">
            <span class="text-text-muted">{t(`cloudflare.conflicts.${conflict.kind}`)}:</span>
            {conflict.description || conflict.detail || "—"}
          </li>
        {/each}
      </ul>
    </div>
  {/if}

  <!-- Domain-wide redirect ------------------------------------------------------------- -->
  <section class="mt-5 border-t border-border pt-4">
    <div class="flex flex-wrap items-baseline justify-between gap-2">
      <h3 class="text-sm font-medium text-text">{t("cloudflare.redirect.title")}</h3>
      {#if redirect}
        <span class="text-xs text-text-muted">
          {t(`cloudflare.redirect_status.${redirect.last_status}`)}
        </span>
      {/if}
    </div>

    {#if redirect && status?.redirect_live?.differences?.length}
      <p class="mt-2 text-sm text-amber-600">
        {t("cloudflare.redirect.drift_explain", {
          fields: status.redirect_live.differences.join(", "),
        })}
      </p>
      {#if status.redirect_live.target}
        <p class="mt-1 break-words text-xs text-text-muted">
          {t("cloudflare.redirect.live_target")}: {status.redirect_live.target}
        </p>
      {/if}
    {/if}

    {#if !canManage}
      <p class="mt-2 text-sm text-text">
        {redirect ? redirect.target_url : t("cloudflare.redirect.none")}
      </p>
    {:else}
      <form
        method="POST"
        action="?/cfSaveRedirect"
        use:enhance={busy.keep("redirect")}
        class="mt-3 space-y-3"
      >
        <div class="grid gap-3 sm:grid-cols-2">
          <div class="min-w-0">
            <label class={labelClass} for="cf-target">{t("cloudflare.redirect.target")}</label>
            <input
              id="cf-target"
              name="target_url"
              bind:value={target}
              placeholder={t("cloudflare.redirect.target_placeholder")}
              class={inputClass}
            />
          </div>
          <div class="min-w-0">
            <label class={labelClass} for="cf-code">{t("cloudflare.redirect.status_code")}</label>
            <select id="cf-code" name="status_code" bind:value={statusCode} class={inputClass}>
              {#each statusCodes as code (code)}
                <option value={code}>{t(`cloudflare.redirect.code_${code}`)}</option>
              {/each}
            </select>
            {#if statusCode === 301 || statusCode === 308}
              <p class="mt-1 text-xs text-text-muted">
                {t("cloudflare.redirect.code_permanent_warning")}
              </p>
            {/if}
          </div>
        </div>

        <label class="flex items-start gap-2 text-sm text-text">
          <input
            type="checkbox"
            name="preserve_path"
            bind:checked={preservePath}
            class="mt-0.5 rounded border-border"
          />
          <span>
            {t("cloudflare.redirect.preserve_path")}
            <span class="block text-xs text-text-muted">
              {t("cloudflare.redirect.preserve_path_help")}
            </span>
          </span>
        </label>
        <label class="flex items-center gap-2 text-sm text-text">
          <input
            type="checkbox"
            name="preserve_query"
            bind:checked={preserveQuery}
            class="rounded border-border"
          />
          {t("cloudflare.redirect.preserve_query")}
        </label>
        <label class="flex items-center gap-2 text-sm text-text">
          <input
            type="checkbox"
            name="include_subdomains"
            bind:checked={includeSubdomains}
            class="rounded border-border"
          />
          {t("cloudflare.redirect.include_subdomains")}
        </label>
        <label class="flex items-start gap-2 text-sm text-text">
          <input
            type="checkbox"
            name="ensure_origin"
            checked
            class="mt-0.5 rounded border-border"
          />
          <span>
            {t("cloudflare.redirect.ensure_origin")}
            <span class="block text-xs text-text-muted">
              {t("cloudflare.redirect.ensure_origin_help")}
            </span>
          </span>
        </label>

        <div class="flex flex-wrap items-center gap-2">
          <Button type="submit" loading={busy.is("redirect")} disabled={busy.active}>
            {t("cloudflare.redirect.save")}
          </Button>
          {#if redirect}
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onclick={() => (confirmRemoveRedirect = true)}
            >
              {t("cloudflare.redirect.remove")}
            </Button>
          {/if}
        </div>
      </form>
    {/if}
  </section>

  <!-- DNS ------------------------------------------------------------------------------- -->
  <CloudflareDns zoneId={zone.id} zoneName={zone.name} {canManage} />
{/if}

<!-- Cloudflare Pages -----------------------------------------------------------------------
     Outside the zone branch on purpose. A Pages custom hostname is registered on a *project*,
     which is an account-level thing: the API resolves the account from the project and only
     writes the CNAME when this domain happens to have a zone here. Drawn inside the connected
     branch, the feature read as "you cannot serve this domain from Pages" for every domain
     whose DNS lives elsewhere — and hid the links of a domain whose zone was later unlinked,
     leaving rows nothing on this page could remove. -->
{#if zone || status?.pages_links?.length || (canManage && panel.projects.length > 0)}
  <section class="mt-5 border-t border-border pt-4">
    <div class="flex flex-wrap items-baseline justify-between gap-2">
      <h3 class="text-sm font-medium text-text">{t("cloudflare.pages.title")}</h3>
      <!-- The zone branch has its own "check" button, and a domain whose DNS lives elsewhere
           is not inside it — so without this the one case Pages exists for could never
           refresh. The action is the same one; only the button is duplicated. -->
      {#if !zone && status?.pages_links?.length}
        <div class="flex flex-none flex-col items-end gap-1">
          <form method="POST" action="?/cfCheck" use:enhance={busy.wrap("check")}>
            <Button variant="secondary" size="xs" loading={busy.is("check")} disabled={busy.active}>
              {t("cloudflare.pages.check")}
            </Button>
          </form>
          <p class="text-xs text-text-muted">{checked}</p>
        </div>
      {/if}
    </div>
    {#if status?.pages_links?.length}
      <ul class="mt-2 space-y-1 text-sm">
        {#each status.pages_links as link (link.id)}
          <li class="flex flex-wrap items-center justify-between gap-2">
            <span class="min-w-0 break-words text-text">
              {link.hostname}
              <span class="text-text-muted">→ {link.project_name ?? ""}</span>
              {#if link.status}<span class="text-xs text-text-muted">({link.status})</span>{/if}
              <!-- Drift, and where the row came from. A link the sync adopted is one nobody
                   here created, so saying so is the difference between "who added this?" and
                   a row that looks like somebody's mistake. -->
              {#if link.missing_at}
                <span class="block text-xs text-amber-600">
                  {t("cloudflare.pages.missing", { when: fmtDateTime(link.missing_at) })}
                </span>
              {:else if link.discovered_at}
                <span class="block text-xs text-text-muted">
                  {t("cloudflare.pages.discovered")}
                </span>
              {/if}
            </span>
            {#if canManage}
              <Button
                type="button"
                variant="secondary"
                size="xs"
                onclick={() => {
                  unlinkTarget = {
                    id: link.id,
                    hostname: link.hostname,
                    project: link.project_name ?? "",
                  };
                  confirmUnlink = true;
                }}
              >
                {t("cloudflare.pages.unlink")}
              </Button>
            {/if}
          </li>
        {/each}
      </ul>
    {:else}
      <p class="mt-2 text-sm text-text-muted">{t("cloudflare.pages.empty")}</p>
    {/if}

    <!-- The issues box lives inside the connected branch, so a domain with no zone would get no
         word at all that the refresh could not run. A check that silently did nothing reads as
         "everything is fine", which is the one thing it does not know. -->
    {#if status?.unavailable?.includes("pages")}
      <p class="mt-2 text-xs text-amber-600">
        {t("cloudflare.unavailable.title", { items: t("cloudflare.unavailable.pages") })}
      </p>
    {/if}

    {#if !zone}
      <p class="mt-2 text-xs text-text-muted">{t("cloudflare.pages.no_zone_hint")}</p>
    {/if}

    {#if canManage}
      {#if panel.projects.length === 0}
        <p class="mt-2 text-xs text-text-muted">{t("cloudflare.pages.no_projects")}</p>
      {:else}
        <form
          method="POST"
          action="?/cfLinkPages"
          use:enhance={busy.clear("pages")}
          class="mt-3 flex flex-wrap items-end gap-2"
        >
          <div class="min-w-0 flex-1">
            <label class={labelClass} for="cf-project">{t("cloudflare.pages.project")}</label>
            <select id="cf-project" name="project_id" class={inputClass}>
              {#each panel.projects as project (project.id)}
                <!-- The account is named only where the tenant has more than one: two accounts
                     may each hold a project called "site", and the account is what decides
                     which Cloudflare this hostname is registered at. -->
                <option value={project.id}>
                  {panel.accounts.length > 1 && project.account_name
                    ? `${project.name} · ${project.account_name}`
                    : project.name}
                </option>
              {/each}
            </select>
          </div>
          <div class="min-w-0 flex-1">
            <label class={labelClass} for="cf-hostname">{t("cloudflare.pages.hostname")}</label>
            <input
              id="cf-hostname"
              name="hostname"
              placeholder={status?.domain_name ?? ""}
              class={inputClass}
            />
          </div>
          <Button
            type="submit"
            variant="secondary"
            loading={busy.is("pages")}
            disabled={busy.active}
          >
            {t("cloudflare.pages.link")}
          </Button>
        </form>
        <p class="mt-1 text-xs text-text-muted">{t("cloudflare.pages.hostname_help")}</p>
      {/if}
    {/if}
  </section>
{/if}

<ConfirmDialog
  bind:open={confirmRemoveRedirect}
  title={t("cloudflare.redirect.remove")}
  message={t("cloudflare.redirect.remove_confirm", { target: redirect?.target_url ?? "" })}
  action="?/cfRemoveRedirect"
/>

<ConfirmDialog
  bind:open={confirmUnlink}
  title={t("cloudflare.pages.unlink")}
  message={t("cloudflare.pages.unlink_confirm", {
    hostname: unlinkTarget?.hostname ?? "",
    project: unlinkTarget?.project ?? "",
  })}
  action="?/cfUnlinkPages"
  fields={{ link_id: unlinkTarget?.id ?? "" }}
/>
