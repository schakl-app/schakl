<script lang="ts">
  import { enhance } from "$app/forms";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";

  let { data, form } = $props();

  const sso = $derived(data.sso);
  // The enforce toggle is locked until the *current* connection passed a test (the API refuses
  // it anyway — this mirrors that rule so the form can't offer a save that will bounce). It
  // stays operable while already enforced, so switching enforcement off is always possible.
  const enforceLocked = $derived(!sso?.tested && !sso?.enforced);

  const roleName = (option: { key: string; name_i18n: Record<string, string> }) =>
    option.name_i18n[data.locale] ?? option.name_i18n.nl ?? option.key;

  let copied = $state(false);
  async function copyCallbackUrl() {
    if (!sso) return;
    await navigator.clipboard.writeText(sso.callback_url);
    copied = true;
    setTimeout(() => (copied = false), 2000);
  }

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.sso.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.sso.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.sso.subtitle")}</p>

{#if sso?.weak_encryption_key}
  <p
    class="mb-4 max-w-2xl rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200"
  >
    {t("settings.sso.weak_key_warning")}
  </p>
{/if}

<section class="max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <!-- The callback URL is derived from the org's domain, never configured: the one thing the
       admin has to carry to the IdP, shown before anything is saved. -->
  <div class="mb-5 border-b border-border pb-5">
    <label for="sso-callback" class="mb-1 block text-sm font-medium text-text"
      >{t("settings.sso.callback_url")}</label
    >
    <div class="flex gap-2">
      <input
        id="sso-callback"
        readonly
        value={sso?.callback_url ?? ""}
        class="{inputClass} min-w-0 flex-1 bg-surface font-mono text-xs"
        onfocus={(e) => e.currentTarget.select()}
      />
      <button
        type="button"
        class="shrink-0 rounded-lg border border-border px-3 py-2 text-sm text-text hover:border-brand"
        onclick={copyCallbackUrl}
      >
        {copied ? t("settings.sso.copied") : t("settings.sso.copy")}
      </button>
    </div>
    <p class="mt-1 text-xs text-text-muted">{t("settings.sso.callback_url_hint")}</p>
  </div>

  <form method="POST" action="?/save" use:enhance class="space-y-4">
    <label class="flex items-start gap-2 text-sm text-text">
      <FormCheckbox name="enabled" checked={sso?.enabled ?? false} class="mt-0.5" />
      <span>
        {t("settings.sso.enabled")}
        <span class="block text-xs text-text-muted">{t("settings.sso.enabled_hint")}</span>
      </span>
    </label>

    <div class="grid gap-4 sm:grid-cols-2">
      <div>
        <label for="sso-name" class="mb-1 block text-sm text-text">{t("settings.sso.name")}</label>
        <input id="sso-name" name="name" required value={sso?.name ?? "SSO"} class={inputClass} />
        <p class="mt-1 text-xs text-text-muted">{t("settings.sso.name_hint")}</p>
      </div>
      <div>
        <label for="sso-default-role" class="mb-1 block text-sm text-text"
          >{t("settings.sso.default_role")}</label
        >
        <select id="sso-default-role" name="default_role" class={inputClass}>
          {#each sso?.role_options ?? [] as option (option.key)}
            <option value={option.key} selected={(sso?.default_role ?? "member") === option.key}
              >{roleName(option)}</option
            >
          {/each}
        </select>
      </div>
      <div class="sm:col-span-2">
        <label for="sso-discovery" class="mb-1 block text-sm text-text"
          >{t("settings.sso.discovery_url")}</label
        >
        <input
          id="sso-discovery"
          name="discovery_url"
          type="url"
          placeholder="https://idp.example.com/.well-known/openid-configuration"
          value={sso?.discovery_url ?? ""}
          class={inputClass}
        />
        <p class="mt-1 text-xs text-text-muted">{t("settings.sso.discovery_url_hint")}</p>
      </div>
      <div>
        <label for="sso-client-id" class="mb-1 block text-sm text-text"
          >{t("settings.sso.client_id")}</label
        >
        <input
          id="sso-client-id"
          name="client_id"
          autocomplete="off"
          value={sso?.client_id ?? ""}
          class={inputClass}
        />
      </div>
      <div>
        <label for="sso-client-secret" class="mb-1 block text-sm text-text"
          >{t("settings.sso.client_secret")}</label
        >
        <input
          id="sso-client-secret"
          name="client_secret"
          type="password"
          autocomplete="new-password"
          placeholder={sso?.secret_configured ? t("settings.sso.secret_stored") : ""}
          class={inputClass}
        />
      </div>
    </div>

    <label class="flex items-start gap-2 text-sm text-text">
      <FormCheckbox name="auto_provision" checked={sso?.auto_provision ?? true} class="mt-0.5" />
      <span>
        {t("settings.sso.auto_provision")}
        <span class="block text-xs text-text-muted">{t("settings.sso.auto_provision_hint")}</span>
      </span>
    </label>

    <label class="flex items-start gap-2 text-sm text-text" class:opacity-60={enforceLocked}>
      <FormCheckbox
        name="enforced"
        checked={sso?.enforced ?? false}
        disabled={enforceLocked}
        class="mt-0.5"
      />
      <span>
        {t("settings.sso.enforced")}
        <span class="block text-xs text-text-muted">{t("settings.sso.enforced_hint")}</span>
      </span>
    </label>

    {#if form?.error}<p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>{/if}
    {#if form?.fields}
      {#each Object.values(form.fields) as key (key)}
        <p class="text-sm text-red-600 dark:text-red-400">{t(String(key))}</p>
      {/each}
    {/if}
    {#if form?.saved}<p class="text-sm text-green-700 dark:text-green-400">
        {t("settings.sso.saved")}
      </p>{/if}

    <button class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >{t("common.save")}</button
    >
  </form>

  {#if sso?.discovery_url}
    <form method="POST" action="?/test" use:enhance class="mt-4 border-t border-border pt-4">
      <button class="rounded-lg border border-border px-4 py-2 text-sm text-text hover:border-brand"
        >{t("settings.sso.test")}</button
      >
      {#if form?.test}
        {#if form.test.ok}
          <p class="mt-2 text-sm text-green-700 dark:text-green-400">
            {t("settings.sso.test_ok", { issuer: form.test.issuer ?? "?" })}
          </p>
        {:else}
          <p class="mt-2 text-sm text-red-600 dark:text-red-400">
            {t("settings.sso.test_failed", {
              error: form.test.error?.startsWith("errors.")
                ? t(form.test.error)
                : (form.test.error ?? "?"),
            })}
          </p>
        {/if}
      {/if}
    </form>
  {/if}
</section>
