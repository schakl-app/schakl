<script lang="ts">
  import { CalendarPlus, Pencil, Power, Plus, Sparkles, Trash2 } from "@lucide/svelte";
  import { tick } from "svelte";

  import { enhance } from "$app/forms";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import I18nTextField from "$lib/core/ui/I18nTextField.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { LABEL_COLORS, labelDotClass } from "$lib/core/ui/colors";
  import {
    fmtHours,
    holidayName,
    typeLabel,
    type HolidayInfo,
    type LeaveTypeInfo,
  } from "$lib/modules/leave/format";
  import WorkScheduleEditor from "$lib/modules/leave/WorkScheduleEditor.svelte";
  import { cloneSchedule, type WorkSchedule } from "$lib/modules/leave/schedule";

  let { data, form } = $props();

  const types = $derived(data.types as LeaveTypeInfo[]);
  const trackedTypes = $derived(types.filter((lt) => lt.tracks_balance && lt.active));
  const hoursByUser = $derived(
    Object.fromEntries(data.profiles.map((p) => [p.user_id, Number(p.hours_per_week)])),
  );
  const inheritedByUser = $derived(
    Object.fromEntries(data.profiles.map((p) => [p.user_id, p.schedule === null])),
  );

  // The org default, editable here; a person's own schedule lives on the person.
  let defaultSchedule = $state(cloneSchedule(data.defaultSchedule as WorkSchedule));
  const entitledByUserType = $derived.by(() => {
    const byKey: Record<string, number> = {};
    for (const ent of data.entitlements) {
      const key = `${ent.user_id}|${ent.leave_type_id}`;
      byKey[key] = (byKey[key] ?? 0) + Number(ent.hours);
    }
    return byKey;
  });

  // --- leave type editing ------------------------------------------------------
  let typeOpen = $state(false);
  let editType = $state<LeaveTypeInfo | null>(null);
  let typeColor = $state("emerald");
  let deleteId = $state("");
  let confirmDelete = $state(false);

  function openType(lt: LeaveTypeInfo | null) {
    editType = lt;
    typeColor = lt?.color ?? "emerald";
    typeOpen = true;
  }

  // Activate/deactivate lives in the ⋯ menu (non-destructive, no confirm) — posted through
  // one hidden form submitted programmatically.
  let toggleForm: HTMLFormElement | undefined = $state();
  let toggleId = $state("");
  let toggleActive = $state("true");
  function toggleType(lt: LeaveTypeInfo) {
    toggleId = lt.id;
    toggleActive = String(!lt.active);
    void tick().then(() => toggleForm?.requestSubmit());
  }

  // --- holidays (#47) --------------------------------------------------------------
  const holidays = $derived(data.holidays as HolidayInfo[]);

  let holidayOpen = $state(false);
  let editHoliday = $state<HolidayInfo | null>(null);
  let holidayDate = $state("");
  let holidayDeleteId = $state("");
  let confirmHolidayDelete = $state(false);

  function openHoliday(holiday: HolidayInfo | null) {
    editHoliday = holiday;
    holidayDate = holiday?.date ?? "";
    holidayOpen = true;
  }

  // Activate/deactivate: non-destructive, so it lives in the ⋯ menu without a confirm.
  let holidayToggleForm: HTMLFormElement | undefined = $state();
  let holidayToggleId = $state("");
  let holidayToggleActive = $state("true");
  function toggleHoliday(holiday: HolidayInfo) {
    holidayToggleId = holiday.id;
    holidayToggleActive = String(!holiday.active);
    void tick().then(() => holidayToggleForm?.requestSubmit());
  }

  // --- member editing ------------------------------------------------------------
  let memberOpen = $state(false);
  let editMember = $state<(typeof data.members)[number] | null>(null);

  function openMember(member: (typeof data.members)[number]) {
    editMember = member;
    memberOpen = true;
  }

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.leave.title"))}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings" class="text-sm text-text-muted hover:text-text">← {t("settings.title")}</a>
  <h1 class="mt-2 text-xl font-semibold text-text">{t("settings.leave.title")}</h1>
  <p class="mt-1 text-sm text-text-muted">{t("settings.leave.subtitle")}</p>
</div>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600">{t(form.error)}</p>
{/if}

