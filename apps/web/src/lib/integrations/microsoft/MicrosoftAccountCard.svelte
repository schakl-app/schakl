<script lang="ts">
  /**
   * The personal "Microsoft koppelen" card on Instellingen → Account (docs/MICROSOFT.md §1).
   *
   * Connecting is a *separate grant from login* — this card starts the connect flow
   * (`/api/v1/microsoft/oauth/connect`), never the OIDC login. Outlook mail is per-user and
   * opt-in: the checkbox adds the scope to the consent, and the mailbox toggles live here, on the
   * person, not in org settings. The Google card's twin, and deliberately the same shape: a viewer
   * who holds both accounts reads two cards that ask the same questions in the same order.
   *
   * **Host contract:** the account page exposes `?/microsoftDisconnect`, `?/microsoftCalendars`
   * and `?/microsoftOutlookPrefs`.
   */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import FormCheckbox from "$lib/core/ui/FormCheckbox.svelte";

  interface Connection {
    email: string;
    status: string;
    scopes?: string[];
    outlook_sync_enabled: boolean;
    outlook_excluded_category?: string | null;
    last_error?: string | null;
  }

  interface MyConnection {
    connected: boolean;
    connection?: Connection | null;
    configured: boolean;
    calendar_enabled: boolean;
    onedrive_enabled: boolean;
    outlook_enabled: boolean;
  }

  interface CalendarEntry {
    id: string;
    summary: string;
    primary: boolean;
    access_role: string;
    selected: boolean;
  }

  let {
    data,
    status,
    calendars = Promise.resolve(null),
  }: {
    data: MyConnection;
    status: string | null;
    /** The viewer's calendar list, streamed — `null` while unknown or unavailable. */
    calendars?: Promise<CalendarEntry[] | null>;
  } = $props();

  let includeOutlook = $state(false);
  let confirmDisconnect = $state(false);

  // Resolved into state, never awaited in the markup: a save invalidates the page, and a raw
  // `{#await}` would blank the whole checklist on every one (docs/PERFORMANCE.md).
  let calendarList = $state<CalendarEntry[] | null>(null);
  $effect(() => {
    void calendars.then((value) => (calendarList = value));
  });
  // The section is worth its space only when there is a choice to make: a viewer whose
  // account holds nothing but the default calendar sees the card exactly as it always was.
  const selectableCalendars = $derived((calendarList ?? []).filter((c) => !c.primary));

  const busy = new InFlight();

  const connection = $derived(data.connection);
  // The mailbox is a separate consent (docs/MICROSOFT.md §1): an account connected for the
  // calendar alone holds no mail scope, and the toggle below would then opt a mailbox in that
  // the poller can never read. So the card says so and offers the one act that fixes it — a
  // reconnect asking for the mail scope — rather than drawing a switch that silently does
  // nothing (#253).
  const mailScopeMissing = $derived(
    Boolean(
      data.outlook_enabled &&
      connection &&
      connection.status === "active" &&
      !(connection.scopes ?? []).includes("Mail.Read"),
    ),
  );
  const surfaces = $derived(
    [
      data.calendar_enabled ? t("microsoft.surface.calendar") : null,
      data.onedrive_enabled ? t("microsoft.surface.onedrive") : null,
      data.outlook_enabled ? t("microsoft.surface.outlook") : null,
    ].filter(Boolean),
  );

  // Same-host navigation: Traefik routes `/api/` to the API, so the session cookie and the
  // tenant hostname ride along — exactly what the connect flow's require_context needs.
  const connectHref = $derived.by(() => {
    const params = new URLSearchParams();
    if (includeOutlook) params.set("include_outlook", "true");
    const query = params.toString();
    return `/api/v1/microsoft/oauth/connect${query ? `?${query}` : ""}`;
  });
  /** A reconnect that adds the mailbox to an existing grant — scopes union on the callback. */
  const connectMailboxHref = "/api/v1/microsoft/oauth/connect?include_outlook=true";

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

