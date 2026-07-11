<script lang="ts">
  import { BadgeEuro, CalendarClock, FileText, Trash2, UserMinus } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { localeName } from "$lib/core/roles/name";
  import { effectivePermissions, WILDCARD } from "$lib/core/roles/permissions";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { fmtHours } from "$lib/modules/leave/format";
  import WorkScheduleEditor from "$lib/modules/leave/WorkScheduleEditor.svelte";
  import {
    cloneSchedule,
    defaultSchedule,
    weekHours,
    type WorkSchedule,
  } from "$lib/modules/leave/schedule";

  let { data, form } = $props();

  let showInvite = $state(false);
  let revokeId = $state("");
  let confirmRevoke = $state(false);
  let expanded = $state("");

  // The tenant's own roles, fetched once by `settings/+layout.server.ts`. There is no hard-coded
  // list of four any more: an agency defines its own (issue #19).
  const roles = $derived(data.roles);
  const systemRoles = $derived(roles.filter((r) => r.is_system));
  const locale = $derived(data.locale ?? "nl");

  const effectiveFor = (roleIds: string[]) => effectivePermissions(roles, roleIds);

  // --- work schedules (leave module, #46) ---------------------------------------
  // Employment data, so it lives on the person rather than under Instellingen → Verlof.
  type Member = (typeof data.members)[number];
  const profileByUser = $derived(Object.fromEntries(data.profiles.map((p) => [p.user_id, p])));

  let scheduleOpen = $state(false);
  let scheduleFor = $state<Member | null>(null);
  let inherit = $state(true);
  // Filled by `openSchedule` before the modal is ever shown; the initial value is never rendered.
  let draft = $state<WorkSchedule>(defaultSchedule());

  function openSchedule(member: Member) {
    const own = profileByUser[member.user_id]?.schedule ?? null;
    inherit = own === null;
    draft = cloneSchedule((own ?? data.defaultSchedule) as WorkSchedule);
    scheduleFor = member;
    scheduleOpen = true;
  }

  /**
   * A pre-#46 part-timer carries contract hours that predate any schedule. Say so out loud:
   * silently measuring their leave against the 40 h default is the whole trap the issue names.
   */
  function staleHours(member: Member): number | null {
    const profile = profileByUser[member.user_id];
    if (!profile || profile.schedule !== null) return null;
    const stored = Number(profile.hours_per_week);
    const inherited = weekHours(data.defaultSchedule as WorkSchedule);
    return stored === inherited ? null : stored;
  }

  // --- hourly rate (#82) --------------------------------------------------------
  // Salary-adjacent, so its own permission (`leave.rate.read`/`.write`), not `profile.manage`.
  const rateByUser = $derived(
    Object.fromEntries((data.rateRows ?? []).map((r) => [r.user_id, r.hourly_rate])),
  );
  let rateOpen = $state(false);
  let rateFor = $state<Member | null>(null);
  let rateDraft = $state("");

  function openRate(member: Member) {
    const current = rateByUser[member.user_id];
    rateDraft = current == null ? "" : String(current);
    rateFor = member;
    rateOpen = true;
  }

  // --- employment contracts (#65) -----------------------------------------------
  const contractsByUser = $derived.by(() => {
    const map: Record<string, typeof data.contracts> = {};
    for (const c of data.contracts ?? []) (map[c.user_id] ??= []).push(c);
    return map;
  });
  let contractsOpen = $state(false);
  let contractsFor = $state<Member | null>(null);
  const todayIso = new Date().toISOString().slice(0, 10);

  function openContracts(member: Member) {
    contractsFor = member;
    contractsOpen = true;
  }

  function memberActions(member: Member) {
    const items = [];
    if (data.schedules) {
      items.push({
        label: t("settings.users.schedule"),
        icon: CalendarClock,
        onclick: () => openSchedule(member),
      });
      items.push({
        label: t("settings.users.contracts"),
        icon: FileText,
        onclick: () => openContracts(member),
      });
    }
    if (data.rates) {
      items.push({
        label: t("settings.users.rate"),
        icon: BadgeEuro,
        onclick: () => openRate(member),
      });
    }
    if (!member.is_self) {
      items.push({
        label: t("settings.users.revoke"),
        icon: UserMinus,
        danger: true,
        onclick: () => {
          revokeId = member.membership_id;
          confirmRevoke = true;
        },
      });
    }
    return items;
  }

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.users.title"))}</title>
</svelte:head>

