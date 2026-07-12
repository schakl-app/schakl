<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  let { data, form } = $props();

  const PROVIDERS = ["smtp", "brevo", "sendgrid", "smtp2go"] as const;

  // "" = keep showing the stored provider; a click switches the form's field set.
  let chosen = $state("");
  const provider = $derived(chosen || data.settings?.provider || "smtp");
  // The stored secret only "carries over" while the provider is unchanged (API rule).
  const secretStored = $derived(
    Boolean(data.settings?.has_secret) && provider === data.settings?.provider,
  );

  let confirmDelete = $state(false);

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.email.title"))}</title>
</svelte:head>

<a href="/settings" class="text-sm text-text-muted hover:text-text">← {t("settings.title")}</a>
<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.email.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.email.subtitle")}</p>

{#if !data.settings}
  <p class="mb-4 rounded-lg border border-border bg-surface px-4 py-3 text-sm text-text-muted">
    {t("settings.email.not_configured")}
  </p>
{/if}

<section class="max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <form method="POST" action="?/save" use:enhance class="space-y-4">
    <input type="hidden" name="provider" value={provider} />

    <div>
      <span class="mb-2 block text-sm font-medium text-text">{t("settings.email.provider")}</span>
      <div class="grid gap-2 sm:grid-cols-4">
        {#each PROVIDERS as p (p)}
          <button
            type="button"
            class="rounded-lg border px-3 py-2 text-sm {provider === p
              ? 'border-brand bg-surface text-brand'
              : 'border-border text-text hover:border-brand'}"
            aria-pressed={provider === p}
            onclick={() => (chosen = p)}
          >
            {t(`settings.email.provider.${p}`)}
          </button>
        {/each}
      </div>
    </div>

    <div class="grid gap-4 sm:grid-cols-2">
      <div>
        <label for="email-from-name" class="mb-1 block text-sm text-text"
          >{t("settings.email.from_name")}</label
        >
        <input
          id="email-from-name"
          name="from_name"
          required
          value={data.settings?.from_name ?? ""}
          class={inputClass}
        />
      </div>
      <div>
        <label for="email-from-email" class="mb-1 block text-sm text-text"
          >{t("settings.email.from_email")}</label
        >
        <input
          id="email-from-email"
          name="from_email"
          type="email"
          required
          placeholder="noreply@bureau.nl"
          value={data.settings?.from_email ?? ""}
          class={inputClass}
        />
      </div>
      <div class="sm:col-span-2">
        <label for="email-reply-to" class="mb-1 block text-sm text-text"
          >{t("settings.email.reply_to")}</label
        >
        <input
          id="email-reply-to"
          name="reply_to"
          type="email"
          value={data.settings?.reply_to ?? ""}
          class={inputClass}
        />
      </div>
    </div>

    {#if provider === "smtp"}
      <div class="grid gap-4 sm:grid-cols-2">
        <div>
          <label for="email-host" class="mb-1 block text-sm text-text"
            >{t("settings.email.host")}</label
          >
          <input
            id="email-host"
            name="host"
            required
            placeholder="mail.bureau.nl"
            value={data.settings?.host ?? ""}
            class={inputClass}
          />
        </div>
        <div class="grid grid-cols-2 gap-4">
          <div>
            <label for="email-port" class="mb-1 block text-sm text-text"
              >{t("settings.email.port")}</label
            >
            <input
              id="email-port"
              name="port"
              type="number"
              min="1"
              max="65535"
              value={data.settings?.port ?? 587}
              class={inputClass}
            />
          </div>
          <div>
            <label for="email-security" class="mb-1 block text-sm text-text"
              >{t("settings.email.security")}</label
            >
            <select id="email-security" name="security" class={inputClass}>
              {#each ["starttls", "ssl", "none"] as option (option)}
                <option value={option} selected={(data.settings?.security ?? "starttls") === option}
                  >{t(`settings.email.security.${option}`)}</option
                >
              {/each}
            </select>
          </div>
        </div>
        <div>
          <label for="email-username" class="mb-1 block text-sm text-text"
            >{t("settings.email.username")}</label
          >
          <input
            id="email-username"
            name="username"
            value={data.settings?.username ?? ""}
            autocomplete="off"
            class={inputClass}
          />
        </div>
        <div>
          <label for="email-password" class="mb-1 block text-sm text-text"
            >{t("settings.email.password")}</label
          >
          <input
            id="email-password"
            name="password"
            type="password"
            autocomplete="new-password"
            placeholder={secretStored ? t("settings.email.secret_stored") : ""}
            class={inputClass}
          />
        </div>
      </div>
    {:else}
      <div>
        <label for="email-api-key" class="mb-1 block text-sm text-text"
          >{t("settings.email.api_key")}</label
        >
        <input
          id="email-api-key"
          name="api_key"
          type="password"
          autocomplete="off"
          required={!secretStored}
          placeholder={secretStored ? t("settings.email.secret_stored") : ""}
          class={inputClass}
        />
      </div>
    {/if}

    {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
    {#if form?.saved}<p class="text-sm text-green-700 dark:text-green-400">
        {t("settings.email.saved")}
      </p>{/if}

    <div class="flex items-center justify-between gap-2">
      <div class="flex gap-2">
        <button
          class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
          >{t("common.save")}</button
        >
      </div>
      {#if data.settings}
        <button
          type="button"
          class="rounded-lg border border-border px-4 py-2 text-sm text-red-600 dark:text-red-400"
          onclick={() => (confirmDelete = true)}>{t("settings.email.delete")}</button
        >
      {/if}
    </div>
  </form>

  {#if data.settings}
    <form method="POST" action="?/test" use:enhance class="mt-4 border-t border-border pt-4">
      <button class="rounded-lg border border-border px-4 py-2 text-sm text-text hover:border-brand"
        >{t("settings.email.test")}</button
      >
      {#if form?.test}
        {#if form.test.ok}
          <p class="mt-2 text-sm text-green-700 dark:text-green-400">
            {t("settings.email.test_ok")}
          </p>
        {:else}
          <p class="mt-2 text-sm text-red-600 dark:text-red-400">
            {t("settings.email.test_failed", { error: form.test.error ?? "?" })}
          </p>
        {/if}
      {/if}
    </form>
  {/if}
</section>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("settings.email.delete")}
  message={t("settings.email.delete_confirm")}
  action="?/delete"
/>
