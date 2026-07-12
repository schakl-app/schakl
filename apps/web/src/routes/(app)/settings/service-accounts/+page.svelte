<script lang="ts">
  import { Plus, Trash2 } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import DateInput from "$lib/core/ui/DateInput.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  let { data, form } = $props();

  let createOpen = $state(false);
  let keyAccountId = $state("");
  let keyOpen = $state(false);
  let deleteAccountId = $state("");
  let deleteOpen = $state(false);
  let revokeKeyId = $state("");
  let revokeOpen = $state(false);

  const keyAccountName = $derived(
    data.accounts.find((a) => a.id === keyAccountId)?.name ?? "",
  );

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.service_accounts.title"))}</title>
</svelte:head>

<div class="mb-6">
  <a href="/settings" class="text-sm text-text-muted hover:text-text">← {t("settings.title")}</a>
  <div class="mt-2 flex flex-wrap items-center justify-between gap-3">
    <div>
      <h1 class="text-xl font-semibold text-text">{t("settings.service_accounts.title")}</h1>
      <p class="mt-1 text-sm text-text-muted">{t("settings.service_accounts.subtitle")}</p>
    </div>
    <button
      type="button"
      class="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      onclick={() => (createOpen = true)}
    >
      <Plus size={16} />
      {t("settings.service_accounts.add")}
    </button>
  </div>
</div>

