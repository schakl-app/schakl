<script lang="ts">
  import { Pencil, Power, Sparkles, Trash2 } from "@lucide/svelte";
  import { tick } from "svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import { LABEL_COLORS, labelDotClass } from "$lib/core/ui/colors";
  import { fmtHours, typeLabel, type LeaveTypeInfo } from "$lib/modules/leave/format";

  let { data, form } = $props();

  const types = $derived(data.types as LeaveTypeInfo[]);
  const trackedTypes = $derived(types.filter((lt) => lt.tracks_balance && lt.active));
  const hoursByUser = $derived(
    Object.fromEntries(data.profiles.map((p) => [p.user_id, Number(p.hours_per_week)])),
  );
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

  // --- member editing ------------------------------------------------------------
  let memberOpen = $state(false);
  let editMember = $state<(typeof data.members)[number] | null>(null);

  function openMember(member: (typeof data.members)[number]) {
    editMember = member;
    memberOpen = true;
  }

  const inputClass =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{t("settings.leave.title")}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings" class="text-sm text-neutral-500 hover:text-neutral-900"
    >← {t("settings.title")}</a
  >
  <h1 class="mt-2 text-xl font-semibold text-neutral-900">{t("settings.leave.title")}</h1>
  <p class="mt-1 text-sm text-neutral-500">{t("settings.leave.subtitle")}</p>
</div>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600">{t(form.error)}</p>
{/if}

