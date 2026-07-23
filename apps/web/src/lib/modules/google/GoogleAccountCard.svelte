<script lang="ts">
  /**
   * The personal "Google koppelen" card on Instellingen → Account (docs/GOOGLE.md §1).
   *
   * Connecting is a *separate grant from login* — this card starts the incremental OAuth flow
   * (`/api/v1/google/oauth/connect`), never the OIDC login. Gmail is per-user and opt-in: the
   * checkbox adds the scope to the consent, and the mailbox toggles live here, on the person,
   * not in org settings.
   *
   * **Host contract:** the account page exposes `?/googleDisconnect` and `?/googleGmailPrefs`.
   */
  import { enhance } from "$app/forms";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  interface Connection {
    email: string;
    status: string;
    scopes?: string[];
    gmail_sync_enabled: boolean;
    gmail_excluded_label?: string | null;
    last_error?: string | null;
  }

  interface MyConnection {
    connected: boolean;
    connection?: Connection | null;
    configured: boolean;
    calendar_enabled: boolean;
    drive_enabled: boolean;
    gmail_enabled: boolean;
  }

  let { data, status }: { data: MyConnection; status: string | null } = $props();

  let includeGmail = $state(false);
  let confirmDisconnect = $state(false);

  const busy = new InFlight();

  const connection = $derived(data.connection);
  const surfaces = $derived(
    [
      data.calendar_enabled ? t("google.surface.calendar") : null,
      data.drive_enabled ? t("google.surface.drive") : null,
      data.gmail_enabled ? t("google.surface.gmail") : null,
    ].filter(Boolean),
  );

  // Same-host navigation: Traefik routes `/api/` to the API, so the session cookie and the
  // tenant hostname ride along — exactly what the connect flow's require_context needs.
  const connectHref = $derived(
    `/api/v1/google/oauth/connect${includeGmail ? "?include_gmail=true" : ""}`,
  );

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<section class="rounded-xl border border-border bg-surface-raised p-5">
  <h2 class="text-sm font-semibold text-text">{t("google.account.title")}</h2>
  <p class="mt-1 text-sm text-text-muted">{t("google.account.hint")}</p>

  {#if status === "connected"}
    <p
      class="mt-3 rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-200"
    >
      {t("google.account.just_connected")}
    </p>
  {:else if status === "error"}
    <p
      class="mt-3 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-700 dark:bg-red-950 dark:text-red-200"
    >
      {t("google.account.connect_failed")}
    </p>
  {/if}

  {#if !data.configured}
    <p class="mt-3 text-sm text-text-muted">{t("google.account.not_configured")}</p>
  {:else if !data.connected}
    <div class="mt-4 space-y-3">
      {#if surfaces.length > 0}
        <p class="text-sm text-text">
          {t("google.account.grants")}
          <span class="text-text-muted">{surfaces.join(" · ")}</span>
        </p>
      {/if}
      {#if data.gmail_enabled}
        <label class="flex items-start gap-2 text-sm text-text">
          <input type="checkbox" bind:checked={includeGmail} class="mt-0.5" />
          <span>
            {t("google.account.include_gmail")}
            <span class="block text-xs text-text-muted"
              >{t("google.account.include_gmail_hint")}</span
            >
          </span>
        </label>
      {/if}
      <a
        href={connectHref}
        data-sveltekit-preload-data="off"
        class="inline-block rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        {t("google.account.connect")}
      </a>
    </div>
  {:else if connection}
    <div class="mt-4 space-y-4">
      <div class="flex flex-wrap items-center gap-2">
        <span class="text-sm font-medium text-text">{connection.email}</span>
        {#if connection.status === "active"}
          <span
            class="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-400"
            >{t("google.account.status_active")}</span
          >
        {:else}
          <span
            class="rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-medium text-red-800 dark:bg-red-500/15 dark:text-red-400"
            >{t("google.account.status_error")}</span
          >
        {/if}
      </div>

      {#if connection.status !== "active"}
        <p class="text-sm text-text-muted">{t("google.account.reconnect_hint")}</p>
        <a
          href={connectHref}
          data-sveltekit-preload-data="off"
          class="inline-block rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("google.account.reconnect")}
        </a>
      {/if}

      {#if data.gmail_enabled}
        <form method="POST" action="?/googleGmailPrefs" use:enhance={busy.wrap()} class="space-y-3">
          <label class="flex items-start gap-2 text-sm text-text">
            <FormCheckbox
              name="gmail_sync_enabled"
              checked={connection.gmail_sync_enabled}
              class="mt-0.5"
            />
            <span>
              {t("google.account.gmail_sync")}
              <span class="block text-xs text-text-muted"
                >{t("google.account.gmail_sync_hint")}</span
              >
            </span>
          </label>
          <div class="max-w-xs">
            <label for="google-gmail-label" class="mb-1 block text-sm text-text"
              >{t("google.account.gmail_excluded_label")}</label
            >
            <input
              id="google-gmail-label"
              name="gmail_excluded_label"
              value={connection.gmail_excluded_label ?? ""}
              placeholder="geen-crm"
              class={inputClass}
            />
            <p class="mt-1 text-xs text-text-muted">
              {t("google.account.gmail_excluded_label_hint")}
            </p>
          </div>
          <Button type="submit" variant="secondary" loading={busy.active}>
            {t("common.save")}
          </Button>
        </form>
      {/if}

      <div class="border-t border-border pt-3">
        <button
          type="button"
          class="text-sm font-medium text-red-600 hover:underline"
          onclick={() => (confirmDisconnect = true)}
        >
          {t("google.account.disconnect")}
        </button>
      </div>
    </div>
  {/if}
</section>

<ConfirmDialog
  bind:open={confirmDisconnect}
  title={t("google.account.disconnect_title")}
  message={t("google.account.disconnect_message")}
  action="?/googleDisconnect"
/>