{#if form?.error && !form?.keyError}
  <p class="mb-4 text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
{/if}

<!-- The full secret is shown exactly once, right after minting (#20). -->
{#if form?.createdSecret}
  <div
    class="mb-4 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950"
  >
    <p class="text-xs font-medium text-amber-800 dark:text-amber-200">
      {t("settings.account.api_key_created", { name: form.createdName ?? "" })}
    </p>
    <code
      class="mt-2 block overflow-x-auto rounded bg-surface px-2 py-1 font-mono text-xs text-text"
      >{form.createdSecret}</code
    >
    <p class="mt-1 text-xs text-amber-700 dark:text-amber-300">
      {t("settings.account.api_key_once")}
    </p>
  </div>
{/if}

{#if data.accounts.length === 0}
  <p class="rounded-xl border border-border bg-surface-raised p-6 text-sm text-text-muted">
    {t("settings.service_accounts.empty")}
  </p>
{:else}
  <div class="space-y-4">
    {#each data.accounts as account (account.id)}
      {@const keys = data.keysByAccount[account.id] ?? []}
      <section class="rounded-xl border border-border bg-surface-raised p-5">
        <div class="mb-3 flex items-center justify-between gap-3">
          <div class="min-w-0">
            <h2 class="truncate text-sm font-semibold text-text">{account.name}</h2>
            <p class="text-xs text-text-muted">
              {t("settings.service_accounts.since", {
                date: fmtNumericDate(account.created_at.slice(0, 10)),
              })}
            </p>
          </div>
          <div class="flex items-center gap-2">
            <button
              type="button"
              class="flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-brand"
              onclick={() => {
                keyAccountId = account.id;
                keyOpen = true;
              }}
            >
              <Plus size={14} />
              {t("settings.service_accounts.new_key")}
            </button>
            <ActionsMenu
              items={[
                {
                  label: t("common.delete"),
                  icon: Trash2,
                  danger: true,
                  onclick: () => {
                    deleteAccountId = account.id;
                    deleteOpen = true;
                  },
                },
              ]}
            />
          </div>
        </div>

        {#if keys.length === 0}
          <p class="text-sm text-text-muted">{t("settings.service_accounts.no_keys")}</p>
        {:else}
          <ul class="divide-y divide-border rounded-lg border border-border">
            {#each keys as key (key.id)}
              <li class="flex items-center gap-3 px-3 py-2 text-sm">
                <div class="min-w-0 flex-1">
                  <span class="font-medium text-text">{key.name}</span>
                  {#if key.revoked_at}
                    <span
                      class="ml-2 rounded-full bg-red-100 px-2 py-0.5 text-[11px] text-red-700 dark:bg-red-950 dark:text-red-300"
                      >{t("settings.account.api_key_revoked")}</span
                    >
                  {/if}
                  <span class="block truncate font-mono text-xs text-text-muted"
                    >{key.redacted}</span
                  >
                  <span class="block text-xs text-text-muted">
                    {t("settings.account.api_key_scopes", { count: key.scopes.length })} ·
                    {key.expires_at
                      ? t("settings.account.api_key_expires", {
                          date: fmtNumericDate(key.expires_at.slice(0, 10)),
                        })
                      : t("settings.account.api_key_no_expiry")}
                  </span>
                </div>
                {#if !key.revoked_at}
                  <button
                    type="button"
                    class="rounded-lg border border-border px-2.5 py-1.5 text-xs text-text-muted hover:text-red-600 dark:hover:text-red-400"
                    onclick={() => {
                      revokeKeyId = key.id;
                      revokeOpen = true;
                    }}
                  >
                    {t("settings.account.api_key_revoke")}
                  </button>
                {/if}
              </li>
            {/each}
          </ul>
        {/if}
      </section>
    {/each}
  </div>
{/if}

<!-- Create a service account -->
<Modal bind:open={createOpen} title={t("settings.service_accounts.add")}>
  <form
    method="POST"
    action="?/createAccount"
    class="space-y-4"
    use:enhance={() =>
      ({ result, update }) => {
        if (result.type === "success") createOpen = false;
        void update();
      }}
  >
    <div>
      <label for="sa-name" class="mb-1 block text-sm font-medium text-text"
        >{t("settings.service_accounts.name")}</label
      >
      <input
        id="sa-name"
        name="name"
        required
        placeholder={t("settings.service_accounts.name_placeholder")}
        class={inputClass}
      />
    </div>
    {#if form?.error && !form?.keyError}
      <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (createOpen = false)}>{t("common.cancel")}</button
      >
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
        >{t("common.save")}</button
      >
    </div>
  </form>
</Modal>

<!-- Mint a key for one account: scope + expiry, like the personal-key flow (#20). -->
<Modal
  bind:open={keyOpen}
  title={`${t("settings.service_accounts.new_key")} · ${keyAccountName}`}
>
  <form
    method="POST"
    action="?/createKey"
    class="space-y-4"
    use:enhance={() =>
      ({ result, update }) => {
        if (result.type === "success") keyOpen = false;
        void update();
      }}
  >
    <input type="hidden" name="account_id" value={keyAccountId} />
    <div>
      <label for="sa-key-name" class="mb-1 block text-sm font-medium text-text"
        >{t("settings.account.api_key_name")}</label
      >
      <input
        id="sa-key-name"
        name="name"
        required
        placeholder={t("settings.account.api_key_name_placeholder")}
        class={inputClass}
      />
    </div>
    <div>
      <label for="sa-key-expiry" class="mb-1 block text-sm font-medium text-text"
        >{t("settings.account.api_key_expiry")}</label
      >
      <DateInput name="expires_at" id="sa-key-expiry" />
      <p class="mt-1 text-xs text-text-muted">{t("settings.account.api_key_expiry_help")}</p>
    </div>
    <div>
      <span class="mb-1 block text-sm font-medium text-text"
        >{t("settings.account.api_key_scopes_label")}</span
      >
      <div class="max-h-48 space-y-1 overflow-y-auto rounded-lg border border-border p-2">
        {#each data.scopeOptions as scope (scope.value)}
          <label class="flex items-center gap-2 text-xs text-text">
            <input
              type="checkbox"
              name="scopes"
              value={scope.value}
              class="h-3.5 w-3.5 rounded border-border"
            />
            <span>{t(scope.label_key)}</span>
            {#if scope.value.includes(":")}
              <span class="text-text-muted/70">({scope.value.split(":")[1]})</span>
            {/if}
          </label>
        {/each}
      </div>
    </div>
    {#if form?.error && form?.keyError}
      <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
    {/if}
    <div class="flex justify-end gap-2">
      <button
        type="button"
        class="rounded-lg border border-border px-4 py-2 text-sm text-text"
        onclick={() => (keyOpen = false)}>{t("common.cancel")}</button
      >
      <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white"
        >{t("settings.account.api_key_create")}</button
      >
    </div>
  </form>
</Modal>

<ConfirmDialog
  bind:open={deleteOpen}
  title={t("settings.service_accounts.delete")}
  message={t("settings.service_accounts.delete_confirm")}
  action="?/deleteAccount"
  fields={{ account_id: deleteAccountId }}
/>

<ConfirmDialog
  bind:open={revokeOpen}
  title={t("settings.account.api_key_revoke")}
  message={t("settings.service_accounts.revoke_confirm")}
  action="?/revokeKey"
  fields={{ key_id: revokeKeyId }}
  confirmLabel={t("settings.account.api_key_revoke")}
/>