<div class="mb-6 flex items-start justify-between">
  <div>
    <a href="/settings" class="text-sm text-text-muted hover:text-text">← {t("settings.title")}</a>
    <h1 class="mt-1 text-xl font-semibold text-text">{t("settings.users.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("settings.users.subtitle")}</p>
  </div>
  <button
    class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
    onclick={() => (showInvite = !showInvite)}
  >
    {t("settings.users.invite")}
  </button>
</div>

{#if showInvite}
  <form
    method="POST"
    action="?/invite"
    use:enhance={() =>
      ({ update }) => {
        void update().then(() => (showInvite = false));
      }}
    class="mb-6 rounded-xl border border-border bg-surface-raised p-4"
  >
    <div class="grid gap-3 sm:grid-cols-3">
      <div>
        <label for="email" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.users.email")}</label
        >
        <input id="email" name="email" type="email" required class={inputClass} />
      </div>
      <div>
        <label for="full_name" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.users.name")}</label
        >
        <input id="full_name" name="full_name" class={inputClass} />
      </div>
      <div>
        <label for="role" class="mb-1 block text-sm font-medium text-text"
          >{t("settings.users.role")}</label
        >
        <select id="role" name="role" class={inputClass}>
          {#each systemRoles as role (role.id)}<option
              value={role.key}
              selected={role.key === "member"}>{localeName(role, locale)}</option
            >{/each}
        </select>
      </div>
    </div>
    {#if form?.error}<p class="mt-2 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
    <div class="mt-4 flex items-center gap-3">
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
        {t("settings.users.send_invite")}
      </button>
      <span class="text-xs text-text-muted">{t("settings.users.invited_hint")}</span>
    </div>
  </form>
{/if}

{#if form?.error && !showInvite}
  <p
    class="mb-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-600 dark:bg-red-950 dark:text-red-400"
  >
    {t(form.error)}
  </p>
{/if}

{#if data.members.length === 0}
  <p
    class="rounded-xl border border-dashed border-border bg-surface-raised p-8 text-center text-sm text-text-muted"
  >
    {t("settings.users.empty")}
  </p>
{:else}
  <ul class="divide-y divide-border rounded-xl border border-border bg-surface-raised">
    {#each data.members as member (member.membership_id)}
      {@const effective = effectiveFor(member.role_ids)}
      <li class="flex items-center gap-3 px-4 py-3 first:rounded-t-xl last:rounded-b-xl">
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2">
            <span class="truncate font-medium text-text">{member.full_name || member.email}</span>
            {#if member.is_self}
              <span class="rounded-full bg-brand/10 px-2 py-0.5 text-[11px] font-medium text-brand"
                >{t("settings.users.you")}</span
              >
            {/if}
            {#if !member.is_active}
              <span
                class="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:bg-amber-950 dark:text-amber-300"
                >{t("settings.users.inactive")}</span
              >
            {/if}
          </div>
          {#if member.full_name}<p class="truncate text-sm text-text-muted">{member.email}</p>{/if}
          {#if data.schedules}
            {@const stale = staleHours(member)}
            {#if stale !== null}
              <p class="mt-0.5 text-xs text-amber-600 dark:text-amber-400">
                {t("settings.users.schedule_stale_hours", {
                  hours: fmtHours(stale),
                  inherited: fmtHours(weekHours(data.defaultSchedule as WorkSchedule)),
                })}
              </p>
            {/if}
          {/if}
          {#if data.rates && rateByUser[member.user_id] != null}
            <p class="mt-0.5 text-xs text-text-muted">
              {t("settings.users.rate_value", { rate: String(rateByUser[member.user_id]) })}
            </p>
          {/if}
        </div>

        <button
          type="button"
          class="shrink-0 rounded-lg border border-border px-2.5 py-1.5 text-xs text-text-muted hover:text-text"
          onclick={() => (expanded = expanded === member.membership_id ? "" : member.membership_id)}
        >
          {effective.includes(WILDCARD)
            ? t("settings.users.effective_all")
            : t("settings.users.effective_count", { count: effective.length })}
        </button>

        {#if memberActions(member).length > 0}
          <ActionsMenu items={memberActions(member)} />
        {/if}
      </li>

      {#if expanded === member.membership_id}
        <li class="bg-surface px-4 py-4">
          <!-- The whole role set, one save (docs/UX.md). A user may hold several roles; their
               permissions are the union. -->
          <form method="POST" action="?/saveRoles" use:enhance class="space-y-3">
            <input type="hidden" name="membership_id" value={member.membership_id} />
            <p class="text-xs font-semibold uppercase tracking-wide text-text-muted">
              {t("settings.users.roles")}
            </p>
            <ul class="grid gap-2 sm:grid-cols-2">
              {#each roles as role (role.id)}
                <li>
                  <label
                    class="flex items-center gap-3 rounded-lg border border-border bg-surface-raised px-3 py-2"
                  >
                    <input
                      type="checkbox"
                      name="role_ids"
                      value={role.id}
                      checked={member.role_ids.includes(role.id)}
                      class="h-4 w-4 rounded border-border"
                    />
                    <span class="flex-1 text-sm text-text">{localeName(role, locale)}</span>
                    {#if role.is_system}
                      <span class="rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted">
                        {t("settings.roles.system")}
                      </span>
                    {/if}
                  </label>
                </li>
              {/each}
            </ul>
            <p class="text-xs text-text-muted">{t("settings.users.system_role_hint")}</p>

            {#if !effective.includes(WILDCARD)}
              <details class="rounded-lg border border-border bg-surface-raised px-3 py-2">
                <summary class="cursor-pointer text-xs font-medium text-text-muted">
                  {t("settings.users.effective")}
                </summary>
                <ul class="mt-2 space-y-1">
                  {#each effective as permission (permission)}
                    <li class="text-xs text-text-muted">
                      {t(`permissions.${permission.split(":")[0]}`)}
                      {#if permission.includes(":")}
                        <span class="text-text-muted/70"
                          >({permission.endsWith(":any")
                            ? t("settings.roles.scope_any")
                            : t("settings.roles.scope_own")})</span
                        >
                      {/if}
                    </li>
                  {/each}
                </ul>
              </details>
            {/if}

            <button
              class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
            >
              {t("settings.users.save_roles")}
            </button>
          </form>
        </li>
      {/if}
    {/each}
  </ul>
{/if}

<!-- This person's working week (#46). One save; contract hours are derived from it. -->
<Modal bind:open={scheduleOpen} title={t("settings.users.schedule")}>
  {#if scheduleFor}
    {#key scheduleFor.user_id}
      <div class="space-y-4">
        <p class="text-sm text-text-muted">
          {scheduleFor.full_name || scheduleFor.email}
        </p>

        <label class="flex items-center gap-2 text-sm text-text">
          <input type="checkbox" bind:checked={inherit} class="h-4 w-4 rounded border-border" />
          {t("settings.users.schedule_inherit")}
        </label>

        {#if inherit}
          <p class="rounded-lg bg-surface px-3 py-2 text-xs text-text-muted">
            {t("settings.users.schedule_inherited_hint", {
              hours: fmtHours(weekHours(data.defaultSchedule as WorkSchedule)),
            })}
          </p>
        {/if}

        <!-- Rendered outside the form on purpose: its TimeInputs post hidden fields of their own
             and a form they are not inside is a form they cannot pollute. -->
        <div class:opacity-50={inherit} class:pointer-events-none={inherit}>
          <WorkScheduleEditor
            bind:schedule={draft}
            formId="user-schedule-form"
            disabled={inherit}
          />
        </div>

        {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}

        <form
          id="user-schedule-form"
          method="POST"
          action="?/saveSchedule"
          class="flex justify-end"
          use:enhance={() =>
            ({ result, update }) => {
              if (result.type === "success") scheduleOpen = false;
              void update({ reset: false });
            }}
        >
          <input type="hidden" name="user_id" value={scheduleFor.user_id} />
          <input type="hidden" name="inherit" value={String(inherit)} />
          <button
            class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          >
            {t("common.save")}
          </button>
        </form>
      </div>
    {/key}
  {/if}
</Modal>

<!-- Employment contracts (#65): contract hours, distinct from scheduled hours; ADV accrues on
     the gap. A changed contract is a new row, so this is add + terminate, never edit-in-place. -->
<Modal bind:open={contractsOpen} title={t("settings.users.contracts")}>
  {#if contractsFor}
    {#key contractsFor.user_id}
      {@const rows = contractsByUser[contractsFor.user_id] ?? []}
      <div class="space-y-4">
        <p class="text-sm text-text-muted">{contractsFor.full_name || contractsFor.email}</p>

        {#if rows.length > 0}
          <ul class="divide-y divide-border rounded-lg border border-border">
            {#each rows as contract (contract.id)}
              <li class="flex items-center gap-3 px-3 py-2 text-sm">
                <div class="min-w-0 flex-1">
                  <span class="font-medium text-text">
                    {t("settings.users.contract_hours_value", {
                      hours: fmtHours(contract.contract_hours_per_week),
                    })}
                  </span>
                  <span class="block text-xs text-text-muted">
                    <!-- Through the shared formatter, so the period honors the personal
                         date-format preference like the rest of the app (#104). -->
                    {fmtNumericDate(contract.start_date)} → {contract.end_date
                      ? fmtNumericDate(contract.end_date)
                      : t("settings.users.contract_open")}
                    · {t("settings.users.contract_scheduled", {
                      hours: fmtHours(contract.scheduled_hours_per_week),
                    })}
                  </span>
                </div>
                {#if !contract.end_date}
                  <form method="POST" action="?/terminateContract" use:enhance>
                    <input type="hidden" name="contract_id" value={contract.id} />
                    <input type="hidden" name="end_date" value={todayIso} />
                    <button
                      class="rounded-lg border border-border px-2 py-1 text-xs text-text-muted hover:text-text"
                      title={t("settings.users.contract_terminate")}
                    >
                      {t("settings.users.contract_terminate")}
                    </button>
                  </form>
                {/if}
                <form method="POST" action="?/deleteContract" use:enhance>
                  <input type="hidden" name="contract_id" value={contract.id} />
                  <button
                    class="rounded-lg p-1 text-text-muted hover:text-red-600 dark:hover:text-red-400"
                    title={t("common.delete")}
                    aria-label={t("common.delete")}
                  >
                    <Trash2 size={14} />
                  </button>
                </form>
              </li>
            {/each}
          </ul>
        {:else}
          <p class="rounded-lg bg-surface px-3 py-2 text-xs text-text-muted">
            {t("settings.users.contract_empty")}
          </p>
        {/if}

        <!-- Keyed on the row count: a successful add re-mounts the form, which is what clears
             the DateInputs — their display text is component state a form reset cannot reach. -->
        {#key rows.length}
          <form
            method="POST"
            action="?/saveContract"
            class="space-y-3 border-t border-border pt-4"
            use:enhance={() =>
              ({ result, update }) => {
                if (result.type === "success") void update({ reset: true });
                else void update({ reset: false });
              }}
          >
            <input type="hidden" name="user_id" value={contractsFor.user_id} />
            <p class="text-xs font-semibold uppercase tracking-wide text-text-muted">
              {t("settings.users.contract_add")}
            </p>
            <div class="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <!-- Shared DateInput, never a native type="date": browsers render those after the
                 browser locale, ignoring the personal date-format preference (#104, docs/UX.md). -->
              <div>
                <label for="c-start" class="mb-1 block text-xs text-text-muted"
                  >{t("settings.users.contract_start")}</label
                >
                <DateInput id="c-start" name="start_date" required />
              </div>
              <div>
                <label for="c-end" class="mb-1 block text-xs text-text-muted"
                  >{t("settings.users.contract_end")}</label
                >
                <DateInput id="c-end" name="end_date" />
              </div>
              <div>
                <label for="c-hours" class="mb-1 block text-xs text-text-muted"
                  >{t("settings.users.contract_hours")}</label
                >
                <input
                  id="c-hours"
                  name="contract_hours_per_week"
                  inputmode="decimal"
                  required
                  placeholder="38"
                  class={inputClass}
                />
              </div>
            </div>
            <p class="text-xs text-text-muted">{t("settings.users.contract_hint")}</p>
            {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">
                {t(form.error)}
              </p>{/if}
            <div class="flex justify-end">
              <button
                class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
              >
                {t("settings.users.contract_add")}
              </button>
            </div>
          </form>
        {/key}
      </div>
    {/key}
  {/if}
</Modal>

<!-- This person's hourly rate (#82). Salary-adjacent — its own permission gates edit. -->
<Modal bind:open={rateOpen} title={t("settings.users.rate")}>
  {#if rateFor}
    {#key rateFor.user_id}
      <form
        method="POST"
        action="?/saveRate"
        class="space-y-4"
        use:enhance={() =>
          ({ result, update }) => {
            if (result.type === "success") rateOpen = false;
            void update({ reset: false });
          }}
      >
        <input type="hidden" name="user_id" value={rateFor.user_id} />
        <p class="text-sm text-text-muted">{rateFor.full_name || rateFor.email}</p>
        <div>
          <label for="hourly_rate" class="mb-1 block text-sm font-medium text-text">
            {t("settings.users.rate_label")}
          </label>
          <input
            id="hourly_rate"
            name="hourly_rate"
            inputmode="decimal"
            bind:value={rateDraft}
            disabled={!data.canEditRates}
            placeholder={t("settings.users.rate_placeholder")}
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("settings.users.rate_hint")}</p>
        </div>
        {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
        {#if data.canEditRates}
          <div class="flex justify-end">
            <button
              class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
            >
              {t("common.save")}
            </button>
          </div>
        {/if}
      </form>
    {/key}
  {/if}
</Modal>

<ConfirmDialog
  bind:open={confirmRevoke}
  title={t("settings.users.revoke")}
  message={t("settings.users.revoke_confirm")}
  confirmLabel={t("settings.users.revoke")}
  action="?/revoke"
  fields={{ membership_id: revokeId }}
/>
