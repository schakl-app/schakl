<script lang="ts">
  import { UserMinus } from "@lucide/svelte";

  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import ActionsMenu from "$lib/core/ui/ActionsMenu.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  let { data, form } = $props();

  let showInvite = $state(false);
  let revokeId = $state("");
  let confirmRevoke = $state(false);

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{t("settings.users.title")}</title>
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
          {#each data.roles as r (r)}<option value={r} selected={r === "member"}
              >{t(`roles.${r}`)}</option
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
        </div>

        <form method="POST" action="?/changeRole" use:enhance>
          <input type="hidden" name="membership_id" value={member.membership_id} />
          <select
            name="role"
            onchange={(e) => e.currentTarget.form?.requestSubmit()}
            class="rounded-lg border border-border px-2 py-1.5 text-sm"
          >
            {#each data.roles as r (r)}<option value={r} selected={member.role === r}
                >{t(`roles.${r}`)}</option
              >{/each}
          </select>
        </form>

        {#if !member.is_self}
          <ActionsMenu
            items={[
              {
                label: t("settings.users.revoke"),
                icon: UserMinus,
                danger: true,
                onclick: () => {
                  revokeId = member.membership_id;
                  confirmRevoke = true;
                },
              },
            ]}
          />
        {/if}
      </li>
    {/each}
  </ul>
{/if}

<ConfirmDialog
  bind:open={confirmRevoke}
  title={t("settings.users.revoke")}
  message={t("settings.users.revoke_confirm")}
  confirmLabel={t("settings.users.revoke")}
  action="?/revoke"
  fields={{ membership_id: revokeId }}
/>
