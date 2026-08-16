<script lang="ts">
  /**
   * The registrar panel on a domain's detail page (issue #296).
   *
   * Two states, and the difference matters: what the page loads is what schakl *stored* — no
   * OXXA call, so opening a domain is as fast as it was and still works when the registrar is
   * down (docs/PERFORMANCE.md). "Ververs" is the explicit action that goes and looks, and it is
   * the only thing that can answer *"the registry says something else than we last pushed"*.
   *
   * Every write control gates on the API's own key (docs/UX.md, the client-portal entry) —
   * `oxxa.registrar.sync` for the refresh, `oxxa.registrar.manage` for the push, base keys. A
   * domain page is client-reachable through the portal, and none of this is a client's to touch.
   *
   * **The one composition worth having lives here.** Connecting a domain to Cloudflare produces
   * a nameserver pair, and moving the domain onto it is a *different* call at a *different*
   * provider — the API keeps the two apart on purpose (see `push_nameservers`' docstring: no
   * shared transaction, no cross-module import, and the push must stay retryable). The web layer
   * is the one place that may legitimately see both, so the push box is pre-filled from whatever
   * the Cloudflare panel already put in this page's data. No extra API call: it is data the page
   * is holding either way, and a domain without the Cloudflare module simply has none.
   *
   * **Host contract:** `?/oxxaRefresh` and `?/oxxaPush` (spread `oxxaActions` into the page's
   * `actions`).
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { fmtDateTime, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  import {
    MAX_NAMESERVERS,
    MIN_NAMESERVERS,
    parseNameservers,
    sameNameservers,
    type NameserverPushResult,
    type OxxaPanelData,
    type RegistrarStatus,
  } from "./types";

  // `context` (the domain id) is part of the panel contract but unread here: every action is
  // the host page's own, so the domain comes from the route, never from a hidden field.
  let { data }: { data: unknown } = $props();

  const panel = $derived((data ?? { status: null, accounts: [] }) as OxxaPanelData);

  // A refresh returns its report to the page rather than through `load` (the load is deliberately
  // OXXA-free), so the freshest answer wins while the page lives.
  const live = $derived((page.form?.oxxaStatus ?? null) as RegistrarStatus | null);
  const status = $derived(live ?? panel.status);
  const pushed = $derived((page.form?.oxxaPush ?? null) as NameserverPushResult | null);

  const canSync = $derived(can(page.data.user, "oxxa.registrar.sync"));
  const canManage = $derived(can(page.data.user, "oxxa.registrar.manage"));

  const busy = new InFlight();

  const row = $derived(status?.registrar ?? null);
  const issues = $derived(status?.issues ?? []);
  const accounts = $derived(panel.accounts.filter((a) => a.active));

  const inputClass =
    "w-full min-w-0 rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const labelClass = "mb-1 block text-sm font-medium text-text";

  /** A tri-state flag the registrar may simply not have told us about yet. */
  function flag(value: boolean | null | undefined): string {
    if (value === null || value === undefined) return t("oxxa.panel.unknown");
    return value ? t("oxxa.panel.yes") : t("oxxa.panel.no");
  }

  /**
   * What the Cloudflare panel put in this page's data, if that module is enabled and this domain
   * has a zone. Read structurally rather than by importing cloudflare's types: this module may
   * not know that module's internals (CLAUDE.md §6), and an absent panel must read as "no
   * suggestion", never as a broken page.
   */
  const cloudflareNameservers = $derived.by(() => {
    const panels = ((page.data as { panels?: { key: string; data?: unknown }[] }).panels ?? []) as {
      key: string;
      data?: unknown;
    }[];
    const cf = panels.find((p) => p.key === "cloudflare.domain")?.data as
      { status?: { expected_nameservers?: string[] | null } | null } | undefined;
    const hosts = cf?.status?.expected_nameservers ?? [];
    return hosts.length >= MIN_NAMESERVERS ? hosts : [];
  });

  /** What the delegation is (or was last asked to be), for the "nothing to suggest" case. */
  const currentNameservers = $derived(row?.ns_desired ?? row?.ns_observed ?? []);

  /**
   * **Whether there is anything to change at all.** The register's own answer (`ns_observed`) is
   * the one that decides it — `ns_desired` is what we last *asked* for, and asking is not being.
   *
   * Without this the panel opened a form headed "Nameservers wijzigen bij OXXA", pre-filled with
   * Cloudflare's pair, over a register that was already holding exactly that pair: an outstanding
   * action where there was none, on the most common finished state this integration has (a zone
   * adopted from a client who was already on Cloudflare, or a push that worked weeks ago).
   *
   * The three push problems are the exception and they must keep the form in front of the user:
   * `drift` means the register has since been edited elsewhere, `missing` that the group backing
   * these nameservers is gone at OXXA, `error` that the last attempt failed. In each of those the
   * delegation can read correct and still need re-sending.
   */
  const pushProblem = $derived(
    row?.ns_push_status === "drift" ||
      row?.ns_push_status === "missing" ||
      row?.ns_push_status === "error",
  );
  const alreadyOnCloudflare = $derived(
    !pushProblem && sameNameservers(row?.ns_observed, cloudflareNameservers),
  );

  // The push box, seeded once per domain-state change and then left alone — the user is editing
  // it. Cloudflare's pair wins when there is one: pushing it is the whole reason this box exists,
  // and a push that matches the live delegation is a no-op at the registrar anyway.
  let nameservers = $state("");
  let seeded = $state<string | null>(null);
  const seedFromCloudflare = $derived(cloudflareNameservers.length > 0);
  $effect(() => {
    const seed = seedFromCloudflare ? cloudflareNameservers : currentNameservers;
    const key = `${row?.id ?? "none"}:${seed.join(",")}`;
    if (seeded === key) return;
    seeded = key;
    nameservers = seed.join("\n");
  });

  // Opened by hand when the delegation is already right: the ability to push something else must
  // survive "there is nothing to do", because `missing` and `drift` are not the only reasons an
  // agency re-points a domain — they may simply be moving it off Cloudflare.
  let editAnyway = $state(false);
  const showPushForm = $derived(!alreadyOnCloudflare || editAnyway);

  const pushCount = $derived(parseNameservers(nameservers).length);
  const pushValid = $derived(pushCount >= MIN_NAMESERVERS && pushCount <= MAX_NAMESERVERS);
  // A control that would write what is already in the box does nothing, so it is not drawn
  // (#253: a control that always refuses is a broken control).
  const showFromCloudflare = $derived(
    seedFromCloudflare && !sameNameservers(parseNameservers(nameservers), cloudflareNameservers),
  );

  /**
   * With one active register the API resolves it itself; with several it refuses to pick
   * (`errors.oxxa_account_ambiguous`), so the panel has to name one. The `<select>`s below carry
   * no binding on purpose — a browser selects the first option, which is a real answer, where a
   * `""` binding would post "no account" and get the refusal the picker exists to avoid.
   */
  const soleAccount = $derived(accounts.length === 1 ? accounts[0].id : "");
