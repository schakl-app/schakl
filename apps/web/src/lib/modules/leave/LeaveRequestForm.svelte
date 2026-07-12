<script lang="ts">
  /**
   * One form for requesting + editing leave (docs/UX.md: one save per surface).
   *
   * The hours are **not** typed and no longer guessed here: the server computes them from the
   * employee's schedule minus weekends, holidays and breaks (#48). The field is read-only and
   * fed by `POST /leave/preview`, debounced, so the number shown before submitting is the number
   * that will be stored. The per-day breakdown underneath is what lets the form say
   * "vrijdag 25 december — feestdag, 0 uur" instead of quietly charging nothing.
   *
   * Most requests are whole days and must stay two clicks, so the from/to times sit behind a
   * "part day" toggle rather than being two more required fields.
   *
   * Managers may pass `userOptions` to register leave for someone else (e.g. a sick call), and
   * `canOverride` to set the hours by hand when the computation cannot express what was agreed.
   */
  import { Clock } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtPeriod } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { getLocale } from "$lib/paraglide/runtime";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import TimeInput from "$lib/core/ui/TimeInput.svelte";

  import LeaveStatusPill from "./LeaveStatusPill.svelte";
  import {
    dayReasonKey,
    fmtHours,
    typeLabel,
    type LeaveDayHours,
    type LeaveTypeInfo,
  } from "./format";

  interface RequestValues {
    id: string;
    leave_type_id: string;
    start_date: string;
    start_time: string | null;
    end_date: string;
    end_time: string | null;
    hours: string | number;
    hours_override: string | number | null;
    note: string | null;
    /** Present in edit mode; drives the "this returns to pending approval" warning (#72). */
    status?: string;
  }

  let {
    types,
    request = null,
    balances = {},
    userOptions = null,
    canOverride = false,
    canBackdate = false,
    action = "?/create",
    error = null,
    ondone,
  }: {
    types: LeaveTypeInfo[];
    /** Existing request → edit mode (posts its id). */
    request?: RequestValues | null;
    /** remaining_hours by leave_type_id, to show the balance next to the type. */
    balances?: Record<string, number>;
    /** Manager register-for-someone flow: member picker options. */
    userOptions?: { value: string; label: string }[] | null;
    /** Holders of `leave.request.approve` may set the hours by hand, and are recorded doing it. */
    canOverride?: boolean;
    /**
     * Whether the caller may reach into the past (`leave.request.write:any`, #65 §6). Drives
     * the upfront past indicator (#114): a member is told before submit instead of at a 403,
     * a manager sees their save framed as a backdated registration. UX only — the API is the
     * boundary and re-checks (incl. the own-leave lock of #110).
     */
    canBackdate?: boolean;
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
  let partDay = $state(Boolean(request?.start_time || request?.end_time));
  let startTime = $state(request?.start_time ?? "");
  let endTime = $state(request?.end_time ?? "");
  let override = $state(request?.hours_override != null ? String(request.hours_override) : "");
  let overriding = $state(request?.hours_override != null);

  const selectedType = $derived(types.find((lt) => lt.id === typeId));
  const remaining = $derived(selectedType?.tracks_balance ? balances[selectedType.id] : undefined);

  // --- the preview -------------------------------------------------------------
  let hours = $state(request ? Number(request.hours) : 0);
  // The days-equivalent comes from the API too: it divides by *this employee's* average
  // scheduled day, which is not the day of the manager registering leave on their behalf.
  let days = $state(0);
  let breakdown = $state<LeaveDayHours[]>([]);
  let previewError = $state<string | null>(null);
  let previewing = $state(false);
  let requiresApproval = $state(false);
  // Whether the chosen span reaches before today (org-local) — the API computes it (#114).
  let touchesPast = $state(false);
  // Remaining balance for the chosen type/year, for the *target* employee — computed by the API,
  // because the `balances` prop belongs to the viewer, who on the manager's register-for-someone
  // flow is the wrong person (#109). Null = no type chosen, or the type tracks no balance.
  let previewRemaining = $state<number | null>(null);
  let timer: ReturnType<typeof setTimeout> | undefined;

  // Warn only when editing an *already-approved* request that the save would push back to pending
  // (#72). Creating, or editing a still-pending one, needs no warning — nothing is being undone.
  const reapprovalWarning = $derived(request?.status === "approved" && requiresApproval);

  const effectiveHours = $derived(overriding && override ? Number(override) : hours);
  // Over-requests submit — a manager decides — but never silently (#109).
  const overRequestedBy = $derived(
    previewRemaining === null ? 0 : effectiveHours - previewRemaining,
  );

  // Did this edit move the span? A note-only edit of a past request is allowed for anyone, so
  // the past lock only bites when the dates/times themselves change (or on a create).
  const spanChanged = $derived(
    !request ||
      startDate !== request.start_date ||
      endDate !== request.end_date ||
      (partDay ? startTime || null : null) !== (request.start_time || null) ||
      (partDay ? endTime || null : null) !== (request.end_time || null),
  );
  // Someone who can't backdate is told up front, and submit is disabled — a dead-end 403 on
  // submit is exactly what this replaces (#114).
  const pastBlocked = $derived(touchesPast && !canBackdate && spanChanged);

  /** One call per meaningful change, debounced — not one per keystroke. */
  function schedulePreview() {
    clearTimeout(timer);
    timer = setTimeout(runPreview, 250);
  }

  async function runPreview() {
    if (!startDate || !endDate) return;
    previewing = true;
    try {
      const res = await fetch("/leave/preview", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          user_id: userId || null,
          start_date: startDate,
          start_time: partDay ? startTime || null : null,
          end_date: endDate,
          end_time: partDay ? endTime || null : null,
          leave_type_id: typeId || null,
          request_id: request?.id ?? null,
        }),
      });
      const body = await res.json();
      if (body.error) {
        previewError = body.error;
        hours = 0;
        days = 0;
        breakdown = [];
      } else {
        previewError = null;
        hours = Number(body.preview.hours);
        days = Number(body.preview.days);
        breakdown = body.preview.breakdown as LeaveDayHours[];
        requiresApproval = Boolean(body.preview.requires_approval);
        touchesPast = Boolean(body.preview.touches_past);
        previewRemaining =
          body.preview.remaining_hours == null ? null : Number(body.preview.remaining_hours);
      }
    } catch {
      previewError = "errors.server";
    } finally {
      previewing = false;
    }
  }

  function syncDates(which: "start" | "end", iso: string) {
    if (which === "start") {
      startDate = iso;
      if (!endDate || endDate < iso) endDate = iso;
    } else {
      endDate = iso;
    }
    // No preview call here: the `$effect` below tracks these, and firing both would double it.
  }

  $effect(() => {
    // A first preview for an edit surface, and one whenever the span, the employee or the type
    // changes — the type decides whether a save needs (re-)approval (#72).
    void [startDate, endDate, partDay, startTime, endTime, userId, typeId];
    schedulePreview();
  });

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
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
    <!-- An edit reads as an edit (#114): what you are changing — its status and original
         shape — sits above the fields, so this can never pass for a new request. -->
    <div
      class="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-border bg-surface px-3 py-2 text-xs text-text-muted"
    >
      <span class="font-medium text-text">{t("leave.form.editing_label")}</span>
      {#if request.status}
        <LeaveStatusPill status={request.status} />
      {/if}
      <span>{fmtPeriod(request.start_date, request.end_date)}</span>
      <span class="tabular-nums">
        {t("leave.form.hours_amount", { hours: fmtHours(request.hours) })}
      </span>
    </div>
  {/if}

  {#if userOptions}
    <div>
      <label class="mb-1 block text-xs font-medium text-text-muted" for="leave-user">
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
    <label class="mb-1 block text-xs font-medium text-text-muted" for="leave-type">
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
      <p
        class="mt-1 text-xs {remaining <= 0 ? 'text-red-600 dark:text-red-400' : 'text-text-muted'}"
      >
        {t("leave.form.remaining", { hours: fmtHours(remaining) })}
      </p>
    {/if}
  </div>

  <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
    <div>
      <label class="mb-1 block text-xs font-medium text-text-muted" for="leave-start">
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
      <label class="mb-1 block text-xs font-medium text-text-muted" for="leave-end">
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

  <!-- Most requests are whole days: the times are an affordance, not two more fields. -->
  <div>
    <button
      type="button"
      class="flex items-center gap-1.5 text-xs {partDay
        ? 'text-brand'
        : 'text-text-muted hover:text-brand'}"
      onclick={() => (partDay = !partDay)}
    >
      <Clock size={13} />
      {partDay ? t("leave.form.whole_days") : t("leave.form.part_day")}
    </button>

    {#if partDay}
      <div class="mt-2 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label class="mb-1 block text-xs font-medium text-text-muted" for="leave-start-time">
            {t("leave.form.start_time")}
          </label>
          <TimeInput id="leave-start-time" name="start_time" bind:value={startTime} />
        </div>
        <div>
          <label class="mb-1 block text-xs font-medium text-text-muted" for="leave-end-time">
            {t("leave.form.end_time")}
          </label>
          <TimeInput id="leave-end-time" name="end_time" bind:value={endTime} />
        </div>
      </div>
      <!-- The default was always "the scheduled day's own bounds" — it just never said so. -->
      <p class="mt-1 text-xs text-text-muted">{t("leave.form.times_hint")}</p>
    {/if}
  </div>

  <!-- The hours are computed, not entered. The breakdown says where they went. -->
  <div class="rounded-lg border border-border bg-surface px-3 py-2">
    <div class="flex items-baseline justify-between">
      <span class="text-xs font-medium text-text-muted">{t("leave.form.hours")}</span>
      <span class="text-lg font-semibold tabular-nums text-text">
        {previewing ? "…" : t("leave.form.hours_amount", { hours: fmtHours(effectiveHours) })}
      </span>
    </div>
    {#if !overriding && days > 0}
      <p class="mt-0.5 text-right text-xs text-text-muted">
        {t("leave.form.days_equiv", { days: fmtHours(days) })}
      </p>
    {/if}
    {#if previewError}
      <p class="mt-1 text-xs text-red-600 dark:text-red-400">{t(previewError)}</p>
    {/if}

    {#if breakdown.length > 1 || breakdown.some((d) => d.reason)}
      <ul class="mt-2 space-y-0.5 border-t border-border pt-2">
        {#each breakdown as day (day.date)}
          {@const reason = dayReasonKey(day.reason)}
          <li class="flex justify-between text-xs {reason ? 'text-text-muted' : 'text-text'}">
            <span>
              <span class="capitalize">{fmtPeriod(day.date)}</span>
              <!-- `capitalize` uppercases every word, so the reason stays outside it: the day
                   reads "5 Nov", the reason reads "geen werkdag", not "Geen Werkdag". -->
              {#if reason}<span class="text-text-muted">· {t(reason)}</span>{/if}
            </span>
            <span class="tabular-nums">
              {t("leave.form.hours_amount", { hours: fmtHours(day.hours) })}
            </span>
          </li>
        {/each}
      </ul>
    {/if}
  </div>

  {#if canOverride}
    <div>
      <!-- Marks the field as *offered*, so unticking the box clears a stored override rather
           than silently leaving it. A member never posts this, and never posts `hours_override`. -->
      <input type="hidden" name="override_offered" value="1" />
      <label class="flex items-center gap-2 text-xs text-text-muted">
        <input type="checkbox" bind:checked={overriding} class="h-3.5 w-3.5 rounded" />
        {t("leave.form.override")}
      </label>
      {#if overriding}
        <input
          name="hours_override"
          type="number"
          min="0.25"
          step="0.25"
          required
          bind:value={override}
          class="{inputClass} mt-1"
        />
        <p class="mt-1 text-xs text-text-muted">{t("leave.form.override_hint")}</p>
      {/if}
    </div>
  {/if}

  <div>
    <label class="mb-1 block text-xs font-medium text-text-muted" for="leave-note">
      {t("leave.form.note")}
    </label>
    <textarea id="leave-note" name="note" rows="2" class={inputClass}
      >{request?.note ?? ""}</textarea
    >
  </div>

  {#if touchesPast}
    <!-- The past is flagged *before* submit (#114): a member used to discover it as a 403. -->
    {#if pastBlocked}
      <p
        class="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950 dark:text-red-300"
      >
        {t("leave.form.past_locked")}
      </p>
    {:else if canBackdate && spanChanged}
      <p
        class="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950 dark:text-amber-300"
      >
        {t("leave.form.past_backdate")}
      </p>
    {:else}
      <p class="rounded-lg bg-surface px-3 py-2 text-xs text-text-muted">
        {t("leave.form.past_span")}
      </p>
    {/if}
  {/if}

  {#if overRequestedBy > 0}
    <!-- Not a block: the request still submits and the manager decides (#109). -->
    <p
      class="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950 dark:text-amber-300"
    >
      {t("leave.form.over_request", { hours: fmtHours(overRequestedBy) })}
    </p>
  {/if}

  {#if reapprovalWarning}
    <p
      class="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-700 dark:bg-amber-950 dark:text-amber-300"
    >
      {t("leave.form.reapproval_warning")}
    </p>
  {/if}

  {#if error}
    <p class="text-sm text-red-600 dark:text-red-400">{t(error)}</p>
  {/if}

  <div class="flex justify-end">
    <button
      disabled={effectiveHours <= 0 || pastBlocked}
      class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
    >
      {request ? t("common.save") : t("leave.form.submit")}
    </button>
  </div>
</form>
