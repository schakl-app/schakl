<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";

  let { data, form } = $props();

  const settings = $derived(data.settings);

  const memberName = (userId: string | null | undefined) => {
    const member = data.members.find((m: { user_id: string }) => m.user_id === userId);
    return member ? member.full_name || member.email : "";
  };

  let copied = $state(false);
  async function copyCallbackUrl() {
    if (!settings) return;
    await navigator.clipboard.writeText(settings.callback_url);
    copied = true;
    setTimeout(() => (copied = false), 2000);
  }

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.google.title"))}</title>
</svelte:head>

<a href="/settings" class="text-sm text-text-muted hover:text-text">← {t("settings.title")}</a>
<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.google.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.google.subtitle")}</p>

{#if settings?.weak_encryption_key}
  <p
    class="mb-4 max-w-2xl rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200"
  >
    {t("settings.google.weak_key_warning")}
  </p>
{/if}

<section class="max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <!-- The callback URL is derived from the org's domain, never configured: the one thing the
       admin has to carry to the Google Cloud console, shown before anything is saved. -->
  <div class="mb-5 border-b border-border pb-5">
    <label for="google-callback" class="mb-1 block text-sm font-medium text-text"
      >{t("settings.google.callback_url")}</label
    >
    <div class="flex gap-2">
      <input
        id="google-callback"
        readonly
        value={settings?.callback_url ?? ""}
        class="{inputClass} min-w-0 flex-1 bg-surface font-mono text-xs"
        onfocus={(e) => e.currentTarget.select()}
      />
      <button
        type="button"
        class="shrink-0 rounded-lg border border-border px-3 py-2 text-sm text-text hover:border-brand"
        onclick={copyCallbackUrl}
      >
        {copied ? t("settings.google.copied") : t("settings.google.copy")}
      </button>
    </div>
    <p class="mt-1 text-xs text-text-muted">{t("settings.google.callback_url_hint")}</p>
  </div>

  <form method="POST" action="?/save" use:enhance class="space-y-5">
    <!-- OAuth client (docs/GOOGLE.md §2: each install registers its own "Internal" client). -->
    <div class="grid gap-4 sm:grid-cols-2">
      <div class="sm:col-span-2">
        <label for="google-client-id" class="mb-1 block text-sm text-text"
          >{t("settings.google.client_id")}</label
        >
        <input
          id="google-client-id"
          name="client_id"
          autocomplete="off"
          value={settings?.client_id ?? ""}
          class={inputClass}
        />
        {#if settings?.env_client_configured}
          <p class="mt-1 text-xs text-text-muted">{t("settings.google.env_fallback_hint")}</p>
        {/if}
      </div>
      <div class="sm:col-span-2">
        <label for="google-client-secret" class="mb-1 block text-sm text-text"
          >{t("settings.google.client_secret")}</label
        >
        <input
          id="google-client-secret"
          name="client_secret"
          type="password"
          autocomplete="new-password"
          placeholder={settings?.client_secret_configured
            ? t("settings.google.secret_configured")
            : ""}
          class={inputClass}
        />
        <p class="mt-1 text-xs text-text-muted">{t("settings.google.client_secret_hint")}</p>
      </div>
    </div>

    <!-- Surfaces: which scopes "Google koppelen" asks employees for. -->
    <fieldset class="space-y-3 border-t border-border pt-4">
      <legend class="mb-1 text-sm font-medium text-text">
        {t("settings.google.surfaces")}
      </legend>
      <label class="flex items-start gap-2 text-sm text-text">
        <input
          type="checkbox"
          name="calendar_enabled"
          checked={settings?.calendar_enabled ?? false}
          class="mt-0.5"
        />
        <span>
          {t("settings.google.calendar_enabled")}
          <span class="block text-xs text-text-muted"
            >{t("settings.google.calendar_enabled_hint")}</span
          >
        </span>
      </label>
      <label class="flex items-start gap-2 text-sm text-text">
        <input
          type="checkbox"
          name="drive_enabled"
          checked={settings?.drive_enabled ?? false}
          class="mt-0.5"
        />
        <span>
          {t("settings.google.drive_enabled")}
          <span class="block text-xs text-text-muted"
            >{t("settings.google.drive_enabled_hint")}</span
          >
        </span>
      </label>
      <label class="flex items-start gap-2 text-sm text-text">
        <input
          type="checkbox"
          name="gmail_enabled"
          checked={settings?.gmail_enabled ?? false}
          class="mt-0.5"
        />
        <span>
          {t("settings.google.gmail_enabled")}
          <span class="block text-xs text-text-muted"
            >{t("settings.google.gmail_enabled_hint")}</span
          >
        </span>
      </label>
    </fieldset>

    <!-- Drive layout: where client folders live. Ids for now; the folder pickers arrive with
         the Drive browser so an admin can point instead of paste. -->
    <fieldset class="space-y-4 border-t border-border pt-4">
      <legend class="mb-1 text-sm font-medium text-text">{t("settings.google.drive")}</legend>
      <div class="grid gap-4 sm:grid-cols-2">
        <div>
          <label for="google-shared-drive" class="mb-1 block text-sm text-text"
            >{t("settings.google.shared_drive_id")}</label
          >
          <input
            id="google-shared-drive"
            name="drive_shared_drive_id"
            value={settings?.drive_shared_drive_id ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="google-parent-folder" class="mb-1 block text-sm text-text"
            >{t("settings.google.parent_folder_id")}</label
          >
          <input
            id="google-parent-folder"
            name="drive_parent_folder_id"
            value={settings?.drive_parent_folder_id ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="google-template-folder" class="mb-1 block text-sm text-text"
            >{t("settings.google.template_folder_id")}</label
          >
          <input
            id="google-template-folder"
            name="drive_template_folder_id"
            value={settings?.drive_template_folder_id ?? ""}
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">
            {t("settings.google.template_folder_hint")}
          </p>
        </div>
        <div>
          <label for="google-automation-connection" class="mb-1 block text-sm text-text"
            >{t("settings.google.automation_connection")}</label
          >
          <select
            id="google-automation-connection"
            name="automation_connection_user_id"
            class={inputClass}
          >
            <option value="">{t("settings.google.automation_connection_none")}</option>
            {#each data.connections as connection (connection.user_id)}
              <option
                value={connection.user_id}
                selected={settings?.automation_connection_user_id === connection.user_id}
              >
                {memberName(connection.user_id) || connection.email}
              </option>
            {/each}
          </select>
          <p class="mt-1 text-xs text-text-muted">
            {t("settings.google.automation_connection_hint")}
          </p>
        </div>
      </div>
      <label class="flex items-start gap-2 text-sm text-text">
        <input
          type="checkbox"
          name="drive_auto_provision"
          checked={settings?.drive_auto_provision ?? false}
          class="mt-0.5"
        />
        <span>
          {t("settings.google.auto_provision")}
          <span class="block text-xs text-text-muted"
            >{t("settings.google.auto_provision_hint")}</span
          >
        </span>
      </label>
    </fieldset>

    <!-- Gmail policy: how matched email lands on the timeline (owner decision flow, #22). -->
    <fieldset class="space-y-4 border-t border-border pt-4">
      <legend class="mb-1 text-sm font-medium text-text">{t("settings.google.gmail")}</legend>
      <div class="grid gap-4 sm:grid-cols-2">
        <div>
          <label for="google-approval-mode" class="mb-1 block text-sm text-text"
            >{t("settings.google.approval_mode")}</label
          >
          <select id="google-approval-mode" name="gmail_approval_mode" class={inputClass}>
            {#each ["approval_required", "auto_approve"] as mode (mode)}
              <option value={mode} selected={(settings?.gmail_approval_mode ?? "approval_required") === mode}>
                {t(`settings.google.approval_mode_${mode}`)}
              </option>
            {/each}
          </select>
        </div>
        <div>
          <label for="google-thread-followup" class="mb-1 block text-sm text-text"
            >{t("settings.google.thread_followup")}</label
          >
          <select id="google-thread-followup" name="gmail_thread_followup" class={inputClass}>
            {#each ["inherit_pending", "inherit_approve"] as mode (mode)}
              <option value={mode} selected={(settings?.gmail_thread_followup ?? "inherit_pending") === mode}>
                {t(`settings.google.thread_followup_${mode}`)}
              </option>
            {/each}
          </select>
        </div>
      </div>
    </fieldset>

    {#if form?.error}
      <p class="text-sm text-red-600">{t(form.error)}</p>
    {:else if form?.saved}
      <p class="text-sm text-green-700 dark:text-green-400">{t("settings.google.saved")}</p>
    {/if}

    <div class="flex justify-end border-t border-border pt-4">
      <button
        type="submit"
        class="rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        {t("common.save")}
      </button>
    </div>
  </form>

  <!-- Backfill: existing clients get their folder queued (new ones ride company.created). -->
  <form method="POST" action="?/provisionAll" use:enhance class="mt-5 border-t border-border pt-4">
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <p class="text-sm font-medium text-text">{t("settings.google.provision_all")}</p>
        <p class="text-xs text-text-muted">{t("settings.google.provision_all_hint")}</p>
        {#if form?.provisioned !== undefined}
          <p class="mt-1 text-xs text-green-700 dark:text-green-400">
            {t("settings.google.provision_all_queued", { count: form.provisioned })}
          </p>
        {/if}
      </div>
      <button
        type="submit"
        class="rounded-lg border border-border px-4 py-2 text-sm font-medium text-text hover:border-brand"
      >
        {t("settings.google.provision_all_run")}
      </button>
    </div>
  </form>
</section>