<!-- Leave types -->
<section class="mb-8">
  <div class="mb-3 flex items-center justify-between">
    <h2 class="text-xs font-semibold uppercase tracking-wide text-neutral-400">
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
  <div class="overflow-hidden rounded-xl border border-neutral-200 bg-white">
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-neutral-100 text-left text-xs text-neutral-400">
            <th class="px-4 py-2 font-medium">{t("settings.leave.type_label")}</th>
            <th class="px-2 py-2 font-medium">{t("settings.leave.type_props")}</th>
            <th class="px-2 py-2 text-right font-medium">{t("settings.leave.type_weeks")}</th>
            <th class="px-2 py-2 text-right font-medium">{t("settings.leave.type_carry")}</th>
            <th class="px-2 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-neutral-50">
          {#each types as lt (lt.id)}
            <tr class={lt.active ? "" : "opacity-50"}>
              <td class="px-4 py-2">
                <span class="inline-flex items-center gap-2 font-medium text-neutral-800">
                  <span class="h-2.5 w-2.5 rounded-full {labelDotClass(lt.color)}"></span>
                  {typeLabel(lt, data.locale)}
                </span>
                <p class="mt-0.5 text-xs text-neutral-400">{lt.key}</p>
              </td>
              <td class="px-2 py-2">
                <span class="flex flex-wrap gap-1">
                  {#if lt.paid}
                    <span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600">
                      {t("settings.leave.prop_paid")}
                    </span>
                  {/if}
                  {#if lt.tracks_balance}
                    <span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600">
                      {t("settings.leave.prop_balance")}
                    </span>
                  {/if}
                  {#if lt.requires_approval}
                    <span class="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-600">
                      {t("settings.leave.prop_approval")}
                    </span>
                  {/if}
                </span>
              </td>
              <td class="px-2 py-2 text-right tabular-nums text-neutral-700">
                {lt.default_weeks != null ? fmtHours(lt.default_weeks) : "—"}
              </td>
              <td class="px-2 py-2 text-right tabular-nums text-neutral-700">
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

<!-- Contract hours + yearly entitlements -->
<section>
  <div class="mb-3 flex flex-wrap items-center justify-between gap-3">
    <div class="flex items-center gap-3">
      <h2 class="text-xs font-semibold uppercase tracking-wide text-neutral-400">
        {t("settings.leave.team_heading", { year: data.year })}
      </h2>
      <div class="flex items-center gap-1 text-sm" data-sveltekit-preload-data="hover">
        <a
          href="?year={data.year - 1}"
          class="rounded px-1.5 py-0.5 text-neutral-400 hover:text-brand">‹</a
        >
        <span class="font-medium text-neutral-700">{data.year}</span>
        <a
          href="?year={data.year + 1}"
          class="rounded px-1.5 py-0.5 text-neutral-400 hover:text-brand">›</a
        >
      </div>
    </div>
    <form method="POST" action="?/generate" use:enhance>
      <input type="hidden" name="year" value={data.year} />
      <button
        class="flex items-center gap-2 rounded-lg border border-neutral-300 px-3 py-1.5 text-sm text-neutral-700 hover:border-brand hover:text-brand"
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
  <div class="overflow-hidden rounded-xl border border-neutral-200 bg-white">
    <div class="overflow-x-auto">
      <table class="w-full text-sm">
        <thead>
          <tr class="border-b border-neutral-100 text-left text-xs text-neutral-400">
            <th class="px-4 py-2 font-medium">{t("leave.team.member")}</th>
            <th class="px-2 py-2 text-right font-medium">{t("leave.team.contract_hours")}</th>
            {#each trackedTypes as lt (lt.id)}
              <th class="px-2 py-2 text-right font-medium">{typeLabel(lt, data.locale)}</th>
            {/each}
            <th class="px-2 py-2"></th>
          </tr>
        </thead>
        <tbody class="divide-y divide-neutral-50">
          {#each data.members as member (member.user_id)}
            <tr>
              <td class="px-4 py-2 font-medium text-neutral-800">
                {member.full_name || member.email}
              </td>
              <td class="px-2 py-2 text-right tabular-nums text-neutral-700">
                {fmtHours(hoursByUser[member.user_id] ?? 40)}
              </td>
              {#each trackedTypes as lt (lt.id)}
                <td class="px-2 py-2 text-right tabular-nums text-neutral-700">
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
          <label class="mb-1 block text-xs font-medium text-neutral-500" for="type-key">
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
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label class="mb-1 block text-xs font-medium text-neutral-500" for="type-label-nl">
            {t("settings.leave.label_nl")}
          </label>
          <input
            id="type-label-nl"
            name="label_nl"
            required
            value={editType?.label_i18n?.nl ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label class="mb-1 block text-xs font-medium text-neutral-500" for="type-label-en">
            {t("settings.leave.label_en")}
          </label>
          <input
            id="type-label-en"
            name="label_en"
            required
            value={editType?.label_i18n?.en ?? ""}
            class={inputClass}
          />
        </div>
      </div>
      <div>
        <span class="mb-1 block text-xs font-medium text-neutral-500">
          {t("settings.leave.type_color")}
        </span>
        <input type="hidden" name="color" value={typeColor} />
        <div class="flex flex-wrap gap-1.5">
          {#each LABEL_COLORS as color (color)}
            <button
              type="button"
              aria-label={color}
              class="h-6 w-6 rounded-full {labelDotClass(color)} {typeColor === color
                ? 'ring-2 ring-neutral-800 ring-offset-1'
                : ''}"
              onclick={() => (typeColor = color)}
            ></button>
          {/each}
        </div>
      </div>
      <div class="space-y-2">
        <label class="flex items-center gap-2 text-sm text-neutral-700">
          <input type="checkbox" name="paid" checked={editType?.paid ?? true} />
          {t("settings.leave.prop_paid")}
        </label>
        <label class="flex items-center gap-2 text-sm text-neutral-700">
          <input
            type="checkbox"
            name="tracks_balance"
            checked={editType?.tracks_balance ?? false}
          />
          {t("settings.leave.prop_balance_long")}
        </label>
        <label class="flex items-center gap-2 text-sm text-neutral-700">
          <input
            type="checkbox"
            name="requires_approval"
            checked={editType?.requires_approval ?? true}
          />
          {t("settings.leave.prop_approval_long")}
        </label>
      </div>
      <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <label class="mb-1 block text-xs font-medium text-neutral-500" for="type-weeks">
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
          <label class="mb-1 block text-xs font-medium text-neutral-500" for="type-carry">
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
        <div>
          <label class="mb-1 block text-xs font-medium text-neutral-500" for="member-hours">
            {t("leave.team.contract_hours")}
          </label>
          <input
            id="member-hours"
            name="hours_per_week"
            type="number"
            min="1"
            max="80"
            step="0.5"
            required
            value={hoursByUser[editMember.user_id] ?? 40}
            class={inputClass}
          />
        </div>
        <fieldset>
          <legend class="mb-1 text-xs font-medium text-neutral-500">
            {t("settings.leave.entitlements_for", { year: data.year })}
          </legend>
          <div class="space-y-2">
            {#each trackedTypes as lt (lt.id)}
              <div class="flex items-center gap-3">
                <span class="flex flex-1 items-center gap-2 text-sm text-neutral-700">
                  <span class="h-2 w-2 rounded-full {labelDotClass(lt.color)}"></span>
                  {typeLabel(lt, data.locale)}
                </span>
                <input
                  name="ent_{lt.id}"
                  type="number"
                  min="0"
                  step="0.5"
                  value={entitledByUserType[`${editMember.user_id}|${lt.id}`] ?? 0}
                  class="w-28 rounded-lg border border-neutral-300 px-3 py-1.5 text-right text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
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

<!-- Hidden activate/deactivate form, submitted from the ⋯ menu -->
<form bind:this={toggleForm} method="POST" action="?/toggleType" class="hidden" use:enhance>
  <input type="hidden" name="id" value={toggleId} />
  <input type="hidden" name="active" value={toggleActive} />
</form>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("settings.leave.delete_confirm")}
  action="?/deleteType"
  fields={{ id: deleteId }}
/>
