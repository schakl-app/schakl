<script lang="ts">
  /**
   * One form for requesting + editing leave (docs/UX.md: one save per surface). Hours are
   * suggested from the date range × contract hours but stay editable for part days.
   * Managers may pass `userOptions` to register leave for someone else (e.g. a sick call).
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { getLocale } from "$lib/paraglide/runtime";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";

  import { fmtHours, hoursToDays, suggestedHours, typeLabel, type LeaveTypeInfo } from "./format";

  interface RequestValues {
    id: string;
    leave_type_id: string;
    start_date: string;
    end_date: string;
    hours: string | number;
    note: string | null;
  }

  let {
    types,
    hoursPerWeek,
    request = null,
    balances = {},
    userOptions = null,
    action = "?/create",
    error = null,
    ondone,
  }: {
    types: LeaveTypeInfo[];
    hoursPerWeek: number | string;
    /** Existing request → edit mode (posts its id). */
    request?: RequestValues | null;
    /** remaining_hours by leave_type_id, to show the balance next to the type. */
    balances?: Record<string, number>;
    /** Manager register-for-someone flow: member picker options. */
    userOptions?: { value: string; label: string }[] | null;
    action?: string;
    error?: string | null;
    ondone?: () => void;
  } = $props();

  const locale = getLocale();
  const typeItems = $derived(types.map((lt) => ({ value: lt.id, label: typeLabel(lt, locale) })));

  let userId = $state("");
  let typeId = $state(request?.leave_type_id ?? types[0]?.id ?? "");
  let startDate = $state(request?.start_date ?? "");
  let endDate = $state(request?.end_date ?? "");
  let hours = $state(request ? String(Number(request.hours)) : "");
  let hoursTouched = $state(Boolean(request));

  const selectedType = $derived(types.find((lt) => lt.id === typeId));
  const remaining = $derived(selectedType?.tracks_balance ? balances[selectedType.id] : undefined);
  const days = $derived(hours ? hoursToDays(Number(hours), hoursPerWeek) : 0);

  function syncDates(which: "start" | "end", iso: string) {
    if (which === "start") {
      startDate = iso;
      if (!endDate || endDate < iso) endDate = iso;
    } else {
      endDate = iso;
    }
    if (!hoursTouched) {
      const suggestion = suggestedHours(startDate, endDate, hoursPerWeek);
      if (suggestion > 0) hours = String(suggestion);
    }
  }

  const inputClass =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<form
  method="POST"
  {action}
  class="space-y-4"
  use:enhance={() =>
    ({ result, update }) => {
      if (result.type === "success") ondone?.();
      void update({ reset: false });
    }}
>
  {#if request}
    <input type="hidden" name="id" value={request.id} />
  {/if}

  {#if userOptions}
    <div>
      <label class="mb-1 block text-xs font-medium text-neutral-500" for="leave-user">
        {t("leave.form.employee")}
      </label>
      <Combobox
        id="leave-user"
        items={userOptions}
        name="user_id"
        bind:value={userId}
        allowEmpty={false}
        placeholder={t("leave.form.employee")}
      />
    </div>
  {/if}

  <div>
    <label class="mb-1 block text-xs font-medium text-neutral-500" for="leave-type">
      {t("leave.form.type")}
    </label>
    <Combobox
      id="leave-type"
      items={typeItems}
      name="leave_type_id"
      bind:value={typeId}
      allowEmpty={false}
      placeholder={t("leave.form.type")}
    />
    {#if remaining !== undefined}
      <p class="mt-1 text-xs {remaining <= 0 ? 'text-red-600' : 'text-neutral-500'}">
        {t("leave.form.remaining", { hours: fmtHours(remaining) })}
      </p>
    {/if}
  </div>

  <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
    <div>
      <label class="mb-1 block text-xs font-medium text-neutral-500" for="leave-start">
        {t("leave.form.start")}
      </label>
      <DateInput
        id="leave-start"
        name="start_date"
        value={startDate}
        required
        onchange={(iso) => syncDates("start", iso)}
      />
    </div>
    <div>
      <label class="mb-1 block text-xs font-medium text-neutral-500" for="leave-end">
        {t("leave.form.end")}
      </label>
      <DateInput
        id="leave-end"
        name="end_date"
        value={endDate}
        required
        onchange={(iso) => syncDates("end", iso)}
      />
    </div>
  </div>

  <div>
    <label class="mb-1 block text-xs font-medium text-neutral-500" for="leave-hours">
      {t("leave.form.hours")}
    </label>
    <input
      id="leave-hours"
      name="hours"
      type="number"
      min="0.5"
      step="0.5"
      required
      bind:value={hours}
      oninput={() => (hoursTouched = true)}
      class={inputClass}
    />
    {#if days > 0}
      <p class="mt-1 text-xs text-neutral-500">
        {t("leave.form.days_equiv", { days: fmtHours(days) })}
      </p>
    {/if}
  </div>

  <div>
    <label class="mb-1 block text-xs font-medium text-neutral-500" for="leave-note">
      {t("leave.form.note")}
    </label>
    <textarea id="leave-note" name="note" rows="2" class={inputClass}
      >{request?.note ?? ""}</textarea
    >
  </div>

  {#if error}
    <p class="text-sm text-red-600">{t(error)}</p>
  {/if}

  <div class="flex justify-end">
    <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90">
      {request ? t("common.save") : t("leave.form.submit")}
    </button>
  </div>
</form>