<!-- Leave types -->
<section class="mb-8">
  <div class="mb-3 flex items-center justify-between">
    <h2 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
      {t("settings.leave.types_heading")}
    </h2>
    <button
      type="button"
      class="rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
      onclick={() => openType(null)}
    >
      {t("settings.leave.new_type")}
    </button>
  </div>
  <div class="overflow-hidden rounded-xl border border-border bg-surface-raised">
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-xs text-text-muted">
            <th class="px-4 py-2 font-medium">{t("settings.leave.type_label")}</th>
            <th class="px-2 py-2 font-medium">{t("settings.leave.type_props")}</th>
            <th class="px-2 py-2 text-right font-medium">{t("settings.leave.type_weeks")}</th>
            <th class="px-2 py-2 text-right font-medium">{t("settings.leave.type_carry")}</th>
            <th class="px-2 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          {#each types as lt (lt.id)}
            <tr class={lt.active ? "" : "opacity-50"}>
              <td class="px-4 py-2">
                <span class="inline-flex items-center gap-2 font-medium text-text">
                  <span class="h-2.5 w-2.5 rounded-full {labelDotClass(lt.color)}"></span>
                  {typeLabel(lt, data.locale)}
                </span>
                <p class="mt-0.5 text-xs text-text-muted">{lt.key}</p>
              </td>
              <td class="px-2 py-2">
                <span class="flex flex-wrap gap-1">
                  {#if lt.paid}
                    <span class="rounded-full bg-surface px-2 py-0.5 text-xs text-text-muted">
                      {t("settings.leave.prop_paid")}
                    </span>
                  {/if}
                  {#if lt.tracks_balance}
                    <span class="rounded-full bg-surface px-2 py-0.5 text-xs text-text-muted">
                      {t("settings.leave.prop_balance")}
                    </span>
                  {/if}
                  {#if lt.requires_approval}
                    <span class="rounded-full bg-surface px-2 py-0.5 text-xs text-text-muted">
                      {t("settings.leave.prop_approval")}
                    </span>
                  {/if}
                </span>
              </td>
              <td class="px-2 py-2 text-right tabular-nums text-text">
                {lt.default_weeks != null ? fmtHours(lt.default_weeks) : "—"}
              </td>
              <td class="px-2 py-2 text-right tabular-nums text-text">
                {lt.carry_over_months != null
                  ? t("settings.leave.carry_months", { months: lt.carry_over_months })
                  : "—"}
              </td>
              <td class="px-2 py-2 text-right">
                <ActionsMenu
                  compact
                  items={[
                    { label: t("common.edit"), icon: Pencil, onclick: () => openType(lt) },
                    {
                      label: lt.active
                        ? t("settings.leave.deactivate")
                        : t("settings.leave.activate"),
                      icon: Power,
                      onclick: () => toggleType(lt),
                    },
                    {
                      label: t("common.delete"),
                      icon: Trash2,
                      danger: true,
                      onclick: () => {
                        deleteId = lt.id;
                        confirmDelete = true;
                      },
                    },
                  ]}
                />
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </div>
</section>

<!-- The schedule new employees inherit (#46) -->
<section class="mb-8">
  <div class="mb-3">
    <h2 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
      {t("settings.leave.schedule_heading")}
    </h2>
    <p class="mt-1 text-sm text-text-muted">{t("settings.leave.schedule_hint")}</p>
  </div>
  <div class="rounded-xl border border-border bg-surface p-4">
    <!-- The grid lives outside the <form> and posts through `form=`, so its TimeInputs' hidden
         fields never reach the action (docs/UX.md — one save per surface). -->
    <WorkScheduleEditor bind:schedule={defaultSchedule} formId="default-schedule-form" />
    {#if form?.scheduleSaved}
      <p class="mt-3 text-sm text-green-600">{t("settings.leave.schedule_saved")}</p>
    {/if}
    <form
      id="default-schedule-form"
      method="POST"
      action="?/saveSchedule"
      class="mt-3 flex justify-end"
      use:enhance={() =>
        ({ update }) =>
          update({ reset: false })}
    >
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
        {t("common.save")}
      </button>
    </form>
  </div>
</section>

<!-- Goedkeuringsbeleid (#110): may approvers manage their own leave? -->
<section class="mb-8">
  <div class="mb-3">
    <h2 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
      {t("settings.leave.approval_heading")}
    </h2>
  </div>
  <div class="rounded-xl border border-border bg-surface p-4">
    <form
      method="POST"
      action="?/savePolicy"
      use:enhance={() =>
        ({ update }) =>
          update({ reset: false })}
    >
      <label class="flex items-start gap-3 text-sm text-text">
        <FormCheckbox
          name="self_approval"
          checked={data.selfApproval}
          class="mt-0.5 h-4 w-4 rounded border-border"
        />
        <span>
          {t("settings.leave.self_approval")}
          <span class="mt-0.5 block text-xs text-text-muted">
            {t("settings.leave.self_approval_hint")}
          </span>
        </span>
      </label>
      {#if form?.policySaved}
        <p class="mt-3 text-sm text-green-600">{t("settings.leave.policy_saved")}</p>
      {/if}
      <div class="mt-3 flex justify-end">
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("common.save")}
        </button>
      </div>
    </form>
  </div>
</section>

<!-- Roostervrije dagen (#107): how far ahead the generator plans -->
<section class="mb-8">
  <div class="mb-3">
    <h2 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
      {t("settings.leave.recurring_heading")}
    </h2>
  </div>
  <div class="rounded-xl border border-border bg-surface p-4">
    <form
      method="POST"
      action="?/saveHorizon"
      use:enhance={() =>
        ({ update }) =>
          update({ reset: false })}
    >
      <label for="recurring-horizon" class="mb-1 block text-sm font-medium text-text">
        {t("settings.leave.recurring_horizon")}
      </label>
      <input
        id="recurring-horizon"
        name="recurring_horizon_months"
        type="number"
        min="1"
        max="24"
        value={data.recurringHorizonMonths}
        class="w-28 rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
      />
      <p class="mt-1 text-xs text-text-muted">{t("settings.leave.recurring_horizon_hint")}</p>
      {#if form?.horizonSaved}
        <p class="mt-3 text-sm text-green-600">{t("settings.leave.recurring_horizon_saved")}</p>
      {/if}
      <div class="mt-3 flex justify-end">
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("common.save")}
        </button>
      </div>
    </form>
  </div>
</section>

<!-- Standaard uurtarief (#113): the house rate a per-employee rate (#82) overrides -->
<section class="mb-8">
  <div class="mb-3">
    <h2 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
      {t("settings.leave.rate_heading")}
    </h2>
  </div>
  <div class="rounded-xl border border-border bg-surface p-4">
    <form
      method="POST"
      action="?/saveDefaultRate"
      use:enhance={() =>
        ({ update }) =>
          update({ reset: false })}
    >
      <label for="default-hourly-rate" class="mb-1 block text-sm font-medium text-text">
        {t("settings.leave.default_rate")}
      </label>
      <input
        id="default-hourly-rate"
        name="default_hourly_rate"
        type="number"
        min="0"
        step="0.01"
        value={data.defaultHourlyRate ?? ""}
        class="w-36 rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
      />
      <p class="mt-1 text-xs text-text-muted">{t("settings.leave.default_rate_hint")}</p>
      {#if form?.rateSaved}
        <p class="mt-3 text-sm text-green-600">{t("settings.leave.default_rate_saved")}</p>
      {/if}
      <div class="mt-3 flex justify-end">
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("common.save")}
        </button>
      </div>
    </form>
  </div>
</section>

<!-- Feestdagen (#47): a holiday on a scheduled working day costs no leave hours -->
<section class="mb-8">
  <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
    <div>
      <div class="flex items-center gap-3">
        <h2 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
          {t("settings.leave.holidays_heading")}
        </h2>
        <div class="flex items-center gap-1 text-sm" data-sveltekit-preload-data="hover">
          <a
            href="?year={data.year - 1}"
            class="rounded px-1.5 py-0.5 text-text-muted hover:text-brand">‹</a
          >
          <span class="font-medium text-text">{data.year}</span>
          <a
            href="?year={data.year + 1}"
            class="rounded px-1.5 py-0.5 text-text-muted hover:text-brand">›</a
          >
        </div>
      </div>
      <p class="mt-1 text-sm text-text-muted">{t("settings.leave.holidays_hint")}</p>
    </div>
    <div class="flex items-center gap-2">
      <form method="POST" action="?/importHolidays" use:enhance>
        <input type="hidden" name="year" value={data.year} />
        <button
          class="flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-brand hover:text-brand"
        >
          <CalendarPlus size={14} />
          {t("settings.leave.import_holidays", { year: data.year })}
        </button>
      </form>
      <button
        type="button"
        class="flex items-center gap-2 rounded-lg bg-brand px-3 py-1.5 text-sm font-medium text-white hover:opacity-90"
        onclick={() => openHoliday(null)}
      >
        <Plus size={14} />
        {t("settings.leave.new_holiday")}
      </button>
    </div>
  </div>

  {#if form?.imported}
    <p class="mb-3 text-sm text-green-600">
      {t("settings.leave.imported", {
        created: form.imported.created,
        updated: form.imported.updated,
        skipped: form.imported.skipped,
      })}
    </p>
  {/if}

  <div class="overflow-hidden rounded-xl border border-border bg-surface-raised">
    {#if holidays.length === 0}
      <p class="p-6 text-sm text-text-muted">{t("settings.leave.holidays_empty")}</p>
    {:else}
      <ul class="divide-y divide-border">
        {#each holidays as holiday (holiday.id)}
          <li class="flex items-center gap-3 px-4 py-2 {holiday.active ? '' : 'opacity-50'}">
            <span class="w-32 shrink-0 tabular-nums text-sm text-text-muted"
              >{fmtNumericDate(holiday.date)}</span
            >
            <span class="min-w-0 flex-1 truncate text-sm font-medium text-text">
              {holidayName(holiday.name_i18n, data.locale)}
            </span>
            {#if holiday.source === "manual"}
              <span class="rounded-full bg-surface px-2 py-0.5 text-xs text-text-muted">
                {t("settings.leave.holiday_manual")}
              </span>
            {/if}
            {#if !holiday.active}
              <span class="rounded-full bg-surface px-2 py-0.5 text-xs text-text-muted">
                {t("settings.leave.holiday_inactive")}
              </span>
            {/if}
            <ActionsMenu
              compact
              items={[
                { label: t("common.edit"), icon: Pencil, onclick: () => openHoliday(holiday) },
                {
                  label: holiday.active
                    ? t("settings.leave.deactivate")
                    : t("settings.leave.activate"),
                  icon: Power,
                  onclick: () => toggleHoliday(holiday),
                },
                {
                  label: t("common.delete"),
                  icon: Trash2,
                  danger: true,
                  onclick: () => {
                    holidayDeleteId = holiday.id;
                    confirmHolidayDelete = true;
                  },
                },
              ]}
            />
          </li>
        {/each}
      </ul>
    {/if}
  </div>
</section>

<!-- Contract hours + yearly entitlements -->
<section>
  <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
    <div class="flex items-center gap-3">
      <h2 class="text-xs font-semibold uppercase tracking-wide text-text-muted">
        {t("settings.leave.team_heading", { year: data.year })}
      </h2>
      <div class="flex items-center gap-1 text-sm" data-sveltekit-preload-data="hover">
        <a
          href="?year={data.year - 1}"
          class="rounded px-1.5 py-0.5 text-text-muted hover:text-brand">‹</a
        >
        <span class="font-medium text-text">{data.year}</span>
        <a
          href="?year={data.year + 1}"
          class="rounded px-1.5 py-0.5 text-text-muted hover:text-brand">›</a
        >
      </div>
    </div>
    <form method="POST" action="?/generate" use:enhance>
      <input type="hidden" name="year" value={data.year} />
      <button
        class="flex items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-brand hover:text-brand"
        title={t("settings.leave.generate_hint")}
      >
        <Sparkles size={14} />
        {t("settings.leave.generate")}
      </button>
    </form>
  </div>
  {#if form?.generated !== undefined}
    <p class="mb-3 text-sm text-green-600">
      {t("settings.leave.generated", { count: form.generated })}
    </p>
  {/if}
  <div class="overflow-hidden rounded-xl border border-border bg-surface-raised">
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-border text-left text-xs text-text-muted">
            <th class="px-4 py-2 font-medium">{t("leave.team.member")}</th>
            <th class="px-2 py-2 text-right font-medium">{t("leave.team.contract_hours")}</th>
            {#each trackedTypes as lt (lt.id)}
              <th class="px-2 py-2 text-right font-medium">{typeLabel(lt, data.locale)}</th>
            {/each}
            <th class="px-2 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-border">
          {#each data.members as member (member.user_id)}
            <tr>
              <td class="px-4 py-2 font-medium text-text">
                {member.full_name || member.email}
              </td>
              <!-- Derived from the person's schedule, so it is read here and edited there. -->
              <td class="px-2 py-2 text-right tabular-nums text-text">
                <a
                  href="/settings/users"
                  class="hover:text-brand hover:underline"
                  title={t("settings.leave.contract_hours_derived")}
                >
                  {fmtHours(hoursByUser[member.user_id] ?? 40)}
                </a>
                {#if inheritedByUser[member.user_id] !== false}
                  <span class="ml-1 text-xs text-text-muted"
                    >{t("settings.leave.schedule_inherited_short")}</span
                  >
                {/if}
              </td>
              {#each trackedTypes as lt (lt.id)}
                <td class="px-2 py-2 text-right tabular-nums text-text">
                  {fmtHours(entitledByUserType[`${member.user_id}|${lt.id}`] ?? 0)}
                </td>
              {/each}
              <td class="px-2 py-2 text-right">
                <ActionsMenu
                  compact
                  items={[
                    { label: t("common.edit"), icon: Pencil, onclick: () => openMember(member) },
                  ]}
                />
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  </div>
</section>

<!-- Leave type create/edit (one save per surface) -->
<Modal bind:open={typeOpen} title={editType ? t("common.edit") : t("settings.leave.new_type")}>
  {#key editType?.id ?? "new"}
    <form
      method="POST"
      action="?/saveType"
      class="space-y-4"
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") typeOpen = false;
          void update({ reset: false });
        }}
    >
      {#if editType}
        <input type="hidden" name="id" value={editType.id} />
      {:else}
        <div>
          <label class="mb-1 block text-xs font-medium text-text-muted" for="type-key">
            {t("settings.leave.type_key")}
          </label>
          <input
            id="type-key"
            name="key"
            required
            pattern="[a-z0-9_]+"
            placeholder="study_leave"
            class={inputClass}
          />
        </div>
      {/if}
      {#key editType?.id ?? "new"}
        <I18nTextField
          label={t("common.label_field")}
          basename="label"
          values={editType?.label_i18n ?? {}}
          idPrefix="type-label"
        />
      {/key}
      <div>
        <span class="mb-1 block text-xs font-medium text-text-muted">
          {t("settings.leave.type_color")}
        </span>
        <input type="hidden" name="color" value={typeColor} />
        <div class="flex flex-wrap gap-1.5">
          {#each LABEL_COLORS as color (color)}
            <button
              type="button"
              aria-label={color}
              class="h-6 w-6 rounded-full {labelDotClass(color)} {typeColor === color
                ? 'ring-2 ring-text ring-offset-1'
                : ''}"
              onclick={() => (typeColor = color)}
            ></button>
          {/each}
        </div>
      </div>
      <div class="space-y-2">
        <label class="flex items-center gap-2 text-sm text-text">
          <FormCheckbox name="paid" checked={editType?.paid ?? true} />
          {t("settings.leave.prop_paid")}
        </label>
        <label class="flex items-center gap-2 text-sm text-text">
          <FormCheckbox name="tracks_balance" checked={editType?.tracks_balance ?? false} />
          {t("settings.leave.prop_balance_long")}
        </label>
        <label class="flex items-center gap-2 text-sm text-text">
          <FormCheckbox name="requires_approval" checked={editType?.requires_approval ?? true} />
          {t("settings.leave.prop_approval_long")}
        </label>
      </div>
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label class="mb-1 block text-xs font-medium text-text-muted" for="type-weeks">
            {t("settings.leave.type_weeks_long")}
          </label>
          <input
            id="type-weeks"
            name="default_weeks"
            type="number"
            min="0"
            max="52"
            step="0.5"
            value={editType?.default_weeks ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label class="mb-1 block text-xs font-medium text-text-muted" for="type-carry">
            {t("settings.leave.type_carry_long")}
          </label>
          <input
            id="type-carry"
            name="carry_over_months"
            type="number"
            min="0"
            max="120"
            value={editType?.carry_over_months ?? ""}
            class={inputClass}
          />
        </div>
      </div>
      <input type="hidden" name="position" value={editType?.position ?? types.length * 10 + 10} />
      {#if form?.error}<p class="text-sm text-red-600">{t(form.error)}</p>{/if}
      <div class="flex justify-end">
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("common.save")}
        </button>
      </div>
    </form>
  {/key}
</Modal>

<!-- Member contract hours + entitlements (one save per surface) -->
<Modal bind:open={memberOpen} title={editMember?.full_name || editMember?.email || ""}>
  {#if editMember}
    {#key editMember.user_id}
      <form
        method="POST"
        action="?/saveMember"
        class="space-y-4"
        use:enhance={() =>
          ({ result, update }) => {
            if (result.type === "success") memberOpen = false;
            void update({ reset: false });
          }}
      >
        <input type="hidden" name="user_id" value={editMember.user_id} />
        <input type="hidden" name="year" value={data.year} />
        <p class="rounded-lg bg-surface px-3 py-2 text-xs text-text-muted">
          {t("settings.leave.contract_hours_derived")}
          <a href="/settings/users" class="text-brand hover:underline">
            {t("settings.leave.manage_schedule")}
          </a>
        </p>
        <fieldset>
          <legend class="mb-1 text-xs font-medium text-text-muted">
            {t("settings.leave.entitlements_for", { year: data.year })}
          </legend>
          <div class="space-y-2">
            {#each trackedTypes as lt (lt.id)}
              <div class="flex items-center gap-3">
                <span class="flex flex-1 items-center gap-2 text-sm text-text">
                  <span class="h-2 w-2 rounded-full {labelDotClass(lt.color)}"></span>
                  {typeLabel(lt, data.locale)}
                </span>
                <input
                  name="ent_{lt.id}"
                  type="number"
                  min="0"
                  step="0.5"
                  value={entitledByUserType[`${editMember.user_id}|${lt.id}`] ?? 0}
                  class="w-28 rounded-lg border border-border px-3 py-1.5 text-right text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
                />
              </div>
            {/each}
          </div>
        </fieldset>
        {#if form?.error}<p class="text-sm text-red-600">{t(form.error)}</p>{/if}
        <div class="flex justify-end">
          <button
            class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          >
            {t("common.save")}
          </button>
        </div>
      </form>
    {/key}
  {/if}
</Modal>

<!-- Holiday create/edit. Names are tenant data (nl/en), never message keys. -->
<Modal
  bind:open={holidayOpen}
  title={editHoliday ? t("common.edit") : t("settings.leave.new_holiday")}
>
  {#key editHoliday?.id ?? "new"}
    <form
      method="POST"
      action="?/saveHoliday"
      class="space-y-4"
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") holidayOpen = false;
          void update({ reset: false });
        }}
    >
      {#if editHoliday}
        <input type="hidden" name="id" value={editHoliday.id} />
      {/if}
      <div>
        <label class="mb-1 block text-xs font-medium text-text-muted" for="holiday-date">
          {t("settings.leave.holiday_date")}
        </label>
        <DateInput id="holiday-date" name="date" bind:value={holidayDate} required />
      </div>
      {#key editHoliday?.id ?? "new"}
        <I18nTextField
          label={t("common.name_field")}
          basename="name"
          values={editHoliday?.name_i18n ?? {}}
          idPrefix="holiday-name"
        />
      {/key}
      {#if form?.error}<p class="text-sm text-red-600">{t(form.error)}</p>{/if}
      <div class="flex justify-end">
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("common.save")}
        </button>
      </div>
    </form>
  {/key}
</Modal>

<!-- Hidden activate/deactivate forms, submitted from the ⋯ menus -->
<form bind:this={toggleForm} method="POST" action="?/toggleType" class="hidden" use:enhance>
  <input type="hidden" name="id" value={toggleId} />
  <input type="hidden" name="active" value={toggleActive} />
</form>

<form
  bind:this={holidayToggleForm}
  method="POST"
  action="?/toggleHoliday"
  class="hidden"
  use:enhance
>
  <input type="hidden" name="id" value={holidayToggleId} />
  <input type="hidden" name="active" value={holidayToggleActive} />
</form>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("settings.leave.delete_confirm")}
  action="?/deleteType"
  fields={{ id: deleteId }}
/>

<ConfirmDialog
  bind:open={confirmHolidayDelete}
  title={t("common.delete")}
  message={t("settings.leave.holiday_delete_confirm")}
  action="?/deleteHoliday"
  fields={{ id: holidayDeleteId }}
/>
