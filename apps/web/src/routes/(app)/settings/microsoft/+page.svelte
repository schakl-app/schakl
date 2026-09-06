<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import { pageTitle } from "$lib/core/title";
  import Button from "$lib/core/ui/Button.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";

  let { data, form } = $props();

  const settings = $derived(data.settings);

  const memberName = (userId: string | null | undefined) => {
    const member = data.members.find((m: { user_id: string }) => m.user_id === userId);
    return member ? member.full_name || member.email : "";
  };

  // Two derived URLs the admin carries to the Entra portal and to their proxy; each copies on
  // its own so neither is retyped.
  let copied = $state<"callback" | "webhook" | null>(null);
  async function copy(which: "callback" | "webhook") {
    if (!settings) return;
    await navigator.clipboard.writeText(
      which === "callback" ? settings.callback_url : settings.webhook_url,
    );
    copied = which;
    setTimeout(() => (copied = null), 2000);
  }

  const busy = new InFlight();

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<svelte:head>
  <title>{pageTitle(t("settings.microsoft.title"))}</title>
</svelte:head>

<h1 class="mb-1 mt-2 text-xl font-semibold text-text">{t("settings.microsoft.title")}</h1>
<p class="mb-6 text-sm text-text-muted">{t("settings.microsoft.subtitle")}</p>

