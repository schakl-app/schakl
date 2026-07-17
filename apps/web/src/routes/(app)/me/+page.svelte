<script lang="ts">
  /**
   * The personal page (hr module): your leave, your contract, your dossier documents —
   * contract copy, growth plans, bonus agreements, benefits, CAO. A dossier manager can
   * view and file for any employee (?user=); everyone else sees exactly their own.
   */
  import { Download, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { goto } from "$app/navigation";
  import { page } from "$app/state";
  import { fmtDayMonthYear, fmtNumber } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  let { data, form } = $props();

  const CATEGORIES = ["contract", "growth_plan", "bonus", "benefits", "cao", "other"] as const;

  const viewedName = $derived(
    data.self
      ? page.data.user?.full_name || page.data.user?.email || ""
      : (data.members.find((m) => m.user_id === data.viewedUserId)?.full_name ??
          data.members.find((m) => m.user_id === data.viewedUserId)?.email ??
          ""),
  );
  const memberItems = $derived(
    data.members.map((m) => ({ value: m.user_id, label: m.full_name || m.email })),
  );
  const typeLabel = (id: string) => {
    const lt = data.leaveTypes.find((x) => x.id === id);
    const labels = (lt?.label_i18n ?? {}) as Record<string, string>;
    return labels[data.locale] || labels.nl || labels.en || "";
  };
  // Newest contract first; the current one leads the section.
  const contract = $derived(
    [...data.contracts].sort((a, b) => String(b.start_date).localeCompare(String(a.start_date)))[0] ??
      null,
  );
  const docsFor = (category: string) =>
    data.dossier.documents.filter((d) => d.category === category);

  let confirmDelete = $state(false);
  let deleteId = $state("");

  const inputClass =
    "w-full rounded-lg border border-border bg-surface px-3 py-2 text-sm text-text outline-none focus:border-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("hr.me.title"))}</title>
</svelte:head>

<div class="mb-6 flex flex-wrap items-start justify-between gap-3">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("hr.me.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">
      {data.self ? t("hr.me.subtitle") : viewedName}
    </p>
  </div>
  {#if data.canAny}
    <div class="w-64 max-w-full">
      <Combobox
        items={memberItems}
        name="_dossier_user"
        id="dossier-user"
        value={data.viewedUserId}
        placeholder={t("hr.me.pick_employee")}
        onselect={(v) => goto(v && v !== page.data.user?.id ? `/me?user=${v}` : "/me", { noScroll: true })}
      />
    </div>
  {/if}
</div>

{#if form?.error}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

<div class="grid gap-4 lg:grid-cols-2">
  {#if data.balance}
    <!-- Leave: hours with the days equivalent, straight from the leave module (§14). -->
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <div class="mb-3 flex items-center justify-between">
        <h2 class="text-sm font-semibold text-text">{t("hr.me.leave")}</h2>
        {#if data.self}
          <a href="/leave" class="text-xs text-brand hover:underline">{t("hr.me.leave_open")}</a>
        {/if}
      </div>
      {#if data.balance.length === 0}
        <p class="text-sm text-text-muted">—</p>
      {:else}
        <ul class="space-y-2">
          {#each data.balance as row (row.leave_type_id + String(row.year))}
            <li class="flex items-baseline justify-between gap-2 text-sm">
              <span class="min-w-0 truncate text-text">{typeLabel(row.leave_type_id)} {row.year}</span>
              <span class="tabular-nums text-text">
                {fmtNumber(Number(row.remaining_hours), 1)} u
                <span class="text-text-muted">/ {fmtNumber(Number(row.entitled_hours), 1)} u</span>
              </span>
            </li>
          {/each}
        </ul>
      {/if}
    </section>
  {/if}

  {#if contract}
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="mb-3 text-sm font-semibold text-text">{t("hr.me.contract")}</h2>
      <dl class="grid grid-cols-2 gap-3 text-sm">
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
            {t("hr.me.contract_since")}
          </dt>
          <dd class="mt-0.5 text-text">{fmtDayMonthYear(contract.start_date)}</dd>
        </div>
        <div>
          <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
            {t("hr.me.contract_hours")}
          </dt>
          <dd class="mt-0.5 tabular-nums text-text">
            {fmtNumber(Number(contract.contract_hours_per_week), 1)} u
          </dd>
        </div>
        {#if contract.end_date}
          <div>
            <dt class="text-xs font-medium uppercase tracking-wide text-text-muted">
              {t("hr.me.contract_until")}
            </dt>
            <dd class="mt-0.5 text-text">{fmtDayMonthYear(contract.end_date)}</dd>
          </div>
        {/if}
      </dl>
    </section>
  {/if}
</div>

<!-- The dossier itself: one section per category, in a fixed order. -->
<div class="mt-4 grid gap-4">
  {#each CATEGORIES as category (category)}
    {@const docs = docsFor(category)}
    {#if docs.length > 0 || data.canManage}
      <section class="rounded-xl border border-border bg-surface-raised p-5">
        <h2 class="mb-3 text-sm font-semibold text-text">{t(`hr.category.${category}`)}</h2>
        {#if docs.length === 0}
          <p class="text-sm text-text-muted">{t("hr.me.no_documents")}</p>
        {:else}
          <ul class="divide-y divide-border/60">
            {#each docs as doc (doc.id)}
              <li class="flex items-center gap-3 py-2">
                <a
                  href={`/api/v1/hr/documents/${doc.id}/file`}
                  class="flex min-w-0 flex-1 items-center gap-2 text-sm text-text hover:text-brand"
                  download
                >
                  <Download size={15} class="shrink-0 text-text-muted" />
                  <span class="min-w-0 truncate">{doc.title}</span>
                </a>
                <span class="hidden shrink-0 text-xs text-text-muted sm:inline">
                  {doc.created_at ? fmtDayMonthYear(doc.created_at) : ""}
                  {#if doc.uploaded_by_name}· {doc.uploaded_by_name}{/if}
                </span>
                {#if data.canManage}
                  <ActionsMenu
                    compact
                    items={[
                      {
                        label: t("common.delete"),
                        icon: Trash2,
                        danger: true,
                        onclick: () => {
                          deleteId = doc.id;
                          confirmDelete = true;
                        },
                      },
                    ]}
                  />
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      </section>
    {/if}
  {/each}

  {#if data.canManage}
    <!-- Employer filing: one form, employee fixed to the viewed dossier. -->
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <h2 class="mb-3 text-sm font-semibold text-text">{t("hr.me.upload_title")}</h2>
      <form
        method="POST"
        action="?/upload"
        enctype="multipart/form-data"
        use:enhance
        class="grid gap-3 sm:grid-cols-2"
      >
        <input type="hidden" name="user_id" value={data.viewedUserId} />
        <div>
          <label for="doc-category" class="mb-1 block text-sm font-medium text-text"
            >{t("hr.me.upload_category")}</label
          >
          <select id="doc-category" name="category" class={inputClass}>
            {#each CATEGORIES as category (category)}
              <option value={category}>{t(`hr.category.${category}`)}</option>
            {/each}
          </select>
        </div>
        <div>
          <label for="doc-title" class="mb-1 block text-sm font-medium text-text"
            >{t("hr.me.upload_name")}</label
          >
          <input
            id="doc-title"
            name="title"
            placeholder={t("hr.me.upload_name_placeholder")}
            class={inputClass}
          />
        </div>
        <div class="sm:col-span-2">
          <input
            name="file"
            type="file"
            required
            class="block w-full text-sm text-text-muted file:mr-3 file:cursor-pointer file:rounded-lg file:border file:border-solid file:border-border file:bg-transparent file:px-3 file:py-1.5 file:text-sm file:text-text hover:file:border-brand"
          />
        </div>
        <div class="sm:col-span-2">
          <button
            class="rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:opacity-90"
          >
            {t("hr.me.upload_submit")}
          </button>
        </div>
      </form>
    </section>
  {/if}
</div>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("hr.me.delete_title")}
  message={t("hr.me.delete_message")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
