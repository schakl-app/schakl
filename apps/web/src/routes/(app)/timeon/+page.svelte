<script lang="ts">
  /**
   * The Timeon sync workspace.
   *
   * Four questions, in the order somebody asks them: **is it in step?**, **what needs me?**, **run
   * it again**, and **what happened last time?** Everything else on the page is in service of one
   * of those.
   *
   * Two decisions carry the screen.
   *
   * **A dry run is the primary button.** It is a read of both systems and a piece of arithmetic —
   * it changes nothing, needs no write permission, and answers exactly the question somebody has
   * before they press the real one. Putting "Synchroniseren" first would make the safe act the
   * one you have to look for (#305: show the constraint working rather than removing the control).
   *
   * **The conflict queue is the top of the page when it is not empty, and absent when it is.** A
   * heading over "geen conflicten" is a heading over a negative sentence (#364), and a queue that
   * sits there greyed out every day is one people stop reading the day it fills.
   */
  import { AlertTriangle, Link2Off, RefreshCw } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtDateTime, fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import ConflictCard from "$lib/integrations/timeon/ConflictCard.svelte";
  import RunReport from "$lib/integrations/timeon/RunReport.svelte";
  import type {
    TimeonAccount,
    TimeonConflict,
    TimeonLink,
    TimeonRun,
  } from "$lib/integrations/timeon/types";

  let { data, form } = $props();

  const accounts = $derived(data.accounts as TimeonAccount[]);
  const account = $derived(accounts.find((a) => a.id === data.selectedId) ?? null);
  const conflicts = $derived(data.conflicts as TimeonConflict[]);
  const runs = $derived(data.runs as TimeonRun[]);
  const links = $derived(data.links as TimeonLink[]);
  /**
   * The history, minus the run that is already printed above it as **Resultaat**.
   *
   * The page reloads after a sync, so the run just made is in both lists — and two identical
   * cards one screen apart read as "it ran twice", which is the one thing a sync report must
   * never accidentally say.
   */
  const history = $derived(runs.filter((run) => run.id !== form?.run?.id));
  /** Settling a conflict writes into schakl *and* Timeon, so both halves gate it (#310). */
  const mayResolve = $derived(Boolean(data.mayWrite && data.mayWriteHours));

  const busy = new InFlight();
  /** The window override, empty by default — the account's own rolling window is the answer. */
  let windowFrom = $state("");
  let windowTo = $state("");
  let showWindow = $state(false);

  const idle = $derived(
    account !== null && account.hours_direction === "off" && account.projects_direction === "off",
  );

  const paired = $derived(
    Object.entries((account?.counts ?? {}) as Record<string, number>)
      .filter(([key]) => key.startsWith("hour."))
      .reduce((sum, [, value]) => sum + value, 0),
  );

  const linkStatusTone: Record<string, string> = {
    linked: "text-text-muted",
    drift: "text-amber-600",
    missing: "text-amber-600",
    conflict: "text-amber-600",
    error: "text-red-600",
    ignored: "text-text-muted",
    pending: "text-text-muted",
  };
</script>

<svelte:head><title>{pageTitle(t("timeon.workspace.title"))}</title></svelte:head>