</script>

{#if page.form?.oxxaError}
  <p class="mb-3 text-sm text-red-600 dark:text-red-400">{t(page.form.oxxaError)}</p>
{/if}
{#if pushed}
  <!-- A refused push answers 200 with `ok: false` and an i18n key: the API persists what was
       asked for and why it failed rather than raising, which would discard that row. So the
       result decides the colour here — a 200 is not a success on its own. -->
  {#if pushed.ok}
    <p class="mb-1 text-sm text-green-600">
      {pushed.changed ? t("oxxa.push.success") : t("oxxa.push.unchanged")}
    </p>
  {:else}
    <p class="mb-1 text-sm text-red-600 dark:text-red-400">
      {t(pushed.error || "errors.oxxa_request_failed")}
    </p>
  {/if}
  {#if pushed.nameservers?.length}
    <p class="mb-3 break-words text-xs text-text-muted">{pushed.nameservers.join(", ")}</p>
  {/if}
{/if}

{#if !status}
  <!-- The status call answered nothing, which on a rendered page means it 403'd: the panel's
       `load` reads on `oxxa.registrar.sync` and a member without it gets `null`. Saying "no
       account is configured, add one under Instellingen" here would be a lie *and* a dead end —
       the settings screen is a permission they do not hold either. -->
  <p class="text-sm text-text-muted">{t("oxxa.panel.no_access")}</p>
{:else if !status.configured}
  <!-- No usable credential anywhere in the org: nothing here can say anything yet. -->
  <p class="text-sm text-text-muted">{t("oxxa.panel.no_account")}</p>
{:else if !row}
  <!-- Configured, but this domain is in no synced register — the ordinary state for a domain
       registered somewhere else entirely. -->
  <p class="text-sm text-text-muted">{t("oxxa.panel.not_in_register")}</p>
  {#if canSync}
    <form method="POST" action="?/oxxaRefresh" use:enhance={busy.keep("refresh")} class="mt-3">
      {#if accounts.length > 1}
        <div class="mb-3 max-w-sm">
          <label class={labelClass} for="oxxa-account-empty">{t("oxxa.title")}</label>
          <select id="oxxa-account-empty" name="account_id" class={inputClass}>
            {#each accounts as account (account.id)}
              <option value={account.id}>{account.name}</option>
            {/each}
          </select>
        </div>
      {:else}
        <input type="hidden" name="account_id" value={soleAccount} />
      {/if}
      <Button variant="secondary" size="sm" loading={busy.is("refresh")} disabled={busy.active}>
        {t("oxxa.panel.refresh")}
      </Button>
    </form>
  {/if}
{:else}
  <!-- In the register: what it says, and when we last asked. -->
  <div class="flex flex-wrap items-baseline justify-between gap-2">
    <div class="min-w-0">
      <p class="truncate text-sm font-medium text-text">{row.name}</p>
      <p class="text-xs text-text-muted">
        {status.account_name ?? ""}
        {#if row.last_synced_at}· {fmtDateTime(row.last_synced_at)}{/if}
      </p>
    </div>
    {#if canSync}
      <form method="POST" action="?/oxxaRefresh" use:enhance={busy.keep("refresh")}>
        <input type="hidden" name="account_id" value={soleAccount} />
        <Button variant="secondary" size="xs" loading={busy.is("refresh")} disabled={busy.active}>
          {t("oxxa.panel.refresh")}
        </Button>
      </form>
    {/if}
  </div>

  {#if row.last_error}
    <!-- OXXA's own words, verbatim: the API stores them untranslated because they are the only
         thing that says *why* it refused. The issue list above already names the situation. -->
    <p class="mt-2 break-words text-xs text-red-600">{row.last_error}</p>
  {/if}

  <dl class="mt-3 grid gap-3 text-sm sm:grid-cols-2">
    <div class="min-w-0">
      <dt class="text-xs text-text-muted">{t("oxxa.panel.expires")}</dt>
      <dd class="text-text">{row.expires_on ? fmtNumericDate(row.expires_on) : "—"}</dd>
    </div>
    <div class="min-w-0">
      <dt class="text-xs text-text-muted">{t("oxxa.panel.registrant")}</dt>
      <dd class="break-words text-text">{row.registrant_name || "—"}</dd>
    </div>
    <div class="min-w-0">
      <dt class="text-xs text-text-muted">{t("oxxa.panel.transfer_lock")}</dt>
      <dd class="text-text">{flag(row.transfer_lock)}</dd>
    </div>
    <div class="min-w-0">
      <dt class="text-xs text-text-muted">{t("oxxa.panel.autorenew")}</dt>
      <dd class="text-text">{flag(row.autorenew)}</dd>
    </div>
    <div class="min-w-0">
      <dt class="text-xs text-text-muted">{t("oxxa.panel.dnssec")}</dt>
      <dd class="text-text">{flag(row.dnssec)}</dd>
    </div>
    <div class="min-w-0">
      <dt class="text-xs text-text-muted">{t("oxxa.panel.nsgroup")}</dt>
      <dd class="break-words text-text">{row.nsgroup_ref || "—"}</dd>
    </div>
    <div class="min-w-0 sm:col-span-2">
      <dt class="text-xs text-text-muted">{t("oxxa.panel.nameservers")}</dt>
      <dd class="break-words text-text">{row.ns_observed?.join(", ") || "—"}</dd>
      {#if alreadyOnCloudflare}
        <!-- Said where the nameservers are, not down in the push section: this is a fact about
             the delegation on screen, and it is what makes the missing form below make sense. -->
        <dd class="mt-1 text-xs text-text-muted">{t("oxxa.panel.already_cloudflare")}</dd>
      {/if}
    </div>
  </dl>

  {#if row.ns_pushed_at}
    <!-- A whole sentence ("Verstuurd {when}"), so it is a line and not a label/value pair. -->
    <p class="mt-2 text-xs text-text-muted">
      {t("oxxa.panel.pushed_at", { when: fmtDateTime(row.ns_pushed_at) })}
    </p>
  {/if}

  {#if issues.length > 0}
    <div class="mt-4 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:bg-amber-950/30">
      <ul class="list-inside list-disc space-y-1 text-sm text-text">
        {#each issues as issue (issue)}
          <li>{t(`oxxa.issue.${issue}`)}</li>
        {/each}
      </ul>
    </div>
  {/if}

  <!-- Push the delegation ---------------------------------------------------------------- -->
  {#if canManage}
    <section class="mt-5 border-t border-border pt-4">
      <h3 class="text-sm font-medium text-text">{t("oxxa.push.title")}</h3>
      {#if !showPushForm}
        <!-- Nothing to change: the register already holds Cloudflare's pair. The section stays
             (it is where this lives, and hiding it would make the panel's shape depend on state
             nobody can see), but it states the outcome instead of asking for the action. -->
        <p class="mt-2 text-sm text-text-muted">{t("oxxa.push.nothing_to_change")}</p>
        <div class="mt-3">
          <Button variant="secondary" size="sm" type="button" onclick={() => (editAnyway = true)}>
            {t("oxxa.push.change_anyway")}
          </Button>
        </div>
      {:else}
        <form
          method="POST"
          action="?/oxxaPush"
          use:enhance={busy.keep("push")}
          class="mt-3 space-y-3"
        >
          {#if accounts.length > 1}
            <div class="max-w-sm">
              <label class={labelClass} for="oxxa-account">{t("oxxa.title")}</label>
              <select id="oxxa-account" name="account_id" class={inputClass}>
                {#each accounts as account (account.id)}
                  <option value={account.id}>{account.name}</option>
                {/each}
              </select>
            </div>
          {:else}
            <input type="hidden" name="account_id" value={soleAccount} />
          {/if}
          <div class="min-w-0">
            <label class={labelClass} for="oxxa-ns">{t("oxxa.push.nameservers")}</label>
            <textarea
              id="oxxa-ns"
              name="nameservers"
              rows={Math.max(MIN_NAMESERVERS, Math.min(MAX_NAMESERVERS, pushCount + 1))}
              bind:value={nameservers}
              spellcheck="false"
              class="{inputClass} font-mono"></textarea>
            <p class="mt-1 text-xs text-text-muted">{t("oxxa.push.help")}</p>
            {#if showFromCloudflare}
              <!-- The composition, as a control rather than a note: the box opens on Cloudflare's
                   pair where there is one, and this puts it back after an edit or a failed
                   attempt. Absent while the box already holds it, since pressing it would do
                   nothing at all. -->
              <div class="mt-2">
                <Button
                  type="button"
                  variant="secondary"
                  size="xs"
                  onclick={() => (nameservers = cloudflareNameservers.join("\n"))}
                >
                  {t("oxxa.push.from_cloudflare")}
                </Button>
              </div>
            {/if}
          </div>
          <Button type="submit" loading={busy.is("push")} disabled={busy.active || !pushValid}>
            {t("oxxa.push.submit")}
          </Button>
        </form>
      {/if}
    </section>
  {/if}
{/if}
