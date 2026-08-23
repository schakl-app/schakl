<script lang="ts">
  /**
   * Instellingen → Timeon.
   *
   * The credential half is ordinary. The policy half is the screen's reason for existing, and it
   * is built around one idea: **a sync's settings are only meaningful in combination**, so the
   * page renders the combination as sentences (`SyncPlan`) beside the controls, live, before Save.
   * Reading "Uren: twee richtingen · Conflicten: Timeon wint" tells nobody that tonight's run may
   * overwrite what they typed this afternoon. The sentence does.
   *
   * Two things it says out loud that no other credential screen has to.
   *
   * **Which organisation this key opens** — and how big it is. A key that merely works is not the
   * answer; "breik., 7 medewerkers, 108 klanten, 159 projecten" is, and it is the shape of the job
   * the sync is about to do. An integration that shows nothing until the first run has spent the
   * one moment somebody was paying attention.
   *
   * **That the window is a horizon.** Timeon's hour rows carry no modified timestamp, so a change
   * outside the window is not synced late — it is never noticed. That is a property of somebody
   * else's API and there is no way to fix it here, so the screen states it where the number is
   * set rather than leaving it to be discovered in March.
   */
  import { AlertTriangle, CheckCircle2, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";
  import RunReport from "$lib/integrations/timeon/RunReport.svelte";
  import SyncPlan from "$lib/integrations/timeon/SyncPlan.svelte";
  import type { TimeonAccount, TimeonRun } from "$lib/integrations/timeon/types";

  let { data, form } = $props();

  const accounts = $derived(data.accounts as TimeonAccount[]);
  const runs = $derived(data.runs as TimeonRun[]);

  const busy = new InFlight();
  let adding = $state(false);
  /**
   * A successful create closes the panel. Emptying it (`busy.clear`) is the right reset and is
   * not the whole job: the row it created is now on the page above, and leaving a blank second
   * form open under it reads as "that did not work" — which is exactly what it looked like the
   * first time this screen was driven for real.
   */
  $effect(() => {
    if (form?.createdId) adding = false;
  });
  let deleteTarget = $state<TimeonAccount | null>(null);
  let confirmDelete = $state(false);

  /**
   * The account as the *unsaved form* currently describes it, so `SyncPlan` narrates what Save
   * would do rather than what the last save did. Seeded per row from the server's copy and
   * updated by the controls — the whole point of the panel is that it moves while you decide.
   */
  let draft = $state<Record<string, Partial<TimeonAccount>>>({});
  const shown = (account: TimeonAccount): TimeonAccount =>
    ({ ...account, ...(draft[account.id] ?? {}) }) as TimeonAccount;
  function edit(account: TimeonAccount, patch: Partial<TimeonAccount>) {
    draft[account.id] = { ...(draft[account.id] ?? {}), ...patch };
  }

  /**
   * "Volgende: 24-08-2026 04:20", or "bij de eerstvolgende ronde" for a run that is already due.
   *
   * The server answers `now` when a connection is due (including one that has never run), and
   * printing that as a moment in the past reads as a fault where the honest sentence is "within
   * the quarter of an hour". The tick is the clock's resolution, not the schedule.
   */
  function nextRunLabel(when: string): string {
    return new Date(when).getTime() <= Date.now() + 1000
      ? t("timeon.schedule.next_soon")
      : t("timeon.schedule.next", { when: fmtDateTime(when) });
  }

  const inputClass =
    "w-full min-w-0 rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const labelClass = "mb-1 block text-sm font-medium text-text";

  /**
   * Status as a glyph plus a word, never as a colour alone: the dev tenant's brand colour is
   * gold, so `text-brand` renders identically to an amber warning. `pending` is a real third
   * state — a row created before a key was pasted — and drawing it red would report a fault
   * during the ten seconds in which everything is going to plan.
   */
  const statusLook: Record<string, { glyph: string; cls: string }> = {
    active: { glyph: "●", cls: "text-emerald-600" },
    pending: { glyph: "○", cls: "text-text-muted" },
    error: { glyph: "■", cls: "text-red-600" },
  };

  const DIRECTIONS = ["off", "pull", "push", "two_way"] as const;
  const POLICIES = ["manual", "schakl_wins", "timeon_wins"] as const;

  /** The two direction selects, named once so the markup is one block rather than two copies. */
  const DIRECTION_FIELDS = ["hours_direction", "projects_direction"] as const;

  /**
   * The switches, in reading order: what the sync may never touch first, what it may create
   * second. `active` is at the end because it is about the connection rather than about the sync.
   *
   * `auto_sync` used to sit here and no longer does (#388): *whether* it runs automatically and
   * *when* are one decision made in one place, and a checkbox in this list with its schedule in
   * another fieldset is the shape that let "Rond 04:20" survive as help text for a year.
   */
  const FLAGS = [
    "protect_invoiced",
    "protect_approved",
    "push_approvals",
    "create_missing_projects",
    "create_missing_users",
    "active",
  ] as const;

  /** The cadences, in the order an agency moves through them during a cutover. */
  const FREQUENCIES = ["hourly", "every_n_hours", "daily", "weekdays"] as const;
</script>

<svelte:head><title>{pageTitle(t("settings.timeon.title"))}</title></svelte:head>

<div class="mx-auto w-full max-w-3xl px-4 py-6">
  <header class="mb-6">
    <h1 class="text-xl font-semibold text-text">{t("settings.timeon.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("settings.timeon.intro")}</p>
  </header>

  {#if form?.error}
    <p
      class="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-500/10 dark:text-red-400"
    >
      {t(form.error)}
    </p>
  {/if}
  {#if form?.saved}
    <p class="mb-4 rounded-lg bg-surface-raised px-3 py-2 text-sm text-text">
      {t("settings.timeon.saved")}
    </p>
  {/if}
  {#if form?.deleted}
    <p class="mb-4 rounded-lg bg-surface-raised px-3 py-2 text-sm text-text">
      {t("settings.timeon.deleted")}
    </p>
  {/if}
  {#if form?.verify}
    {#if form.verify.ok}
      <p
        class="mb-4 flex flex-wrap items-center gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:bg-emerald-500/10 dark:text-emerald-400"
      >
        <CheckCircle2 size={16} aria-hidden="true" />
        <!-- The organisation's name *and* its size: a credential that works is not the answer to
             "did I connect the right account?", and the counts are the shape of the job. -->
        <span>
          {t("settings.timeon.verified", { organisation: form.verify.organisation_name ?? "—" })}
          {#if form.verify.user_count !== null && form.verify.user_count !== undefined}
            · {t("settings.timeon.verified_counts", {
              users: form.verify.user_count ?? 0,
              customers: form.verify.customer_count ?? 0,
              projects: form.verify.project_count ?? 0,
            })}
          {/if}
        </span>
      </p>
    {:else}
      <p
        class="mb-4 flex flex-wrap items-start gap-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700 dark:bg-red-500/10 dark:text-red-400"
      >
        <AlertTriangle size={16} class="mt-0.5 shrink-0" aria-hidden="true" />
        <span>
          {t(form.verify.error_key ?? "errors.timeon.unreachable")}
          {#if form.verify.detail}
            <span class="block opacity-80">{form.verify.detail}</span>
          {/if}
        </span>
      </p>
    {/if}
  {/if}

  {#if accounts.length === 0 && !adding}
    <p class="mb-4 text-sm text-text-muted">{t("settings.timeon.empty")}</p>
  {/if}

  <div class="space-y-6">
    {#each accounts as account (account.id)}
      {@const look = statusLook[account.status] ?? statusLook.pending}
      <section class="rounded-xl border border-border bg-surface p-4">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <div class="min-w-0">
            <h2 class="flex items-center gap-2 text-base font-semibold text-text">
              <span class={look.cls} aria-hidden="true">{look.glyph}</span>
              {account.name}
              <span class="text-sm font-normal text-text-muted">
                {t(`timeon.status.${account.status}`)}
              </span>
            </h2>
            <p class="mt-0.5 text-sm text-text-muted">
              {#if account.organisation_name}
                {account.organisation_name}
                {#if account.organisation_id}({account.organisation_id}){/if}
              {:else}
                {t("settings.timeon.not_verified")}
              {/if}
              {#if account.last_verified_at}
                · {t("settings.timeon.verified_at", {
                  when: fmtDateTime(account.last_verified_at),
                })}
              {/if}
            </p>
            {#if account.last_error}
              <!-- Timeon's own untranslatable words, verbatim: they name the actual problem, and
                   a house sentence in their place would say less. -->
              <p class="mt-1 break-words text-xs text-red-600">{account.last_error}</p>
            {/if}
          </div>
          <div class="flex shrink-0 gap-2">
            {#if data.mayManage}
              <form method="POST" action="?/verify" use:enhance={busy.wrap(`verify:${account.id}`)}>
                <input type="hidden" name="account_id" value={account.id} />
                <Button
                  type="submit"
                  variant="secondary"
                  size="sm"
                  loading={busy.is(`verify:${account.id}`)}
                  disabled={busy.active || !account.connected}
                >
                  {t("settings.timeon.verify")}
                </Button>
              </form>
              <Button
                variant="danger-outline"
                size="sm"
                onclick={() => {
                  deleteTarget = account;
                  confirmDelete = true;
                }}
              >
                <Trash2 size={14} aria-hidden="true" />
                <span class="sr-only">{t("common.delete")}</span>
              </Button>
            {/if}
            {#if data.maySync}
              <a
                href="/timeon?account={account.id}"
                class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:bg-surface-raised"
              >
                {t("settings.timeon.open_workspace")}
              </a>
            {/if}
          </div>
        </div>

        {#if data.mayManage}
          <form
            method="POST"
            action="?/update"
            class="mt-4 space-y-4"
            use:enhance={busy.keep(`save:${account.id}`)}
          >
            <input type="hidden" name="account_id" value={account.id} />

            <div class="grid gap-3 sm:grid-cols-2">
              <div>
                <label class={labelClass} for="name-{account.id}">{t("settings.timeon.name")}</label
                >
                <input id="name-{account.id}" name="name" class={inputClass} value={account.name} />
              </div>
              <div>
                <label class={labelClass} for="key-{account.id}"
                  >{t("settings.timeon.api_key")}</label
                >
                <input
                  id="key-{account.id}"
                  name="api_key"
                  type="password"
                  autocomplete="off"
                  class={inputClass}
                  placeholder={account.connected
                    ? t("settings.timeon.api_key_stored")
                    : t("settings.timeon.api_key_missing")}
                />
                <p class="mt-1 text-xs text-text-muted">{t("settings.timeon.api_key_help")}</p>
              </div>
            </div>

            <fieldset class="rounded-lg border border-border p-3">
              <legend class="px-1 text-sm font-medium text-text">
                {t("settings.timeon.directions")}
              </legend>
              <div class="grid gap-3 sm:grid-cols-2">
                {#each DIRECTION_FIELDS as field (field)}
                  <div>
                    <label class={labelClass} for="{field}-{account.id}">
                      {t(`settings.timeon.${field}`)}
                    </label>
                    <select
                      id="{field}-{account.id}"
                      name={field}
                      class={inputClass}
                      value={account[field]}
                      onchange={(event) =>
                        edit(account, { [field]: event.currentTarget.value } as never)}
                    >
                      {#each DIRECTIONS as direction (direction)}
                        <option value={direction}>{t(`timeon.direction.${direction}`)}</option>
                      {/each}
                    </select>
                  </div>
                {/each}
              </div>

              <div class="mt-3 grid gap-3 sm:grid-cols-2">
                <div>
                  <label class={labelClass} for="window-{account.id}">
                    {t("settings.timeon.window_days")}
                  </label>
                  <input
                    id="window-{account.id}"
                    name="window_days"
                    type="number"
                    min="1"
                    max="3650"
                    class={inputClass}
                    value={account.window_days}
                    oninput={(event) =>
                      edit(account, { window_days: Number(event.currentTarget.value) || 45 })}
                  />
                  <p class="mt-1 text-xs text-text-muted">{t("settings.timeon.window_help")}</p>
                </div>
                <div>
                  <label class={labelClass} for="floor-{account.id}">
                    {t("settings.timeon.history_floor")}
                  </label>
                  <!-- `DateInput`, never a native `type="date"`: the browser formats that one
                       after its *own* locale, so a Dutch tenant gets mm/dd/yyyy (#13). -->
                  <DateInput
                    name="history_floor"
                    id="floor-{account.id}"
                    value={account.history_floor ?? ""}
                    onchange={(value) => edit(account, { history_floor: value || null })}
                  />
                  <p class="mt-1 text-xs text-text-muted">{t("settings.timeon.floor_help")}</p>
                </div>
              </div>

              <div class="mt-3">
                <label class={labelClass} for="policy-{account.id}">
                  {t("settings.timeon.conflict_policy")}
                </label>
                <select
                  id="policy-{account.id}"
                  name="conflict_policy"
                  class={inputClass}
                  value={account.conflict_policy}
                  onchange={(event) =>
                    edit(account, { conflict_policy: event.currentTarget.value } as never)}
                >
                  {#each POLICIES as policy (policy)}
                    <option value={policy}>{t(`timeon.policy.${policy}`)}</option>
                  {/each}
                </select>
              </div>
            </fieldset>

            <fieldset class="rounded-lg border border-border p-3">
              <legend class="px-1 text-sm font-medium text-text">
                {t("settings.timeon.safeguards")}
              </legend>
              <div class="space-y-2">
                {#each FLAGS as field (field)}
                  <label class="flex items-start gap-2 text-sm text-text">
                    <FormCheckbox
                      name={field}
                      checked={Boolean(account[field])}
                      class="mt-0.5"
                      onchange={(event) =>
                        edit(account, { [field]: event.currentTarget.checked } as never)}
                    />
                    <span>
                      {t(`settings.timeon.${field}`)}
                      <span class="block text-xs text-text-muted">
                        {t(`settings.timeon.${field}_help`)}
                      </span>
                    </span>
                  </label>
                {/each}
              </div>
            </fieldset>

            <fieldset class="rounded-lg border border-border p-3">
              <legend class="px-1 text-sm font-medium text-text">
                {t("settings.timeon.schedule")}
              </legend>
              <!-- Whether *and* when, together. A cadence is an operational choice an agency
                   makes during a cutover — hourly while people log hours in both systems,
                   nightly once the traffic is one-way again — and it belongs to the connection,
                   not to a constant in our source (#388). -->
              <label class="flex items-start gap-2 text-sm text-text">
                <FormCheckbox
                  name="auto_sync"
                  checked={Boolean(account.auto_sync)}
                  class="mt-0.5"
                  onchange={(event) => edit(account, { auto_sync: event.currentTarget.checked })}
                />
                <span>
                  {t("settings.timeon.auto_sync")}
                  <span class="block text-xs text-text-muted">
                    {t("settings.timeon.auto_sync_help")}
                  </span>
                </span>
              </label>

              <div class="mt-3 grid gap-3 sm:grid-cols-2">
                <div>
                  <label class={labelClass} for="frequency-{account.id}">
                    {t("settings.timeon.auto_frequency")}
                  </label>
                  <select
                    id="frequency-{account.id}"
                    name="auto_frequency"
                    class={inputClass}
                    value={account.auto_frequency}
                    onchange={(event) =>
                      edit(account, { auto_frequency: event.currentTarget.value } as never)}
                  >
                    {#each FREQUENCIES as frequency (frequency)}
                      <option value={frequency}>{t(`timeon.frequency.${frequency}`)}</option>
                    {/each}
                  </select>
                </div>
                <!-- Both controls stay **in the form** and one of them is hidden, rather than
                     the irrelevant one being torn out of the DOM. A field that is not rendered
                     is a field that is not posted, and this form sends the whole policy — so
                     picking "om de zoveel uur", saving, and switching back to "elke dag" would
                     have silently reset a 09:30 to the default. Keeping the value is the same
                     argument the interval column is stored separately for. -->
                <div class:hidden={shown(account).auto_frequency !== "every_n_hours"}>
                  <label class={labelClass} for="interval-{account.id}">
                    {t("settings.timeon.auto_interval_hours")}
                  </label>
                  <input
                    id="interval-{account.id}"
                    name="auto_interval_hours"
                    type="number"
                    min="1"
                    max="24"
                    class={inputClass}
                    value={account.auto_interval_hours}
                    oninput={(event) =>
                      edit(account, {
                        auto_interval_hours: Number(event.currentTarget.value) || 4,
                      })}
                  />
                </div>
                <div class:hidden={shown(account).auto_frequency === "every_n_hours"}>
                  <label class={labelClass} for="autotime-{account.id}">
                    {t("settings.timeon.auto_time")}
                  </label>
                  <!-- `TimeInput`, never a native `type="time"`: that one renders after the
                       browser's own locale, so a machine set to en-US asks a Dutch tenant for
                       an AM/PM value (#13). -->
                  <TimeInput
                    name="auto_time"
                    id="autotime-{account.id}"
                    value={(account.auto_time ?? "04:20").slice(0, 5)}
                    onchange={(value) => edit(account, { auto_time: value })}
                  />
                  <p class="mt-1 text-xs text-text-muted">
                    {t("settings.timeon.auto_time_help", {
                      zone: account.timezone ?? "UTC",
                    })}
                  </p>
                </div>
              </div>

              <!-- What the schedule resolved to, from the server: the *last* automatic run and
                   the *next* one. A schedule you cannot see is one you cannot trust, which is
                   how a nightly that never fired survived five nights (#387). -->
              <p class="mt-3 text-xs text-text-muted">
                {#if account.last_auto_run_at}
                  {t("timeon.schedule.last", { when: fmtDateTime(account.last_auto_run_at) })}
                {:else}
                  {t("timeon.schedule.never")}
                {/if}
                {#if account.next_auto_run_at}
                  · {nextRunLabel(account.next_auto_run_at)}
                {/if}
              </p>
            </fieldset>

            <!-- Live, from the unsaved form: what Save would make tonight's run do. -->
            <SyncPlan account={shown(account)} />

            <div class="flex justify-end">
              <Button type="submit" loading={busy.is(`save:${account.id}`)} disabled={busy.active}>
                {t("common.save")}
              </Button>
            </div>
          </form>
        {/if}
      </section>
    {/each}
  </div>

  {#if data.mayManage}
    {#if adding}
      <form
        method="POST"
        action="?/create"
        class="mt-6 space-y-3 rounded-xl border border-border bg-surface p-4"
        use:enhance={busy.clear("create")}
      >
        <h2 class="text-base font-semibold text-text">{t("settings.timeon.add")}</h2>
        <div>
          <label class={labelClass} for="new-name">{t("settings.timeon.name")}</label>
          <input id="new-name" name="name" class={inputClass} required />
        </div>
        <div>
          <label class={labelClass} for="new-key">{t("settings.timeon.api_key")}</label>
          <input
            id="new-key"
            name="api_key"
            type="password"
            autocomplete="off"
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("settings.timeon.api_key_help")}</p>
        </div>
        <p class="text-xs text-text-muted">{t("settings.timeon.add_help")}</p>
        <div class="flex justify-end gap-2">
          <Button variant="secondary" onclick={() => (adding = false)}>{t("common.cancel")}</Button>
          <Button type="submit" loading={busy.is("create")} disabled={busy.active}>
            {t("common.save")}
          </Button>
        </div>
      </form>
    {:else}
      <div class="mt-6">
        <Button variant="secondary" onclick={() => (adding = true)}>
          {t("settings.timeon.add")}
        </Button>
      </div>
    {/if}
  {/if}

  {#if data.maySync && runs.length > 0}
    <section class="mt-8">
      <h2 class="mb-2 text-base font-semibold text-text">{t("settings.timeon.recent_runs")}</h2>
      <div class="space-y-2">
        {#each runs as run (run.id)}
          <RunReport {run} compact />
        {/each}
      </div>
      <a class="mt-2 inline-block text-sm text-brand hover:underline" href="/timeon">
        {t("settings.timeon.open_workspace")}
      </a>
    </section>
  {/if}
</div>

<!-- Deleting forgets the connection and its pairings; the *time entries* stay, because a
     pulled entry is schakl's record of work somebody did and removing a credential says nothing
     about whether that work happened. The dialog says so. -->
<ConfirmDialog
  bind:open={confirmDelete}
  title={t("settings.timeon.delete_title")}
  message={t("settings.timeon.delete_message", { name: deleteTarget?.name ?? "" })}
  action="?/delete"
  fields={{ account_id: deleteTarget?.id ?? "" }}
  confirmLabel={t("common.delete")}
/>