{#if settings?.weak_encryption_key}
  <p
    class="mb-4 max-w-2xl rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200"
  >
    {t("settings.microsoft.weak_key_warning")}
  </p>
{/if}

<section class="max-w-2xl rounded-xl border border-border bg-surface-raised p-5">
  <!-- Both URLs are derived from the org's domain, never configured: the two things the admin
       has to carry elsewhere — the redirect URI to the app registration, the notification URL
       to whatever proxy stands in front of the host — shown before anything is saved. -->
  <div class="mb-5 space-y-4 border-b border-border pb-5">
    <div>
      <label for="microsoft-callback" class="mb-1 block text-sm font-medium text-text"
        >{t("settings.microsoft.callback_url")}</label
      >
      <div class="flex gap-2">
        <input
          id="microsoft-callback"
          readonly
          value={settings?.callback_url ?? ""}
          class="{inputClass} min-w-0 flex-1 bg-surface font-mono text-xs"
          onfocus={(e) => e.currentTarget.select()}
        />
        <button
          type="button"
          class="shrink-0 rounded-lg border border-border px-3 py-2 text-sm text-text hover:border-brand"
          onclick={() => copy("callback")}
        >
          {copied === "callback" ? t("settings.microsoft.copied") : t("settings.microsoft.copy")}
        </button>
      </div>
      <p class="mt-1 text-xs text-text-muted">{t("settings.microsoft.callback_url_hint")}</p>
    </div>
    <div>
      <label for="microsoft-webhook" class="mb-1 block text-sm font-medium text-text"
        >{t("settings.microsoft.webhook_url")}</label
      >
      <div class="flex gap-2">
        <input
          id="microsoft-webhook"
          readonly
          value={settings?.webhook_url ?? ""}
          class="{inputClass} min-w-0 flex-1 bg-surface font-mono text-xs"
          onfocus={(e) => e.currentTarget.select()}
        />
        <button
          type="button"
          class="shrink-0 rounded-lg border border-border px-3 py-2 text-sm text-text hover:border-brand"
          onclick={() => copy("webhook")}
        >
          {copied === "webhook" ? t("settings.microsoft.copied") : t("settings.microsoft.copy")}
        </button>
      </div>
      <p class="mt-1 text-xs text-text-muted">{t("settings.microsoft.webhook_url_hint")}</p>
    </div>
  </div>

  <form
    method="POST"
    action="?/save"
    use:enhance={busy.wrap(
      "save",
      () =>
        ({ update }) =>
          update({ reset: false }),
    )}
    class="space-y-5"
  >
    <!-- The app registration (docs/MICROSOFT.md §2): each agency registers its own. -->
    <div class="grid gap-4 sm:grid-cols-2">
      <div class="sm:col-span-2">
        <label for="microsoft-client-id" class="mb-1 block text-sm text-text"
          >{t("settings.microsoft.client_id")}</label
        >
        <input
          id="microsoft-client-id"
          name="client_id"
          autocomplete="off"
          value={settings?.client_id ?? ""}
          class={inputClass}
        />
        {#if settings?.env_client_configured}
          <p class="mt-1 text-xs text-text-muted">{t("settings.microsoft.env_fallback_hint")}</p>
        {/if}
      </div>
      <div class="sm:col-span-2">
        <label for="microsoft-client-secret" class="mb-1 block text-sm text-text"
          >{t("settings.microsoft.client_secret")}</label
        >
        <input
          id="microsoft-client-secret"
          name="client_secret"
          type="password"
          autocomplete="new-password"
          placeholder={settings?.client_secret_configured
            ? t("settings.microsoft.secret_configured")
            : ""}
          class={inputClass}
        />
        <p class="mt-1 text-xs text-text-muted">{t("settings.microsoft.client_secret_hint")}</p>
      </div>
      <div class="sm:col-span-2">
        <label for="microsoft-tenant-id" class="mb-1 block text-sm text-text"
          >{t("settings.microsoft.tenant_id")}</label
        >
        <input
          id="microsoft-tenant-id"
          name="tenant_id"
          autocomplete="off"
          placeholder="common"
          value={settings?.tenant_id ?? ""}
          class={inputClass}
        />
        <p class="mt-1 text-xs text-text-muted">{t("settings.microsoft.tenant_id_hint")}</p>
      </div>
    </div>

    <!-- Surfaces: which scopes "Microsoft koppelen" asks employees for. -->
    <fieldset class="space-y-3 border-t border-border pt-4">
      <legend class="mb-1 text-sm font-medium text-text">
        {t("settings.microsoft.surfaces")}
      </legend>
      <label class="flex items-start gap-2 text-sm text-text">
        <FormCheckbox
          name="calendar_enabled"
          checked={settings?.calendar_enabled ?? false}
          class="mt-0.5"
        />
        <span>
          {t("settings.microsoft.calendar_enabled")}
          <span class="block text-xs text-text-muted"
            >{t("settings.microsoft.calendar_enabled_hint")}</span
          >
        </span>
      </label>
      <label class="flex items-start gap-2 text-sm text-text">
        <FormCheckbox
          name="onedrive_enabled"
          checked={settings?.onedrive_enabled ?? false}
          class="mt-0.5"
        />
        <span>
          {t("settings.microsoft.onedrive_enabled")}
          <span class="block text-xs text-text-muted"
            >{t("settings.microsoft.onedrive_enabled_hint")}</span
          >
        </span>
      </label>
      <label class="flex items-start gap-2 text-sm text-text">
        <FormCheckbox
          name="outlook_enabled"
          checked={settings?.outlook_enabled ?? false}
          class="mt-0.5"
        />
        <span>
          {t("settings.microsoft.outlook_enabled")}
          <span class="block text-xs text-text-muted"
            >{t("settings.microsoft.outlook_enabled_hint")}</span
          >
        </span>
      </label>
    </fieldset>

    <!-- OneDrive layout: where client folders live. A drive (a SharePoint library or a OneDrive)
         plus a parent folder and an optional template, all by id. -->
    <fieldset class="space-y-4 border-t border-border pt-4">
      <legend class="mb-1 text-sm font-medium text-text">{t("settings.microsoft.onedrive")}</legend>
      <div class="grid gap-4 sm:grid-cols-2">
        <div>
          <label for="microsoft-drive-id" class="mb-1 block text-sm text-text"
            >{t("settings.microsoft.drive_id")}</label
          >
          <input
            id="microsoft-drive-id"
            name="onedrive_drive_id"
            value={settings?.onedrive_drive_id ?? ""}
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("settings.microsoft.drive_id_hint")}</p>
        </div>
        <div>
          <label for="microsoft-parent-folder" class="mb-1 block text-sm text-text"
            >{t("settings.microsoft.parent_folder_id")}</label
          >
          <input
            id="microsoft-parent-folder"
            name="onedrive_parent_folder_id"
            value={settings?.onedrive_parent_folder_id ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="microsoft-template-folder" class="mb-1 block text-sm text-text"
            >{t("settings.microsoft.template_folder_id")}</label
          >
          <input
            id="microsoft-template-folder"
            name="onedrive_template_folder_id"
            value={settings?.onedrive_template_folder_id ?? ""}
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">
            {t("settings.microsoft.template_folder_hint")}
          </p>
        </div>
        <div>
          <label for="microsoft-automation-connection" class="mb-1 block text-sm text-text"
            >{t("settings.microsoft.automation_connection")}</label
          >
          <select
            id="microsoft-automation-connection"
            name="automation_connection_user_id"
            class={inputClass}
          >
            <option value="">{t("settings.microsoft.automation_connection_none")}</option>
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
            {t("settings.microsoft.automation_connection_hint")}
          </p>
        </div>
      </div>
      <label class="flex items-start gap-2 text-sm text-text">
        <FormCheckbox
          name="onedrive_auto_provision"
          checked={settings?.onedrive_auto_provision ?? false}
          class="mt-0.5"
        />
        <span>
          {t("settings.microsoft.auto_provision")}
          <span class="block text-xs text-text-muted"
            >{t("settings.microsoft.auto_provision_hint")}</span
          >
        </span>
      </label>
    </fieldset>

    <!-- Outlook policy: how matched email lands on the timeline (the shared mailbox policy). -->
    <fieldset class="space-y-4 border-t border-border pt-4">
      <legend class="mb-1 text-sm font-medium text-text">{t("settings.microsoft.outlook")}</legend>
      <div class="grid gap-4 sm:grid-cols-2">
        <div>
          <label for="microsoft-approval-mode" class="mb-1 block text-sm text-text"
            >{t("settings.microsoft.approval_mode")}</label
          >
          <select id="microsoft-approval-mode" name="outlook_approval_mode" class={inputClass}>
            {#each ["approval_required", "auto_approve"] as mode (mode)}
              <option
                value={mode}
                selected={(settings?.outlook_approval_mode ?? "approval_required") === mode}
              >
                {t(`settings.microsoft.approval_mode_${mode}`)}
              </option>
            {/each}
          </select>
        </div>
        <div>
          <label for="microsoft-thread-followup" class="mb-1 block text-sm text-text"
            >{t("settings.microsoft.thread_followup")}</label
          >
          <select id="microsoft-thread-followup" name="outlook_thread_followup" class={inputClass}>
            {#each ["inherit_pending", "inherit_approve"] as mode (mode)}
              <option
                value={mode}
                selected={(settings?.outlook_thread_followup ?? "inherit_pending") === mode}
              >
                {t(`settings.microsoft.thread_followup_${mode}`)}
              </option>
            {/each}
          </select>
        </div>
      </div>
      <label class="flex items-start gap-2 text-sm text-text">
        <FormCheckbox
          name="outlook_log_internal"
          checked={settings?.outlook_log_internal ?? false}
          class="mt-0.5"
        />
        <span>
          {t("settings.microsoft.log_internal")}
          <span class="block text-xs text-text-muted"
            >{t("settings.microsoft.log_internal_hint")}</span
          >
        </span>
      </label>
    </fieldset>

    {#if form?.error}
      <p class="text-sm text-red-600">{t(form.error)}</p>
    {:else if form?.saved}
      <p class="text-sm text-green-700 dark:text-green-400">{t("settings.microsoft.saved")}</p>
    {/if}

    <div class="flex justify-end border-t border-border pt-4">
      <Button type="submit" loading={busy.is("save")} disabled={busy.active}>
        {t("common.save")}
      </Button>
    </div>
  </form>

  <!-- Backfill: existing clients get their folder queued (new ones ride company.created). -->
  <form
    method="POST"
    action="?/provisionAll"
    use:enhance={busy.wrap("provisionAll")}
    class="mt-5 border-t border-border pt-4"
  >
    <div class="flex flex-wrap items-center justify-between gap-2">
      <div>
        <p class="text-sm font-medium text-text">{t("settings.microsoft.provision_all")}</p>
        <p class="text-xs text-text-muted">{t("settings.microsoft.provision_all_hint")}</p>
        {#if form?.provisioned !== undefined}
          <p class="mt-1 text-xs text-green-700 dark:text-green-400">
            {t("settings.microsoft.provision_all_queued", { count: form.provisioned })}
          </p>
        {/if}
      </div>
      <Button
        type="submit"
        variant="secondary"
        loading={busy.is("provisionAll")}
        disabled={busy.active}
      >
        {t("settings.microsoft.provision_all_run")}
      </Button>
    </div>
  </form>
</section>