<div class="mx-auto w-full max-w-4xl px-4 py-6">
  <header class="mb-6 flex flex-wrap items-start justify-between gap-3">
    <div>
      <h1 class="text-xl font-semibold text-text">{t("timeon.workspace.title")}</h1>
      <p class="mt-1 text-sm text-text-muted">{t("timeon.workspace.intro")}</p>
    </div>
    <a
      href="/settings/timeon"
      class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:bg-surface-raised"
    >
      {t("timeon.workspace.settings")}
    </a>
  </header>

  {#if accounts.length === 0}
    <p class="rounded-lg border border-border bg-surface p-4 text-sm text-text-muted">
      {t("timeon.workspace.no_account")}
      <a class="text-brand hover:underline" href="/settings/timeon">
        {t("timeon.workspace.connect")}
      </a>
    </p>
  {:else}
    {#if accounts.length > 1}
      <!-- The URL is the view: a reload lands on the same connection and the tab is shareable. -->
      <nav class="mb-4 flex flex-wrap gap-2">
        {#each accounts as row (row.id)}
          <a
            href={`/timeon?account=${row.id}`}
            class={`rounded-full border px-3 py-1 text-sm ${
              row.id === data.selectedId
                ? "border-brand bg-brand/10 text-text"
                : "border-border text-text-muted hover:text-text"
            }`}
          >
            {row.name}
          </a>
        {/each}
      </nav>
    {/if}

    {#if account}
      <!-- What is true right now, in four numbers. The state, never a spinner's absence: "nog
           niet gesynchroniseerd" and "niets te doen" are different answers (#379's pending rule). -->
      <section class="mb-6 rounded-xl border border-border bg-surface p-4">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 class="text-base font-semibold text-text">
              {account.organisation_name ?? account.name}
            </h2>
            <p class="mt-0.5 text-sm text-text-muted">
              {#if account.last_pull_at}
                {t("timeon.workspace.last_run", { when: fmtDateTime(account.last_pull_at) })}
              {:else}
                {t("timeon.workspace.never_run")}
              {/if}
              · {t(`timeon.direction.${account.hours_direction}`)}
              {#if account.auto_sync}· {t("timeon.workspace.nightly")}{/if}
            </p>
          </div>
          <dl class="flex gap-5">
            <div>
              <dt class="text-xs text-text-muted">{t("timeon.workspace.paired")}</dt>
              <dd class="text-lg font-semibold tabular-nums text-text">{paired}</dd>
            </div>
            <div>
              <dt class="text-xs text-text-muted">{t("timeon.workspace.open_conflicts")}</dt>
              <dd
                class={`text-lg font-semibold tabular-nums ${
                  account.open_conflicts > 0 ? "text-amber-600" : "text-text"
                }`}
              >
                {account.open_conflicts}
              </dd>
            </div>
          </dl>
        </div>

        {#if idle}
          <p class="mt-3 flex items-start gap-2 rounded-lg bg-surface-raised p-3 text-sm text-text">
            <AlertTriangle size={15} class="mt-0.5 shrink-0 text-amber-600" aria-hidden="true" />
            <span>
              {t("timeon.workspace.idle")}
              <a class="text-brand hover:underline" href="/settings/timeon">
                {t("timeon.workspace.settings")}
              </a>
            </span>
          </p>
        {/if}

        <!-- Three acts, safest first. `adopt` is the one an agency presses on day one: it pairs
             what is already on both sides and writes nothing at all, so "2814 gekoppeld, 3 alleen
             in Timeon" is a fact you can look at before deciding anything. -->
        <form
          method="POST"
          action="?/sync"
          class="mt-4 flex flex-wrap items-end gap-2"
          use:enhance={busy.keep("sync")}
        >
          <input type="hidden" name="account_id" value={account.id} />
          {#if showWindow}
            <!-- `DateInput`, never a native `type="date"`: the browser formats that one after its
                 *own* locale, so a Dutch tenant is asked for mm/dd/yyyy (#13). -->
            <div>
              <span class="mb-1 block text-xs text-text-muted">
                {t("timeon.workspace.window_from")}
              </span>
              <DateInput name="window_from" id="window-from" bind:value={windowFrom} />
            </div>
            <div>
              <span class="mb-1 block text-xs text-text-muted">
                {t("timeon.workspace.window_to")}
              </span>
              <DateInput name="window_to" id="window-to" bind:value={windowTo} />
            </div>
          {/if}
          <Button
            type="submit"
            name="kind"
            value="hours"
            loading={busy.is("sync")}
            disabled={busy.active}
          >
            {t("timeon.workspace.dry_run")}
          </Button>
          <Button
            type="submit"
            variant="secondary"
            name="kind"
            value="adopt"
            disabled={busy.active}
          >
            {t("timeon.workspace.adopt")}
          </Button>
          {#if data.mayWrite && data.mayWriteHours}
            <!-- The only control on this page that writes, and it says so in its own label. -->
            <Button type="submit" variant="success" name="apply" value="1" disabled={busy.active}>
              <RefreshCw size={14} aria-hidden="true" />
              {t("timeon.workspace.apply")}
            </Button>
          {/if}
          <button
            type="button"
            class="text-sm text-brand hover:underline"
            onclick={() => (showWindow = !showWindow)}
          >
            {showWindow ? t("timeon.workspace.window_hide") : t("timeon.workspace.window_show")}
          </button>
        </form>
        {#if showWindow}
          <p class="mt-1 text-xs text-text-muted">{t("timeon.workspace.window_help")}</p>
        {/if}
      </section>
    {/if}

    {#if form?.error}
      <p
        class="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-500/10 dark:text-red-400"
      >
        {t(form.error)}
      </p>
    {/if}
    {#if form?.run}
      <section class="mb-6">
        <h2 class="mb-2 text-base font-semibold text-text">{t("timeon.workspace.result")}</h2>
        <RunReport run={form.run} />
      </section>
    {/if}

    {#if conflicts.length > 0}
      <section class="mb-6">
        <h2 class="mb-1 text-base font-semibold text-text">
          {t("timeon.workspace.conflicts")} ({conflicts.length})
        </h2>
        <p class="mb-3 text-sm text-text-muted">{t("timeon.workspace.conflicts_intro")}</p>
        <div class="space-y-3">
          {#each conflicts as conflict (conflict.id)}
            <ConflictCard {conflict} {busy} {mayResolve} />
          {/each}
        </div>
      </section>
    {/if}

    {#if links.length > 0 || data.linkStatus === "all"}
      <section class="mb-6">
        <div class="mb-2 flex flex-wrap items-center justify-between gap-2">
          <h2 class="text-base font-semibold text-text">{t("timeon.workspace.links")}</h2>
          <nav class="flex gap-2 text-sm">
            <!-- A default that hides rows owes the hidden state a URL of its own (#329). -->
            <a
              href={`/timeon?account=${data.selectedId}&links=attention`}
              class={data.linkStatus === "attention"
                ? "text-text"
                : "text-text-muted hover:text-text"}
            >
              {t("timeon.workspace.links_attention")}
            </a>
            <a
              href={`/timeon?account=${data.selectedId}&links=all`}
              class={data.linkStatus === "all" ? "text-text" : "text-text-muted hover:text-text"}
            >
              {t("timeon.workspace.links_all")}
            </a>
          </nav>
        </div>
        {#if links.length === 0}
          <p class="text-sm text-text-muted">{t("timeon.workspace.links_none")}</p>
        {:else}
          <div class="overflow-x-auto rounded-lg border border-border">
            <table class="w-full min-w-[36rem] text-sm">
              <thead class="bg-surface-raised text-left text-xs text-text-muted">
                <tr>
                  <th class="px-3 py-2 font-normal">{t("timeon.workspace.col_kind")}</th>
                  <th class="px-3 py-2 font-normal">{t("timeon.workspace.col_local")}</th>
                  <th class="px-3 py-2 font-normal">{t("timeon.workspace.col_client")}</th>
                  <th class="px-3 py-2 font-normal">{t("timeon.workspace.col_date")}</th>
                  <th class="px-3 py-2 font-normal">{t("timeon.workspace.col_status")}</th>
                  <th class="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {#each links as link (link.id)}
                  <tr class="border-t border-border">
                    <td class="px-3 py-2 text-text-muted">{t(`timeon.link_kind.${link.kind}`)}</td>
                    <td class="px-3 py-2 text-text">
                      {link.local_label ?? link.external_name ?? link.external_id}
                    </td>
                    <td class="px-3 py-2 text-text-muted">{link.company_name ?? "—"}</td>
                    <td class="px-3 py-2 tabular-nums text-text-muted">
                      {link.external_date ? fmtNumericDate(link.external_date) : "—"}
                    </td>
                    <td class={`px-3 py-2 ${linkStatusTone[link.status] ?? "text-text-muted"}`}>
                      {t(`timeon.link_status.${link.status}`)}
                      {#if link.last_error}
                        <span class="block text-xs opacity-80">{link.last_error}</span>
                      {/if}
                    </td>
                    <td class="px-3 py-2 text-right">
                      {#if data.mayWrite}
                        <!-- "These two are not the same thing" is a different act from every
                             resolution in the queue, and the alternative is editing rows by hand. -->
                        <form
                          method="POST"
                          action="?/unpair"
                          use:enhance={busy.wrap(`unpair:${link.id}`)}
                        >
                          <input type="hidden" name="link_id" value={link.id} />
                          <Button
                            type="submit"
                            variant="secondary"
                            size="xs"
                            loading={busy.is(`unpair:${link.id}`)}
                            disabled={busy.active}
                          >
                            <Link2Off size={13} aria-hidden="true" />
                            {t("timeon.workspace.unpair")}
                          </Button>
                        </form>
                      {/if}
                    </td>
                  </tr>
                {/each}
              </tbody>
            </table>
          </div>
        {/if}
      </section>
    {/if}

    {#if history.length > 0}
      <section>
        <h2 class="mb-2 text-base font-semibold text-text">{t("timeon.workspace.history")}</h2>
        <div class="space-y-2">
          {#each history as run (run.id)}
            <RunReport {run} />
          {/each}
        </div>
      </section>
    {/if}
  {/if}
</div>