<section class="rounded-xl border border-border bg-surface-raised p-5">
  <h2 class="text-sm font-semibold text-text">{t("microsoft.account.title")}</h2>
  <p class="mt-1 text-sm text-text-muted">{t("microsoft.account.hint")}</p>

  {#if status === "connected"}
    <p
      class="mt-3 rounded-lg border border-emerald-300 bg-emerald-50 px-3 py-2 text-sm text-emerald-800 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-200"
    >
      {t("microsoft.account.just_connected")}
    </p>
  {:else if status === "error"}
    <p
      class="mt-3 rounded-lg border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-800 dark:border-red-700 dark:bg-red-950 dark:text-red-200"
    >
      {t("microsoft.account.connect_failed")}
    </p>
  {/if}

  {#if !data.configured}
    <p class="mt-3 text-sm text-text-muted">{t("microsoft.account.not_configured")}</p>
  {:else if !data.connected}
    <div class="mt-4 space-y-3">
      {#if surfaces.length > 0}
        <p class="text-sm text-text">
          {t("microsoft.account.grants")}
          <span class="text-text-muted">{surfaces.join(" · ")}</span>
        </p>
      {/if}
      {#if data.outlook_enabled}
        <label class="flex items-start gap-2 text-sm text-text">
          <input type="checkbox" bind:checked={includeOutlook} class="mt-0.5" />
          <span>
            {t("microsoft.account.include_outlook")}
            <span class="block text-xs text-text-muted"
              >{t("microsoft.account.include_outlook_hint")}</span
            >
          </span>
        </label>
      {/if}
      <a
        href={connectHref}
        data-sveltekit-preload-data="off"
        class="inline-block rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
      >
        {t("microsoft.account.connect")}
      </a>
    </div>
  {:else if connection}
    <div class="mt-4 space-y-4">
      <div class="flex flex-wrap items-center gap-2">
        <span class="text-sm font-medium text-text">{connection.email}</span>
        {#if connection.status === "active"}
          <span
            class="rounded-full bg-emerald-100 px-2 py-0.5 text-[11px] font-medium text-emerald-800 dark:bg-emerald-500/15 dark:text-emerald-400"
            >{t("microsoft.account.status_active")}</span
          >
        {:else}
          <span
            class="rounded-full bg-red-100 px-2 py-0.5 text-[11px] font-medium text-red-800 dark:bg-red-500/15 dark:text-red-400"
            >{t("microsoft.account.status_error")}</span
          >
        {/if}
      </div>

      {#if connection.status !== "active"}
        <p class="text-sm text-text-muted">{t("microsoft.account.reconnect_hint")}</p>
        <a
          href={connectHref}
          data-sveltekit-preload-data="off"
          class="inline-block rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        >
          {t("microsoft.account.reconnect")}
        </a>
      {/if}

      {#if data.calendar_enabled && connection.status === "active" && selectableCalendars.length > 0}
        <!-- Which calendars sync. The default calendar always does — stated rather than offered
             as a checkbox — and each ticked extra calendar gets its own colour/hide row in the
             Agenda's feeds menu. -->
        <form
          method="POST"
          action="?/microsoftCalendars"
          use:enhance={busy.keep("calendars")}
          class="space-y-2 border-t border-border pt-3"
        >
          <p class="text-sm font-medium text-text">{t("microsoft.account.calendars_title")}</p>
          <p class="text-xs text-text-muted">{t("microsoft.account.calendars_hint")}</p>
          <ul class="space-y-1.5">
            {#each selectableCalendars as calendar (calendar.id)}
              <li>
                <label class="flex items-start gap-2 text-sm text-text">
                  <FormCheckbox
                    name="calendar_ids"
                    value={calendar.id}
                    checked={calendar.selected}
                    class="mt-0.5"
                  />
                  <span class="min-w-0">
                    <span class="block truncate">{calendar.summary || calendar.id}</span>
                  </span>
                </label>
              </li>
            {/each}
          </ul>
          <Button type="submit" variant="secondary" size="sm" loading={busy.is("calendars")}>
            {t("common.save")}
          </Button>
        </form>
      {/if}

      {#if mailScopeMissing}
        <div class="space-y-2 border-t border-border pt-3">
          <p class="text-sm text-text-muted">{t("microsoft.account.outlook_scope_missing")}</p>
          <a
            href={connectMailboxHref}
            data-sveltekit-preload-data="off"
            class="inline-block rounded-lg border border-border px-3 py-1.5 text-sm font-medium text-text hover:border-brand"
          >
            {t("microsoft.account.connect_mailbox")}
          </a>
        </div>
      {:else if data.outlook_enabled}
        <form
          method="POST"
          action="?/microsoftOutlookPrefs"
          use:enhance={busy.keep()}
          class="space-y-3"
        >
          <label class="flex items-start gap-2 text-sm text-text">
            <FormCheckbox
              name="outlook_sync_enabled"
              checked={connection.outlook_sync_enabled}
              class="mt-0.5"
            />
            <span>
              {t("microsoft.account.outlook_sync")}
              <span class="block text-xs text-text-muted"
                >{t("microsoft.account.outlook_sync_hint")}</span
              >
            </span>
          </label>
          <div class="max-w-xs">
            <label for="microsoft-outlook-category" class="mb-1 block text-sm text-text"
              >{t("microsoft.account.outlook_excluded_category")}</label
            >
            <input
              id="microsoft-outlook-category"
              name="outlook_excluded_category"
              value={connection.outlook_excluded_category ?? ""}
              placeholder="geen-crm"
              class={inputClass}
            />
            <p class="mt-1 text-xs text-text-muted">
              {t("microsoft.account.outlook_excluded_category_hint")}
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
          {t("microsoft.account.disconnect")}
        </button>
      </div>
    </div>
  {/if}
</section>

<ConfirmDialog
  bind:open={confirmDisconnect}
  title={t("microsoft.account.disconnect_title")}
  message={t("microsoft.account.disconnect_message")}
  action="?/microsoftDisconnect"
  confirmLabel={t("microsoft.account.disconnect_title")}
/>
