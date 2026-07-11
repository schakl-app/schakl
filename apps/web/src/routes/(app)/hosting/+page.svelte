<script lang="ts">
  import { Pencil, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";
  import HostingForm from "$lib/modules/hosting/HostingForm.svelte";
  import type { components } from "$lib/core/api/schema";

  type Hosting = components["schemas"]["HostingRead"];

  let { data, form } = $props();

  let showModal = $state(false);
  let editing = $state<Hosting | null>(null);
  let deleteId = $state("");
  let confirmDelete = $state(false);

  function openCreate() {
    editing = null;
    showModal = true;
  }
  function openEdit(h: Hosting) {
    editing = h;
    showModal = true;
  }
  function requestDelete(id: string) {
    deleteId = id;
    confirmDelete = true;
  }
</script>

<svelte:head>
  <title>{t("hosting.title")}</title>
</svelte:head>

<div class="mb-6 flex items-center justify-between">
  <div>
    <h1 class="text-xl font-semibold text-text">{t("hosting.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">{t("hosting.count", { count: data.total })}</p>
  </div>
  <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white" onclick={openCreate}
    >{t("hosting.new")}</button
  >
</div>

<section class="rounded-xl border border-border bg-surface-raised">
  {#if data.hosting.length === 0}
    <p class="p-6 text-sm text-text-muted">{t("hosting.empty")}</p>
  {:else}
    <ul class="divide-y divide-border">
      {#each data.hosting as item (item.id)}
        <li class="flex items-center gap-3 px-4 py-3">
          <span class="flex-1 text-sm font-medium text-text">{item.name}</span>
          {#if item.provider_name}
            <span class="text-xs text-text-muted">{item.provider_name}</span>
          {/if}
          {#if item.ip_address}
            <span class="font-mono text-xs text-text-muted">{item.ip_address}</span>
          {/if}
          {#if item.company_name}
            <span class="text-xs text-text-muted">{item.company_name}</span>
          {/if}
          <ActionsMenu
            items={[
              { label: t("common.edit"), icon: Pencil, onclick: () => openEdit(item) },
              {
                label: t("common.delete"),
                icon: Trash2,
                danger: true,
                onclick: () => requestDelete(item.id),
              },
            ]}
          />
        </li>
      {/each}
    </ul>
  {/if}
</section>

<Modal bind:open={showModal} title={editing ? t("hosting.edit") : t("hosting.new")}>
  {#key editing?.id ?? "new"}
    <form
      method="POST"
      action="?/save"
      use:enhance={() =>
        ({ result, update }) => {
          if (result.type === "success") showModal = false;
          void update({ reset: false });
        }}
    >
      {#if editing}<input type="hidden" name="id" value={editing.id} />{/if}
      <HostingForm
        hosting={editing}
        companies={data.companies}
        providers={data.providers}
        employees={data.employees}
        contacts={data.contacts}
        agencyLabel={data.agencyLabel}
        definitions={data.definitions}
        locale={data.locale}
        idPrefix={editing ? `edit-${editing.id}` : "new-hosting"}
      />
      {#if form?.error}<p class="mt-3 text-sm text-red-600 dark:text-red-400">
          {t(form.error)}
        </p>{/if}
      <div class="mt-4 flex justify-end gap-2">
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-text"
          onclick={() => (showModal = false)}>{t("common.cancel")}</button
        >
        <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
          >{t("common.save")}</button
        >
      </div>
    </form>
  {/key}
</Modal>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("hosting.delete")}
  message={t("hosting.delete_confirm")}
  action="?/delete"
  fields={{ id: deleteId }}
/>
