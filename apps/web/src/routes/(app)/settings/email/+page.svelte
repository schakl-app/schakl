<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  let { data, form } = $props();

  // "instance" = the operator-provided transport (epic #199), offered only while the
  // instance actually has one configured.
  const PROVIDERS = $derived(
    data.instanceEmailAvailable
      ? (["instance", "smtp", "brevo", "sendgrid", "smtp2go"] as const)
      : (["smtp", "brevo", "sendgrid", "smtp2go"] as const),
  );

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

  // Tenant auth-mail templates (#161 tier 2): one editor per (kind, locale). The locales
  // render behind a language switcher instead of side by side (owner feedback) — you edit
  // one language at a time, and every translation is optional (blank = built-in default).
  const templateKinds = ["invite", "reset"] as const;
  const templatesByKind = (kind: string) =>
    (data.templates?.templates ?? []).filter((tpl) => tpl.kind === kind);
  let activeTplLocale = $state<Record<string, string>>({});
  const tplLocale = (kind: string) =>
    activeTplLocale[kind] ?? templatesByKind(kind)[0]?.locale ?? "nl";
  const variablesHint = $derived((data.templates?.variables ?? []).map((v) => `{${v}}`).join("  "));
</script>

<svelte:head>
  <title>{pageTitle(t("settings.email.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.email.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.email.subtitle")}</p>

{#if !data.settings}
  <p class="mb-4 rounded-lg border border-border bg-surface px-4 py-3 text-sm text-text-muted">
    {t("settings.email.not_configured")}
  </p>
{/if}

<section class="max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <form
    method="POST"
    action="?/save"
    use:enhance={() =>
      ({ update }) =>
        update({ reset: false })}
    class="space-y-4"
  >
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
      {#if provider !== "instance"}
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
      {:else}
        <div class="flex items-end">
          <p class="text-sm text-text-muted">{t("settings.email.instance_hint")}</p>
        </div>
      {/if}
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
      <!-- Org-wide signature (owner request): appended automatically to every outgoing
           mail. HTML allowed (sanitised server-side); blank = no signature. -->
      <div class="sm:col-span-2">
        <label for="email-signature" class="mb-1 block text-sm text-text"
          >{t("settings.email.signature")}</label
        >
        <textarea
          id="email-signature"
          name="signature_html"
          rows="5"
          placeholder={t("settings.email.signature_placeholder")}
          class="{inputClass} font-mono text-xs">{data.settings?.signature_html ?? ""}</textarea
        >
        <p class="mt-1 text-xs text-text-muted">{t("settings.email.signature_hint")}</p>
      </div>
    </div>

    {#if provider === "instance"}
      <!-- No credentials to enter: the transport is the instance's own (epic #199). -->
    {:else if provider === "smtp"}
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

{#if data.templates}
  <!-- Tenant-customisable auth mails (#161 tier 2). Blank = the built-in default. -->
  <section class="mt-6 max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
    <h2 class="text-lg font-semibold text-text">{t("settings.email.templates.title")}</h2>
    <p class="mt-1 text-sm text-text-muted">{t("settings.email.templates.subtitle")}</p>

    {#if !data.settings}
      <p
        class="mt-3 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900/50 dark:bg-amber-900/20 dark:text-amber-200"
      >
        {t("settings.email.templates.no_transport")}
      </p>
    {/if}

    {#each templateKinds as kind (kind)}
      <div class="mt-5">
        <div class="flex items-center justify-between gap-2">
          <h3 class="text-sm font-semibold text-text">
            {t(`settings.email.templates.kind.${kind}`)}
          </h3>
          <div class="flex gap-0.5" role="tablist">
            {#each templatesByKind(kind) as tpl (tpl.locale)}
              <button
                type="button"
                role="tab"
                aria-selected={tplLocale(kind) === tpl.locale}
                class="rounded px-1.5 py-0.5 text-[11px] font-medium uppercase {tplLocale(kind) ===
                tpl.locale
                  ? 'bg-brand text-white'
                  : 'text-text-muted hover:bg-surface'}"
                onclick={() => (activeTplLocale[kind] = tpl.locale)}
              >
                {tpl.locale}
              </button>
            {/each}
          </div>
        </div>
        <div class="mt-2">
          {#each templatesByKind(kind) as tpl (tpl.locale)}
            <form
              method="POST"
              action="?/saveTemplate"
              use:enhance
              class="space-y-3 rounded-lg border border-border p-4 {tplLocale(kind) === tpl.locale
                ? ''
                : 'hidden'}"
            >
              <input type="hidden" name="kind" value={tpl.kind} />
              <input type="hidden" name="locale" value={tpl.locale} />
              <div class="flex items-center justify-end gap-2">
                {#if tpl.subject || tpl.body_html}
                  <span
                    class="rounded-full bg-brand/10 px-2 py-0.5 text-[11px] font-medium text-brand"
                    >{t("settings.email.templates.customised")}</span
                  >
                {:else}
                  <span class="text-[11px] text-text-muted"
                    >{t("settings.email.templates.using_default")}</span
                  >
                {/if}
              </div>

              <div>
                <label
                  for={`tpl-subject-${tpl.kind}-${tpl.locale}`}
                  class="mb-1 block text-xs font-medium text-text-muted"
                  >{t("settings.email.templates.subject")}</label
                >
                <input
                  id={`tpl-subject-${tpl.kind}-${tpl.locale}`}
                  name="subject"
                  value={tpl.subject ?? ""}
                  placeholder={tpl.default_subject}
                  class={inputClass}
                />
              </div>

              <div>
                <label
                  for={`tpl-body-${tpl.kind}-${tpl.locale}`}
                  class="mb-1 block text-xs font-medium text-text-muted"
                  >{t("settings.email.templates.body")}</label
                >
                <textarea
                  id={`tpl-body-${tpl.kind}-${tpl.locale}`}
                  name="body_html"
                  rows="7"
                  placeholder={t("settings.email.templates.body_placeholder")}
                  class="{inputClass} font-mono text-xs">{tpl.body_html ?? ""}</textarea
                >
              </div>

              <p class="text-[11px] text-text-muted">
                {t("settings.email.templates.variables")}
                <code class="rounded bg-surface px-1 py-0.5 text-text">{variablesHint}</code>
              </p>

              <details class="text-[11px] text-text-muted">
                <summary class="cursor-pointer hover:text-text"
                  >{t("settings.email.templates.show_default")}</summary
                >
                <pre
                  class="mt-2 max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-surface p-2 text-text">{tpl.default_body_html}</pre>
              </details>

              {#if form?.templateSaved?.kind === tpl.kind && form?.templateSaved?.locale === tpl.locale}
                <p class="text-xs text-green-700 dark:text-green-400">
                  {t("settings.email.templates.saved")}
                </p>
              {/if}
              {#if form?.templateKind === tpl.kind && form?.templateLocale === tpl.locale && form?.templateTest}
                {#if form.templateTest.ok}
                  <p class="text-xs text-green-700 dark:text-green-400">
                    {t("settings.email.test_ok")}
                  </p>
                {:else}
                  <p class="text-xs text-red-600 dark:text-red-400">
                    {t("settings.email.test_failed", { error: form.templateTest.error ?? "?" })}
                  </p>
                {/if}
              {/if}

              <div class="flex gap-2">
                <button
                  class="rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
                  >{t("common.save")}</button
                >
                {#if data.settings}
                  <button
                    formaction="?/testTemplate"
                    class="rounded-lg border border-border px-3 py-1.5 text-xs font-medium text-text hover:border-brand"
                    >{t("settings.email.templates.test")}</button
                  >
                {/if}
              </div>
            </form>
          {/each}
        </div>
      </div>
    {/each}
  </section>
{/if}

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("settings.email.delete")}
  message={t("settings.email.delete_confirm")}
  action="?/delete"
/>
