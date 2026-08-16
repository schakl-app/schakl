<script lang="ts">
  /**
   * Instellingen → SnelStart (epic #377, issue #31).
   *
   * One row per connected administration, not a single "the SnelStart account" setting: an agency
   * that does its own books and its holding's books has two, and a singleton would have made the
   * second one an overwrite — of a credential that writes somebody's ledger.
   *
   * Four things this screen exists to say out loud, none of which the other credential screens
   * have to.
   *
   * **Which books this key opens.** A credential that merely *works* is not the answer; the
   * administration's own name and its financial year are, and they are the first thing on the row
   * rather than a detail two lines down. Connecting the right key to the wrong administration is
   * a mistake nobody notices until an invoice is in it.
   *
   * **Three clocks, not one.** Verified, reference data fetched, and last sync are three different
   * authorities: a key can be valid while the chart of accounts is six months stale, and the chart
   * can be fresh while no invoice has been pushed since March. Collapsing them into one
   * "bijgewerkt" would hide whichever of the three is the one that is actually behind.
   *
   * **Connecting is one path or the other, never two equal boxes.** Where the install has an
   * `appShortName`, the tenant approves the coupling at SnelStart and the key arrives by webhook —
   * so the row stays `pending` until the callback lands, and the screen gives it a way to notice
   * rather than leaving somebody staring at a page that will not change by itself. Where it has
   * none, the activation URL is empty and the paste path is the *only* path: no greyed-out
   * "Verbinden" button, because a control that always refuses is a broken control (#253).
   *
   * **What a sync could not do.** Runs are listed with their counts and their per-row failures in
   * words, not as a JSON dump — a batch that pushed 37 of 40 is not a success with a footnote, it
   * is a run with three things still to do, and the three are named.
   */
  import { AlertTriangle, ExternalLink, Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import {
    LIST_COUNTS,
    MATCH_GROUPS,
    matchConfidence,
    missingScopes,
    type MatchConfidence,
    type SnelstartAccount,
    type SnelstartCandidate,
    type SnelstartLedger,
    type SnelstartRun,
    type SnelstartRunError,
  } from "$lib/integrations/snelstart/types";

  let { data, form } = $props();

  const accounts = $derived(data.accounts as SnelstartAccount[]);
  const relations = $derived(data.relations as SnelstartCandidate[]);

  /**
   * The clients this review may still pair, which is every client **not already paired**.
   *
   * One schakl record pairs with one SnelStart record per administration, so offering a client
   * who is already taken is offering a control that can only refuse — and the refusal a reviewer
   * gets is about a row they cannot see from here. A bookkeeper with the same client entered
   * twice is the ordinary reason this screen is open, so this is the common case, not an edge.
   */
  const adoptable = $derived(
    (() => {
      const taken = new Set(
        relations.filter((c) => c.linked && c.company_id).map((c) => c.company_id),
      );
      return data.companies
        .filter((company) => !taken.has(company.id))
        .map((company) => ({ value: company.id, label: company.name }));
    })(),
  );
  const runs = $derived(data.runs as SnelstartRun[]);
  const selected = $derived(accounts.find((a) => a.id === data.selectedId) ?? null);

  const busy = new InFlight();
  let adding = $state(false);
  let editing = $state<string | null>(null);
  /** The optional half of the add form: an own subscription key is rare and never the first ask. */
  let advanced = $state(false);
  let deleteTarget = $state<SnelstartAccount | null>(null);
  let confirmDelete = $state(false);
  /** Which row's callback URL was just copied — several rows, so this is keyed, not a flag. */
  let copied = $state<string | null>(null);

  const inputClass =
    "w-full min-w-0 rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const labelClass = "mb-1 block text-sm font-medium text-text";

  /**
   * `pending` is a real third state, not a nicety: with the activation flow the row exists before
   * the koppelsleutel does. Drawing it red would tell an admin something is broken during the ten
   * seconds in which everything is going exactly to plan.
   */
  const statusBadge: Record<string, string> = {
    active: "bg-emerald-100 text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-400",
    pending: "bg-sky-100 text-sky-800 dark:bg-sky-500/15 dark:text-sky-400",
    error: "bg-red-100 text-red-800 dark:bg-red-500/15 dark:text-red-400",
  };

  const ledgersOf = (accountId: string): SnelstartLedger[] =>
    (data.ledgers as Record<string, SnelstartLedger[]>)[accountId] ?? [];

  /** "8200 — Omzet hoog (diensten)": the number a bookkeeper says out loud comes first. */
  /** `8200 · Omzet hoog (diensten)`. A middot, not a dash: "X — Y" is not how Dutch punctuates,
   *  and the middot is the separator the rest of the app already uses in a compound label. */
  const ledgerLabel = (ledger: SnelstartLedger): string =>
    `${ledger.code} · ${ledger.name}${ledger.function ? ` (${ledger.function})` : ""}`;

  /** A vocabulary key we may not have a word for yet renders as itself rather than as blank. */
  function orRaw(key: string, value: string): string {
    const label = t(`${key}.${value}`);
    return label === `${key}.${value}` ? value : label;
  }

  /** A run's counts, in a fixed reading order, with the list-valued ones taken out. */
  const countEntries = (run: SnelstartRun): [string, number][] =>
    Object.entries(run.counts ?? {})
      .filter(([key, value]) => !LIST_COUNTS.has(key) && typeof value === "number")
      .map(([key, value]) => [key, value as number]);

  const guessedRates = (run: SnelstartRun): string[] => {
    const value = (run.counts ?? {}).guessed_rates;
    return Array.isArray(value) ? value.map(String) : [];
  };

  /** `["relation.active", 12]` → "Klanten, gekoppeld: 12". Two halves, two message keys. */
  const linkCountLabel = ([key, value]: [string, number]): string => {
    const [kind, status] = key.split(".");
    const what = orRaw("snelstart.link_kind", kind);
    const state = status ? orRaw("snelstart.link_status", status) : "";
    return `${what}${state ? ` (${state.toLowerCase()})` : ""}: ${value}`;
  };

  const groupOf = (confidence: MatchConfidence): SnelstartCandidate[] =>
    relations.filter((candidate) => matchConfidence(candidate) === confidence);

  async function copyCallbackUrl(account: SnelstartAccount) {
    if (!account.coupling_webhook_url) return;
    await navigator.clipboard.writeText(account.coupling_webhook_url);
    copied = account.id;
    setTimeout(() => {
      if (copied === account.id) copied = null;
    }, 2000);
  }
</script>

<svelte:head>
  <title>{pageTitle(t("settings.snelstart.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.snelstart.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.snelstart.subtitle")}</p>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}
{#if form?.saved}
  <p class="mb-4 text-sm text-green-600">{t("settings.snelstart.saved")}</p>
{/if}
{#if form?.deleted}
  <p class="mb-4 text-sm text-green-600">{t("settings.snelstart.deleted")}</p>
{/if}
{#if form?.adopted}
  <p class="mb-4 text-sm text-green-600">{t("settings.snelstart.adopted")}</p>
{/if}

{#if form?.verify}
  <!-- Saved and verified are independent answers and both are reported: a rejected credential is
       still a stored credential, so "SnelStart weigert de sleutel" must never read as "er is niets
       opgeslagen". The refusal names *which* credential was refused, because only one of the two
       is something the agency can fix themselves. -->
  <div class="mb-4 text-sm">
    <p class={form.verify.ok ? "text-green-600" : "text-red-600"}>
      {#if form.verify.ok}
        {t("settings.snelstart.verify_ok", {
          administration: form.verify.administration_name ?? "—",
          year: form.verify.financial_year ?? "—",
        })}
      {:else}
        {form.verify.error_key ? t(form.verify.error_key) : t("settings.snelstart.verify_failed")}
      {/if}
      {#if form.verify.error}
        <!-- SnelStart's own untranslatable words, beside the translated key: they name the actual
             problem, and a house sentence in their place would say less. -->
        <span class="text-text-muted">{form.verify.error}</span>
      {/if}
    </p>
    {#if form.verify.missing_scopes?.length}
      <p class="mt-1 text-amber-600">
        {t("settings.snelstart.missing_scopes", {
          scopes: form.verify.missing_scopes
            .map((scope: string) => orRaw("snelstart.scope", scope))
            .join(", "),
        })}
      </p>
    {/if}
  </div>
{/if}

{#if form?.run}
  {@const run = form.run as SnelstartRun}
  <!-- A run that pushed 37 of 40 is not "ok"; it is a run with three things still to do, and the
       three are named here rather than counted. -->
  <div
    class="mb-4 rounded-xl border p-4 text-sm {run.ok
      ? 'border-border bg-surface-raised'
      : 'border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950'}"
  >
    <p class={run.ok ? "text-text" : "text-amber-800 dark:text-amber-200"}>
      {run.ok
        ? t("settings.snelstart.run_ok", { kind: orRaw("snelstart.run_kind", run.kind) })
        : t("settings.snelstart.run_failed", { kind: orRaw("snelstart.run_kind", run.kind) })}
      {#if run.message}<span class="text-text-muted"> {t(run.message)}</span>{/if}
    </p>
    {#if countEntries(run).length}
      <p class="mt-1 text-text-muted">
        {countEntries(run)
          .map(([key, value]) => `${orRaw("snelstart.count", key)}: ${value}`)
          .join(" · ")}
      </p>
    {/if}
    {#if guessedRates(run).length}
      <!-- "We guessed how to tax this" is a sentence a finance integration has to say out loud. -->
      <p class="mt-1 text-amber-600">
        {t("settings.snelstart.guessed_rates", { rates: guessedRates(run).join(", ") })}
      </p>
    {/if}
    {#if run.errors?.length}
      <ul class="mt-2 space-y-1">
        {#each (run.errors as SnelstartRunError[]) as failure, index (`${failure.local_id ?? index}`)}
          <li class="flex items-start gap-1.5 text-red-600">
            <AlertTriangle size={14} class="mt-0.5 shrink-0" aria-hidden="true" />
            <span class="min-w-0 break-words">
              <span class="font-medium">{failure.name ?? "—"}</span>
              <span class="px-1 text-text-muted">&middot;</span>
              {failure.key
                ? t(failure.key)
                : (failure.message ?? t("settings.snelstart.error_unknown"))}
              {#if failure.key && failure.message}
                <span class="text-text-muted"> {failure.message}</span>
              {/if}
            </span>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
{/if}

<section class="max-w-4xl space-y-4">
  <div class="flex flex-wrap items-baseline justify-between gap-2">
    <div>
      <h2 class="text-base font-medium text-text">{t("settings.snelstart.accounts")}</h2>
      <p class="text-sm text-text-muted">{t("settings.snelstart.accounts_intro")}</p>
    </div>
    {#if data.mayManage}
      <Button type="button" variant="secondary" size="sm" onclick={() => (adding = !adding)}>
        {t("settings.snelstart.add")}
      </Button>
    {/if}
  </div>

  {#if adding && data.mayManage}
    <form
      method="POST"
      action="?/create"
      use:enhance={busy.wrap("create", () => async ({ result, update }) => {
        // `clear()` in effect — the next thing typed here is a *different* administration, so
        // the fields empty — plus the one thing it cannot know: the panel that held them was a
        // disclosure, and leaving it open beside the row it just produced shows an admin two
        // "Opslaan" buttons and no sign that anything happened.
        await update({ reset: true });
        if (result.type === "success") adding = false;
      })}
      class="rounded-xl border border-border bg-surface-raised p-5"
    >
      <div class="grid gap-4 sm:grid-cols-2">
        <div class="min-w-0">
          <label class={labelClass} for="new-name">{t("settings.snelstart.name")}</label>
          <input id="new-name" name="name" required maxlength="255" class={inputClass} />
          <p class="mt-1 text-xs text-text-muted">{t("settings.snelstart.name_help")}</p>
        </div>
        {#if data.mayReadProviders}
          <div class="min-w-0">
            <label class={labelClass} for="new-provider">{t("settings.snelstart.provider")}</label>
            <select id="new-provider" name="provider_id" class={inputClass}>
              <option value="">—</option>
              {#each data.providers as provider (provider.id)}
                <option value={provider.id}>{provider.name}</option>
              {/each}
            </select>
            <p class="mt-1 text-xs text-text-muted">{t("settings.snelstart.provider_help")}</p>
          </div>
        {/if}
        <div class="min-w-0 sm:col-span-2">
          <label class={labelClass} for="new-key">{t("settings.snelstart.client_key")}</label>
          <!-- A koppelsleutel is a long JWT, so a textarea rather than a one-line box: a field
               that shows a tenth of what was pasted is a field nobody can check. -->
          <textarea
            id="new-key"
            name="client_key"
            rows="3"
            autocomplete="off"
            spellcheck="false"
            placeholder={t("settings.snelstart.client_key_placeholder")}
            class="{inputClass} font-mono text-xs"></textarea>
          <p class="mt-1 text-xs text-text-muted">{t("settings.snelstart.client_key_help")}</p>
          <p class="mt-1 text-xs text-text-muted">{t("settings.snelstart.client_key_optional")}</p>
        </div>
        <div class="sm:col-span-2">
          <button
            type="button"
            class="text-sm text-brand hover:underline"
            onclick={() => (advanced = !advanced)}
          >
            {t("settings.snelstart.advanced")}
          </button>
          {#if advanced}
            <div class="mt-2 min-w-0">
              <label class={labelClass} for="new-subscription">
                {t("settings.snelstart.subscription_key")}
              </label>
              <input
                id="new-subscription"
                name="subscription_key"
                type="password"
                autocomplete="new-password"
                maxlength="255"
                class={inputClass}
              />
              <p class="mt-1 text-xs text-text-muted">
                {t("settings.snelstart.subscription_key_help")}
              </p>
            </div>
          {/if}
        </div>
      </div>
      <div class="mt-4">
        <Button type="submit" loading={busy.is("create")} disabled={busy.active}>
          {t("common.save")}
        </Button>
      </div>
    </form>
  {/if}

  {#if accounts.length === 0 && !adding}
    <p class="rounded-xl border border-border bg-surface-raised p-5 text-sm text-text-muted">
      {data.mayManage ? t("settings.snelstart.empty") : t("settings.snelstart.empty_no_manage")}
    </p>
  {/if}

  {#each accounts as account (account.id)}
    {@const missing = missingScopes(account.scopes)}
    {@const ledgers = ledgersOf(account.id)}
    <article class="rounded-xl border border-border bg-surface-raised p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <h3 class="flex flex-wrap items-center gap-2 text-sm font-medium text-text">
            <span class="truncate">{account.name}</span>
            <span
              class="rounded-full px-2 py-0.5 text-[11px] font-medium {statusBadge[
                account.status
              ] ?? ''}"
            >
              {orRaw("snelstart.status", account.status)}
            </span>
            {#if !account.active}
              <span class="text-xs font-normal text-text-muted">
                ({t("settings.snelstart.inactive")})
              </span>
            {/if}
          </h3>

          <!-- The "did I connect the right books?" answer, and it is the loudest line on the row
               rather than a detail: a credential that works but opens the holding's administration
               instead of the agency's is a mistake nobody notices until an invoice is in it. -->
          {#if account.administration_name}
            <p class="mt-1 text-base font-medium text-text">
              {account.administration_name}
              {#if account.financial_year}
                <span class="text-sm font-normal text-text-muted">
                  · {t("settings.snelstart.financial_year", { year: account.financial_year })}
                </span>
              {/if}
            </p>
          {:else}
            <p class="mt-1 text-sm text-text-muted">
              {account.connected
                ? t("settings.snelstart.administration_unknown")
                : t("settings.snelstart.not_connected")}
            </p>
          {/if}
        </div>

        <div class="flex flex-wrap items-center gap-2">
          {#if data.mayManage}
            <form method="POST" action="?/verify" use:enhance={busy.wrap(`v-${account.id}`)}>
              <input type="hidden" name="account_id" value={account.id} />
              <Button
                variant="secondary"
                size="xs"
                loading={busy.is(`v-${account.id}`)}
                disabled={busy.active}
              >
                {t("settings.snelstart.verify")}
              </Button>
            </form>
            <!-- Edit and delete live in the ⋯ menu, never as bare buttons on a row header
                 (docs/UX.md, "known mistakes"); the delete confirms. -->
            <ActionsMenu
              items={[
                {
                  label: t("common.edit"),
                  icon: Pencil,
                  onclick: () => (editing = editing === account.id ? null : account.id),
                },
                {
                  label: t("common.delete"),
                  icon: Trash2,
                  danger: true,
                  onclick: () => {
                    deleteTarget = account;
                    confirmDelete = true;
                  },
                },
              ]}
            />
          {/if}
        </div>
      </div>

      <!-- Three clocks. Three authorities, three separate lines: a valid key over a six-month-old
           chart of accounts and a fresh chart nothing has been pushed against are different
           problems, and one "bijgewerkt" would have hidden whichever one was real. -->
      <dl class="mt-3 grid gap-2 text-xs sm:grid-cols-3">
        <div>
          <dt class="text-text-muted">{t("settings.snelstart.last_verified")}</dt>
          <dd class="text-text">
            {account.last_verified_at
              ? fmtDateTime(account.last_verified_at)
              : t("settings.snelstart.never")}
          </dd>
        </div>
        <div>
          <dt class="text-text-muted">{t("settings.snelstart.last_reference_sync")}</dt>
          <dd class="text-text">
            {account.last_reference_sync_at
              ? fmtDateTime(account.last_reference_sync_at)
              : t("settings.snelstart.never")}
          </dd>
        </div>
        <div>
          <dt class="text-text-muted">{t("settings.snelstart.last_synced")}</dt>
          <dd class="text-text">
            {account.last_synced_at
              ? fmtDateTime(account.last_synced_at)
              : t("settings.snelstart.never")}
          </dd>
        </div>
      </dl>

      {#if account.last_error}
        <!-- Verbatim, always. It is evidence, not an error envelope (§9). -->
        <p
          class="mt-2 break-words text-xs {account.status === 'error'
            ? 'text-red-600'
            : 'text-text-muted'}"
        >
          {account.status === "error"
            ? t("settings.snelstart.status_error")
            : t("settings.snelstart.last_error")}: {account.last_error}
        </p>
      {/if}

      {#if missing.length}
        <!-- A scope discovered mid-sync is a 403 forty rows in, so it is said here first — and it
             is said on every page load, not only right after a verify. -->
        <p class="mt-2 text-xs text-amber-600">
          {t("settings.snelstart.missing_scopes", {
            scopes: missing.map((scope) => orRaw("snelstart.scope", scope)).join(", "),
          })}
        </p>
      {/if}

      {#if account.status === "pending" && !account.connected}
        <!-- The connect step, and the two paths are never drawn as two equal boxes. -->
        <div
          class="mt-4 rounded-lg border border-sky-300 bg-sky-50 p-4 dark:border-sky-800 dark:bg-sky-950"
        >
          {#if account.activation_url}
            <p class="text-sm text-sky-900 dark:text-sky-100">
              {t("settings.snelstart.activate_intro")}
            </p>
            <div class="mt-3 flex flex-wrap items-center gap-2">
              <!-- An anchor, not a button: it leaves the app, and a middle click should open a
                   tab like every other link does. -->
              <a
                href={account.activation_url}
                target="_blank"
                rel="noopener noreferrer"
                class="inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-brand-contrast hover:opacity-90"
              >
                {t("settings.snelstart.activate")}
                <ExternalLink size={14} aria-hidden="true" />
              </a>
              {#if data.mayManage}
                <!-- The key arrives by webhook, so nothing on this page changes by itself. This
                     is the way to notice, and it is a re-verify rather than a poll: one button
                     the admin presses when they are back from SnelStart. -->
                <form method="POST" action="?/verify" use:enhance={busy.wrap(`p-${account.id}`)}>
                  <input type="hidden" name="account_id" value={account.id} />
                  <Button
                    variant="secondary"
                    size="sm"
                    loading={busy.is(`p-${account.id}`)}
                    disabled={busy.active}
                  >
                    {t("settings.snelstart.check_connection")}
                  </Button>
                </form>
              {/if}
            </div>
            <p class="mt-2 text-xs text-sky-900/80 dark:text-sky-100/80">
              {t("settings.snelstart.activate_help")}
            </p>
          {:else}
            <!-- No `appShortName` on this install, so there is no activation URL and no button for
                 one: a disabled "Verbinden met SnelStart" would be a control that can only ever
                 refuse (#253). The paste path is the whole answer here, and it says so. -->
            <p class="text-sm text-sky-900 dark:text-sky-100">
              {t("settings.snelstart.activate_unavailable")}
            </p>
            <p class="mt-1 text-xs text-sky-900/80 dark:text-sky-100/80">
              {t("settings.snelstart.client_key_help")}
            </p>
          {/if}
        </div>
      {/if}

      {#if data.mayManage && account.coupling_webhook_url}
        <!-- Read-only, and shown for the reason Mollie's notification URL is: SnelStart posts a
             granted koppelsleutel here, it has to be reachable, and behind an access proxy
             somebody has to allow that path. An admin who cannot see the URL cannot allow it, and
             the failure it causes — an approved coupling that never arrives — is silent. -->
        <div class="mt-4 border-t border-border pt-3">
          <label class="mb-1 block text-sm font-medium text-text" for="callback-{account.id}">
            {t("settings.snelstart.callback_url")}
          </label>
          <div class="flex gap-2">
            <input
              id="callback-{account.id}"
              readonly
              value={account.coupling_webhook_url}
              class="{inputClass} min-w-0 flex-1 bg-surface font-mono text-xs"
              onfocus={(e) => e.currentTarget.select()}
            />
            <Button
              type="button"
              variant="secondary"
              class="shrink-0"
              onclick={() => copyCallbackUrl(account)}
            >
              {copied === account.id ? t("common.copied") : t("common.copy")}
            </Button>
          </div>
          <p class="mt-1 text-xs text-text-muted">{t("settings.snelstart.callback_url_help")}</p>
        </div>
      {/if}

      <!-- What this administration currently holds. Stored counts, so they cost nothing. The key
           is a composite (`relation.active`), and it is translated as its two halves rather than
           as eighteen message keys nobody would keep true. -->
      {#if Object.keys(account.counts ?? {}).length}
        <p class="mt-3 text-xs text-text-muted">
          {Object.entries(account.counts ?? {})
            .map(linkCountLabel)
            .join(" · ")}
        </p>
      {/if}

      <!-- The seven acts. Each mirrors the key the call actually makes (#310), never the key that
           opened the screen: reading the administration is `sync.run`, writing into it is
           `ledger.write`, and pushing an invoice is both that and `invoicing.invoice.write`. -->
      {#if account.connected && (data.maySync || data.mayWrite)}
        <div class="mt-4 flex flex-wrap gap-2 border-t border-border pt-3">
          {#if data.maySync}
            <form
              method="POST"
              action="?/syncReference"
              use:enhance={busy.wrap(`ref-${account.id}`)}
            >
              <input type="hidden" name="account_id" value={account.id} />
              <Button
                variant="secondary"
                size="xs"
                loading={busy.is(`ref-${account.id}`)}
                disabled={busy.active}
              >
                {t("settings.snelstart.act.reference")}
              </Button>
            </form>
            <form
              method="POST"
              action="?/syncRelations"
              use:enhance={busy.wrap(`rel-${account.id}`)}
            >
              <input type="hidden" name="account_id" value={account.id} />
              <Button
                variant="secondary"
                size="xs"
                loading={busy.is(`rel-${account.id}`)}
                disabled={busy.active}
              >
                {t("settings.snelstart.act.link_relations")}
              </Button>
            </form>
          {/if}
          {#if data.mayWrite}
            <form
              method="POST"
              action="?/pushRelations"
              use:enhance={busy.wrap(`prel-${account.id}`)}
            >
              <input type="hidden" name="account_id" value={account.id} />
              <Button
                variant="secondary"
                size="xs"
                loading={busy.is(`prel-${account.id}`)}
                disabled={busy.active}
              >
                {t("settings.snelstart.act.push_relations")}
              </Button>
            </form>
          {/if}
          {#if data.mayPushInvoices}
            <form
              method="POST"
              action="?/pushInvoices"
              use:enhance={busy.wrap(`pinv-${account.id}`)}
            >
              <input type="hidden" name="account_id" value={account.id} />
              <Button
                variant="secondary"
                size="xs"
                loading={busy.is(`pinv-${account.id}`)}
                disabled={busy.active}
              >
                {t("settings.snelstart.act.push_invoices")}
              </Button>
            </form>
          {/if}
          {#if data.maySync}
            <form
              method="POST"
              action="?/syncPayments"
              use:enhance={busy.wrap(`pay-${account.id}`)}
            >
              <input type="hidden" name="account_id" value={account.id} />
              <Button
                variant="secondary"
                size="xs"
                loading={busy.is(`pay-${account.id}`)}
                disabled={busy.active}
              >
                {t("settings.snelstart.act.pull_payments")}
              </Button>
            </form>
          {/if}
          {#if data.mayWrite}
            <form
              method="POST"
              action="?/pushArticles"
              use:enhance={busy.wrap(`art-${account.id}`)}
            >
              <input type="hidden" name="account_id" value={account.id} />
              <Button
                variant="secondary"
                size="xs"
                loading={busy.is(`art-${account.id}`)}
                disabled={busy.active}
              >
                {t("settings.snelstart.act.push_articles")}
              </Button>
            </form>
          {/if}
        </div>
      {/if}

      {#if editing === account.id && data.mayManage}
        <form
          method="POST"
          action="?/update"
          use:enhance={busy.keep(`e-${account.id}`)}
          class="mt-4 grid gap-4 border-t border-border pt-4 sm:grid-cols-2"
        >
          <input type="hidden" name="account_id" value={account.id} />
          <div class="min-w-0">
            <label class={labelClass} for="name-{account.id}">
              {t("settings.snelstart.name")}
            </label>
            <input
              id="name-{account.id}"
              name="name"
              value={account.name}
              maxlength="255"
              class={inputClass}
            />
          </div>
          {#if data.mayReadProviders}
            <div class="min-w-0">
              <label class={labelClass} for="provider-{account.id}">
                {t("settings.snelstart.provider")}
              </label>
              <select
                id="provider-{account.id}"
                name="provider_id"
                value={account.provider_id ?? ""}
                class={inputClass}
              >
                <option value="">—</option>
                {#each data.providers as provider (provider.id)}
                  <option value={provider.id}>{provider.name}</option>
                {/each}
              </select>
            </div>
          {/if}

          <div class="min-w-0 sm:col-span-2">
            <label class={labelClass} for="key-{account.id}">
              {t("settings.snelstart.client_key")}
            </label>
            <textarea
              id="key-{account.id}"
              name="client_key"
              rows="3"
              autocomplete="off"
              spellcheck="false"
              placeholder={account.connected ? t("settings.snelstart.client_key_stored") : ""}
              class="{inputClass} font-mono text-xs"></textarea>
            <!-- Rotating forgets every observation made through the old key, so the field says so
                 before somebody pastes into it: a key that now opens different books must not keep
                 the old administration's name on screen. -->
            <p class="mt-1 text-xs text-text-muted">{t("settings.snelstart.client_key_keep")}</p>
          </div>

          <!-- The revenue account a line books to when nothing more specific applies. A Combobox
               rather than a native select: 233 accounts is not a dropdown anybody scrolls, and it
               is a closed vocabulary, so there is no inline-create path to offer (#256). -->
          <div class="min-w-0 sm:col-span-2">
            <label class={labelClass} for="ledger-{account.id}">
              {t("settings.snelstart.default_ledger")}
            </label>
            {#if ledgers.length}
              <Combobox
                id="ledger-{account.id}"
                name="default_ledger_code"
                value={account.default_ledger_code ?? ""}
                placeholder={t("settings.snelstart.default_ledger_placeholder")}
                items={ledgers.map((ledger) => ({
                  value: ledger.code,
                  label: ledgerLabel(ledger),
                  hint: (ledger.vat_kinds ?? []).join(", "),
                }))}
              />
            {:else}
              <!-- No chart of accounts cached yet. Saying which button fills it is the whole
                   difference between an empty picker and a broken screen. -->
              <p class="text-sm text-text-muted">
                {t("settings.snelstart.default_ledger_empty")}
              </p>
              <input
                type="hidden"
                name="default_ledger_code"
                value={account.default_ledger_code ?? ""}
              />
            {/if}
            <p class="mt-1 text-xs text-text-muted">
              {t("settings.snelstart.default_ledger_help")}
            </p>
          </div>

          <div class="space-y-3 sm:col-span-2">
            <label class="flex items-start gap-2 text-sm text-text">
              <FormCheckbox
                name="auto_push_invoices"
                checked={account.auto_push_invoices}
                class="mt-0.5 rounded border-border"
              />
              <span>
                {t("settings.snelstart.auto_push_invoices")}
                <span class="mt-0.5 block text-xs text-text-muted">
                  {t("settings.snelstart.auto_push_invoices_help")}
                </span>
              </span>
            </label>
            <label class="flex items-start gap-2 text-sm text-text">
              <FormCheckbox
                name="attach_invoice_pdf"
                checked={account.attach_invoice_pdf}
                class="mt-0.5 rounded border-border"
              />
              <span>
                {t("settings.snelstart.attach_invoice_pdf")}
                <span class="mt-0.5 block text-xs text-text-muted">
                  {t("settings.snelstart.attach_invoice_pdf_help")}
                </span>
              </span>
            </label>
            <label class="flex items-start gap-2 text-sm text-text">
              <FormCheckbox
                name="pull_payments"
                checked={account.pull_payments}
                class="mt-0.5 rounded border-border"
              />
              <span>
                {t("settings.snelstart.pull_payments")}
                <span class="mt-0.5 block text-xs text-text-muted">
                  {t("settings.snelstart.pull_payments_help")}
                </span>
              </span>
            </label>
            <label class="flex items-center gap-2 text-sm text-text">
              <FormCheckbox name="active" checked={account.active} class="rounded border-border" />
              {t("settings.snelstart.active")}
            </label>
          </div>

          <div class="min-w-0 sm:col-span-2">
            <label class={labelClass} for="subscription-{account.id}">
              {t("settings.snelstart.subscription_key")}
            </label>
            <input
              id="subscription-{account.id}"
              name="subscription_key"
              type="password"
              autocomplete="new-password"
              maxlength="255"
              placeholder={account.own_subscription_key
                ? t("settings.snelstart.subscription_key_stored")
                : t("settings.snelstart.subscription_key_default")}
              class={inputClass}
            />
            <p class="mt-1 text-xs text-text-muted">
              {t("settings.snelstart.subscription_key_help")}
            </p>
            {#if account.own_subscription_key}
              <!-- Its own control, because a blank box cannot mean two things. Leaving the field
                   empty keeps the stored key; falling back to the install's is a real state and
                   needs somebody to say so on purpose. -->
              <label class="mt-2 flex items-center gap-2 text-sm text-text">
                <FormCheckbox name="drop_subscription_key" class="rounded border-border" />
                {t("settings.snelstart.subscription_key_drop")}
              </label>
            {/if}
          </div>

          <div class="sm:col-span-2">
            <Button type="submit" loading={busy.is(`e-${account.id}`)} disabled={busy.active}>
              {t("common.save")}
            </Button>
          </div>
        </form>
      {/if}
    </article>
  {/each}
</section>

{#if data.maySync && accounts.length > 0}
  <section class="mt-8 max-w-4xl space-y-4">
    {#if accounts.length > 1}
      <!-- Which administration the log and the review below are about. `<a href>`, so the choice
           is in the URL: the back button lands where it left and the tab is shareable. -->
      <div class="flex flex-wrap gap-2">
        {#each accounts as account (account.id)}
          <a
            href="?account={account.id}"
            class="rounded-full px-3 py-1 text-sm {account.id === data.selectedId
              ? 'bg-brand text-brand-contrast'
              : 'bg-surface-raised text-text-muted ring-1 ring-inset ring-border'}"
          >
            {account.name}
          </a>
        {/each}
      </div>
    {/if}

    <!-- The run log. #31's "failures are visible", and the reason it is a list rather than a
         banner: a banner is gone on the next navigation, and "which invoices did not make it into
         the books last night" is asked the morning after. -->
    <div>
      <h2 class="text-base font-medium text-text">{t("settings.snelstart.runs")}</h2>
      <p class="text-sm text-text-muted">{t("settings.snelstart.runs_intro")}</p>
    </div>

    {#if runs.length === 0}
      <p class="rounded-xl border border-border bg-surface-raised p-5 text-sm text-text-muted">
        {t("settings.snelstart.runs_empty")}
      </p>
    {:else}
      <ul class="divide-y divide-border rounded-xl border border-border bg-surface-raised">
        {#each runs as run (run.id)}
          <li class="p-4 text-sm">
            <div class="flex flex-wrap items-baseline justify-between gap-2">
              <p class="font-medium text-text">
                {orRaw("snelstart.run_kind", run.kind)}
                <span class="ml-2 text-xs font-normal {run.ok ? 'text-green-600' : 'text-red-600'}">
                  {run.ok
                    ? t("settings.snelstart.run_state_ok")
                    : t("settings.snelstart.run_state_failed")}
                </span>
              </p>
              <p class="text-xs text-text-muted">{fmtDateTime(run.created_at)}</p>
            </div>
            {#if countEntries(run).length}
              <p class="mt-1 text-xs text-text-muted">
                {countEntries(run)
                  .map(([key, value]) => `${orRaw("snelstart.count", key)}: ${value}`)
                  .join(" · ")}
              </p>
            {/if}
            {#if run.message}
              <p class="mt-1 break-words text-xs text-text-muted">{t(run.message)}</p>
            {/if}
            {#if guessedRates(run).length}
              <p class="mt-1 text-xs text-amber-600">
                {t("settings.snelstart.guessed_rates", { rates: guessedRates(run).join(", ") })}
              </p>
            {/if}
            {#if run.errors?.length}
              <!-- Folded, never hidden: the count is on the summary line, so a run with three
                   failures says three even while closed. -->
              <details class="mt-2">
                <summary class="cursor-pointer text-xs text-red-600">
                  {run.errors.length === 1
                    ? t("settings.snelstart.run_errors_one", { count: 1 })
                    : t("settings.snelstart.run_errors_other", { count: run.errors.length })}
                </summary>
                <ul class="mt-1 space-y-1">
                  {#each (run.errors as SnelstartRunError[]) as failure, index (`${failure.local_id ?? index}`)}
                    <li class="min-w-0 break-words text-xs text-text">
                      <span class="font-medium">{failure.name ?? "—"}</span>
                      <span class="px-1 text-text-muted">&middot;</span>
                      <span class="text-red-600">
                        {failure.key
                          ? t(failure.key)
                          : (failure.message ?? t("settings.snelstart.error_unknown"))}
                      </span>
                      {#if failure.key && failure.message}
                        <span class="text-text-muted"> {failure.message}</span>
                      {/if}
                    </li>
                  {/each}
                </ul>
              </details>
            {/if}
          </li>
        {/each}
      </ul>
    {/if}
  </section>

  <!-- The relation review. Matching proposes; a human disposes — 200 relations against 180
       companies is an overlap nobody can eyeball, and each proposal says *why* it was made so an
       admin only has to actually read the guesses. -->
  <section class="mt-8 max-w-4xl space-y-4">
    <div>
      <h2 class="text-base font-medium text-text">{t("settings.snelstart.review")}</h2>
      <p class="text-sm text-text-muted">{t("settings.snelstart.review_intro")}</p>
      <!-- The ordering, stated rather than assumed: a candidate has nothing to adopt through
           until `Relaties koppelen` has created its link row. -->
      <p class="mt-1 text-sm text-text-muted">{t("settings.snelstart.review_order")}</p>
    </div>

    {#if !data.reviewId}
      {#if selected}
        <!-- Opt-in, because this is the one read on the screen that talks to SnelStart live. An
             ordinary settings visit must not wait on somebody else's server. -->
        <p class="rounded-xl border border-border bg-surface-raised p-5 text-sm text-text-muted">
          {t("settings.snelstart.review_start_hint")}
          <a
            class="ml-1 text-brand hover:underline"
            href="?account={selected.id}&review={selected.id}"
          >
            {t("settings.snelstart.review_start")}
          </a>
        </p>
      {/if}
    {:else if relations.length === 0}
      <p class="rounded-xl border border-border bg-surface-raised p-5 text-sm text-text-muted">
        {t("settings.snelstart.review_empty")}
      </p>
    {:else}
      {#each MATCH_GROUPS as group (group)}
        {@const rows = groupOf(group)}
        {#if rows.length}
          <div>
            <p class="mb-2 text-xs font-medium text-text">
              {t(`settings.snelstart.confidence.${group}`)}
              <span class="font-normal text-text-muted">({rows.length})</span>
            </p>
            <p class="mb-2 text-xs text-text-muted">
              {t(`settings.snelstart.confidence_help.${group}`)}
            </p>
            <div class="overflow-x-auto rounded-xl border border-border bg-surface-raised">
              <table class="w-full min-w-[36rem] text-sm">
                <thead class="text-left text-xs text-text-muted">
                  <tr>
                    <th class="px-3 py-2 font-medium">{t("settings.snelstart.col.relation")}</th>
                    <th class="px-3 py-2 font-medium">{t("settings.snelstart.col.identifiers")}</th>
                    <th class="px-3 py-2 font-medium">{t("settings.snelstart.col.company")}</th>
                  </tr>
                </thead>
                <tbody>
                  {#each rows as candidate (candidate.external_id)}
                    <tr class="border-t border-border align-top">
                      <td class="min-w-0 px-3 py-2">
                        <span class="block break-words text-text">{candidate.name}</span>
                        {#if candidate.external_code}
                          <span class="text-xs text-text-muted">{candidate.external_code}</span>
                        {/if}
                      </td>
                      <td class="min-w-0 px-3 py-2 text-xs text-text-muted">
                        {#if candidate.coc_number}
                          <span class="block break-words">
                            {t("settings.snelstart.coc")}: {candidate.coc_number}
                          </span>
                        {/if}
                        {#if candidate.vat_number}
                          <span class="block break-words">
                            {t("settings.snelstart.vat")}: {candidate.vat_number}
                          </span>
                        {/if}
                        {#if candidate.email}
                          <span class="block break-words">{candidate.email}</span>
                        {/if}
                        {#if candidate.match_on}
                          <span class="mt-1 block text-text">
                            {t("settings.snelstart.matched_on", {
                              reason: orRaw("snelstart.match", candidate.match_on),
                            })}
                          </span>
                        {/if}
                      </td>
                      <td class="min-w-0 px-3 py-2">
                        {#if candidate.linked}
                          <span class="text-text">{candidate.company_name ?? "—"}</span>
                        {:else if !data.maySync}
                          <span class="text-text-muted">{candidate.company_name ?? "—"}</span>
                        {:else if !candidate.link_id}
                          <!-- No link row yet, so nothing to adopt through. A Koppelen button here
                               would post to an id that does not exist (#253). -->
                          <span class="text-xs text-text-muted">
                            {t("settings.snelstart.adopt_needs_sync")}
                          </span>
                        {:else if !data.mayReadCompanies}
                          <span class="text-xs text-text-muted">
                            {t("settings.snelstart.adopt_needs_companies")}
                          </span>
                        {:else}
                          <form
                            method="POST"
                            action="?/adopt"
                            use:enhance={busy.clear(`a-${candidate.link_id}`)}
                            class="flex flex-wrap items-center gap-2"
                          >
                            <input type="hidden" name="account_id" value={data.reviewId} />
                            <input type="hidden" name="link_id" value={candidate.link_id} />
                            <div class="min-w-[12rem] flex-1">
                              <Combobox
                                id="adopt-{candidate.link_id}"
                                name="local_id"
                                value={candidate.company_id ?? ""}
                                ariaLabel={t("settings.snelstart.col.company")}
                                placeholder={t("settings.snelstart.adopt_placeholder")}
                                items={adoptable}
                              />
                            </div>
                            <Button
                              variant="secondary"
                              size="xs"
                              loading={busy.is(`a-${candidate.link_id}`)}
                              disabled={busy.active}
                            >
                              {t("settings.snelstart.adopt")}
                            </Button>
                          </form>
                        {/if}
                      </td>
                    </tr>
                  {/each}
                </tbody>
              </table>
            </div>
          </div>
        {/if}
      {/each}
    {/if}
  </section>
{/if}

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("settings.snelstart.delete_confirm", { name: deleteTarget?.name ?? "" })}
  action="?/delete"
  fields={{ account_id: deleteTarget?.id ?? "" }}
/>
