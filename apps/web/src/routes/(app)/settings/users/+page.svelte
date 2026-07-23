<script lang="ts">
  import { ShieldOff, UserMinus } from "@lucide/svelte";
  import Avatar from "$lib/core/ui/Avatar.svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { localeName } from "$lib/core/roles/name";
  import { effectivePermissions, WILDCARD } from "$lib/core/roles/permissions";
  import { InFlight } from "$lib/core/submit.svelte";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  // Employment editors (schedule, contracts, recurring, rate) as one shared surface, so this page
  // and the team leave roster can't drift.
  import EmploymentModals, {
    employmentMenuItems,
    type OpenEmployment,
  } from "$lib/modules/leave/EmploymentModals.svelte";
  import { fmtHours, type LeaveTypeInfo } from "$lib/modules/leave/format";
  import { weekHours, type WorkSchedule } from "$lib/modules/leave/schedule";

  let { data, form } = $props();

  let showInvite = $state(false);
  const busy = new InFlight();
  let revokeId = $state("");
  let confirmRevoke = $state(false);
  let resetTwoFactorId = $state("");
  let confirmResetTwoFactor = $state(false);
  let expanded = $state("");

  // The tenant's own roles, fetched once by `settings/+layout.server.ts`. There is no hard-coded
  // list of four any more: an agency defines its own (issue #19).
  const roles = $derived(data.roles);
  const systemRoles = $derived(roles.filter((r) => r.is_system));
  const locale = $derived(data.locale ?? "nl");

  const effectiveFor = (roleIds: string[]) => effectivePermissions(roles, roleIds);

  // Employment data (schedule, contracts, recurring, rate) lives on the person, not under
  // Instellingen → Verlof (#46). Its ⋯ actions and modals are the shared EmploymentModals, so this
  // page and the team leave roster stay in lockstep; this page keeps the roster-level readouts.
  type Member = (typeof data.members)[number];
  const profileByUser = $derived(Object.fromEntries(data.profiles.map((p) => [p.user_id, p])));

  // The opener the shared modals hand back through `register`; a ⋯ item calls it for a member.
  let openEmployment = $state<OpenEmployment>();

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

  // --- hourly rate readouts (#82, #113) -------------------------------------------
  // Salary-adjacent, so its own permission (`leave.rate.read`/`.write`), not `profile.manage`.
  // The edit surface is the shared rate modal; the roster still prints the effective figure.
  const rateByUser = $derived(
    Object.fromEntries((data.rateRows ?? []).map((r) => [r.user_id, r.hourly_rate])),
  );
  // The effective rate (#113): the org default fills in where no personal rate is set, and
  // the roster says so — a defaulted figure must not read as an entered one.
  const effectiveRateByUser = $derived(
    Object.fromEntries((data.rateRows ?? []).map((r) => [r.user_id, r.effective_hourly_rate])),
  );

  function memberActions(member: Member) {
    // Schedule, contracts, recurring and (rate, where permitted) come from the shared helper;
    // this page adds the trust actions (2FA reset, revoke) that only belong on the roster.
    const items = employmentMenuItems(member, openEmployment, {
      schedules: data.schedules,
      rates: data.rates,
    });
    if (!member.is_self) {
      // 2FA reset is the lost-phone escape hatch (docs/TWOFACTOR.md) — only offered where the
      // member actually has 2FA, and confirmed like every destructive trust change.
      if (member.two_factor_enabled) {
        items.push({
          label: t("settings.users.reset_two_factor"),
          icon: ShieldOff,
          danger: true,
          onclick: () => {
            resetTwoFactorId = member.membership_id;
            confirmResetTwoFactor = true;
          },
        });
      }
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

<!-- Every employment editor for a member (schedule, contracts, recurring, rate) + the ADV
     "N days placed" line, shared with the team leave roster. One instance; each row's ⋯ menu
     opens it through `openEmployment`. -->
{#if data.schedules || data.rates}
  <EmploymentModals
    register={(open) => (openEmployment = open)}
    profiles={data.profiles}
    contracts={data.contracts}
    recurring={data.recurring}
    leaveTypes={data.leaveTypes as LeaveTypeInfo[]}
    orgDefaultSchedule={data.defaultSchedule as WorkSchedule}
    {rateByUser}
    canEditRates={data.canEditRates}
    {form}
  />
{/if}

{#if showInvite}
  <form
    method="POST"
    action="?/invite"
    use:enhance={busy.wrap("invite", () => ({ update }) => {
      void update().then(() => (showInvite = false));
    })}
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
    <label class="mt-3 flex items-center gap-2 text-sm text-text">
      <input type="checkbox" name="send_email" checked />
      {t("settings.users.send_invite_email")}
    </label>
    <div class="mt-4 flex items-center gap-3">
      <Button loading={busy.is("invite")}>
        {t("settings.users.send_invite")}
      </Button>
      <span class="text-xs text-text-muted">{t("settings.users.invited_hint")}</span>
    </div>
  </form>
{/if}

{#if form?.invited && form?.inviteEmailSent === false}
  <!-- The invite stood, the mail did not go (#161) — the admin must know, not find out. -->
  <p
    class="mb-4 rounded-lg bg-amber-50 px-3 py-2 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-300"
  >
    {t(form.inviteEmailError ?? "settings.users.invite_email_failed")}
  </p>
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
        <Avatar
          name={member.full_name}
          email={member.email}
          avatarUrl={member.avatar_url ?? null}
          size="md"
        />
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
            {#if data.restrictedMembershipIds.includes(member.membership_id)}
              <!-- A visibility restriction (#191) must be visible at a glance — a restricted
                   member quietly seeing "everything they know of" reads as data loss. -->
              <a
                href="/settings/company-groups"
                class="rounded-full px-2 py-0.5 text-[11px] font-medium text-text-muted ring-1 ring-inset ring-border hover:text-brand"
                title={t("settings.users.restricted_hint")}>{t("settings.users.restricted")}</a
              >
            {/if}
            {#if member.two_factor_enabled}
              <span
                class="rounded-full bg-green-100 px-2 py-0.5 text-[11px] font-medium text-green-700 dark:bg-green-950 dark:text-green-300"
                >{t("settings.users.two_factor_on")}</span
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
          {#if data.rates && effectiveRateByUser[member.user_id] != null}
            <p class="mt-0.5 text-xs text-text-muted">
              {t("settings.users.rate_value", {
                rate: String(effectiveRateByUser[member.user_id]),
              })}
              {#if rateByUser[member.user_id] == null}
                {t("settings.users.rate_default_marker")}
              {/if}
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
               permissions are the union. `reset: false`: the ticks are component state and a
               form reset would revert the DOM behind that state's back (docs/UX.md). -->
          <form
            method="POST"
            action="?/saveRoles"
            class="space-y-3"
            use:enhance={busy.wrap(
              `roles:${member.membership_id}`,
              () =>
                ({ update }) =>
                  update({ reset: false }),
            )}
          >
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
                    <FormCheckbox
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

            <Button loading={busy.is(`roles:${member.membership_id}`)}>
              {t("settings.users.save_roles")}
            </Button>
          </form>
        </li>
      {/if}
    {/each}
  </ul>
{/if}

<ConfirmDialog
  bind:open={confirmResetTwoFactor}
  title={t("settings.users.reset_two_factor")}
  message={t("settings.users.reset_two_factor_confirm")}
  confirmLabel={t("settings.users.reset_two_factor")}
  action="?/resetTwoFactor"
  fields={{ membership_id: resetTwoFactorId }}
/>

<ConfirmDialog
  bind:open={confirmRevoke}
  title={t("settings.users.revoke")}
  message={t("settings.users.revoke_confirm")}
  confirmLabel={t("settings.users.revoke")}
  action="?/revoke"
  fields={{ membership_id: revokeId }}
/>
