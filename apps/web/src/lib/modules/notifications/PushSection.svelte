<script lang="ts">
  /**
   * Browser push notifications: enrol this device, list the others, revoke, test (#309).
   *
   * Entirely client-side rather than a form action, and deliberately so: everything it needs —
   * `Notification.permission`, the service-worker registration, the existing subscription — is
   * knowledge only the browser has, and none of it survives a round trip through SSR. The API
   * calls it makes are the same ones any other component makes.
   *
   * Four rules the screen obeys, each of which is a mistake somebody makes once:
   *
   *  - **Never prompt on load.** The permission prompt is only ever raised by a click. A prompt
   *    fired by a page render is the pattern browsers penalise, and it spends the single chance
   *    the user has to say yes at a moment they were not asking a question.
   *  - **`denied` is not ours to fix.** We cannot reopen the prompt, so the screen says it is a
   *    browser setting instead of rendering a button that always refuses (#253).
   *  - **iOS needs the app installed.** Safari has Web Push from 16.4, but only inside a
   *    home-screen PWA. That gets its own sentence rather than a button that silently fails.
   *  - **The devices are listed, with dates.** "Which of my four browsers is this?" is otherwise
   *    unanswerable, and revoking the wrong one is a silent loss.
   *
   * Which events actually push is not asked here — it is a column in the matrix above, exactly
   * like e-mail. Registering a browser and routing events to it are two decisions (#283's rule:
   * connecting a transport must not start pinging a phone).
   */
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import Button from "$lib/core/ui/Button.svelte";
  import { type PushState, status, subscribe, unsubscribe } from "$lib/modules/notifications/push";

  interface Device {
    id: string;
    user_agent: string | null;
    created_at: string;
    last_seen_at: string;
    last_success_at: string | null;
    current: boolean;
  }

  let pushState = $state<PushState | null>(null);
  let endpoint = $state<string | null>(null);
  let devices = $state<Device[]>([]);
  let pending = $state("");
  let notice = $state<{ ok: boolean; text: string } | null>(null);

  async function loadDevices(): Promise<void> {
    const query = endpoint ? `?endpoint=${encodeURIComponent(endpoint)}` : "";
    const response = await fetch(`/api/v1/notifications/push/subscriptions${query}`, {
      headers: { accept: "application/json" },
    });
    devices = response.ok ? await response.json() : [];
  }

  // Read-only on mount: this settles which of the five states we are in and never prompts.
  $effect(() => {
    void (async () => {
      const current = await status();
      pushState = current.state;
      endpoint = current.endpoint;
      if (current.state === "on" || current.state === "off") await loadDevices();
    })();
  });

  async function onEnable(): Promise<void> {
    pending = "enable";
    notice = null;
    try {
      const result = await subscribe();
      pushState = result.state;
      endpoint = result.endpoint;
      await loadDevices();
    } finally {
      pending = "";
    }
  }

  async function onDisable(): Promise<void> {
    pending = "disable";
    notice = null;
    try {
      const result = await unsubscribe();
      pushState = result.state;
      endpoint = result.endpoint;
      await loadDevices();
    } finally {
      pending = "";
    }
  }

  async function onRevoke(id: string): Promise<void> {
    pending = `revoke-${id}`;
    try {
      await fetch(`/api/v1/notifications/push/subscriptions/${id}`, { method: "DELETE" });
      // Revoking the row for *this* browser leaves its own subscription alive and orphaned, so
      // tear that down too rather than leaving a device that thinks it is still registered.
      if (devices.find((device) => device.id === id)?.current) {
        const result = await unsubscribe();
        pushState = result.state;
        endpoint = result.endpoint;
      }
      await loadDevices();
    } finally {
      pending = "";
    }
  }

  async function onTest(): Promise<void> {
    pending = "test";
    notice = null;
    try {
      const response = await fetch("/api/v1/notifications/push/test", { method: "POST" });
      const result = response.ok ? await response.json() : { ok: false, delivered: 0 };
      notice = {
        ok: result.ok === true,
        text: result.ok ? t("settings.push.test_sent") : t("settings.push.test_failed"),
      };
      // A test is as good a moment as a real send to learn a device is retired, and the API
      // prunes it — so the list is reloaded rather than left showing a device that is gone.
      await loadDevices();
    } finally {
      pending = "";
    }
  }

  const busy = $derived(pending !== "");
</script>

<section class="mt-8 rounded-xl border border-border bg-surface-raised p-6">
  <h2 class="mb-1 text-sm font-semibold text-text">{t("settings.push.title")}</h2>
  <p class="mb-4 text-sm text-text-muted">{t("settings.push.hint")}</p>

  {#if pushState === null}
    <p class="text-sm text-text-muted">{t("common.loading")}</p>
  {:else if pushState === "unsupported"}
    <p class="text-sm text-text-muted">{t("settings.push.unsupported")}</p>
  {:else if pushState === "needs-install"}
    <p class="text-sm text-text-muted">{t("settings.push.needs_install")}</p>
  {:else if pushState === "no-worker"}
    <!-- The browser can do this; the service worker that would receive the push is not running. -->
    <p class="text-sm text-text-muted">{t("settings.push.no_worker")}</p>
  {:else if pushState === "denied"}
    <!-- A browser setting we cannot reopen: say so instead of offering a control that refuses. -->
    <p class="text-sm text-text-muted">{t("settings.push.denied")}</p>
  {:else}
    <div class="flex flex-wrap items-center gap-2">
      {#if pushState === "on"}
        <Button
          variant="secondary"
          size="sm"
          onclick={onDisable}
          loading={pending === "disable"}
          disabled={busy}
        >
          {t("settings.push.disable")}
        </Button>
        <Button
          variant="secondary"
          size="sm"
          onclick={onTest}
          loading={pending === "test"}
          disabled={busy}
        >
          {t("settings.push.test")}
        </Button>
      {:else}
        <Button size="sm" onclick={onEnable} loading={pending === "enable"} disabled={busy}>
          {t("settings.push.enable")}
        </Button>
      {/if}
    </div>

    {#if notice}
      <p class="mt-2 text-sm" class:text-danger={!notice.ok} class:text-text-muted={notice.ok}>
        {notice.text}
      </p>
    {/if}

    {#if pushState === "off"}
      <p class="mt-2 text-sm text-text-muted">{t("settings.push.off_hint")}</p>
    {/if}

    {#if devices.length > 0}
      <h3 class="mt-6 mb-2 text-sm font-semibold text-text">{t("settings.push.devices")}</h3>
      <ul class="divide-y divide-border rounded-lg border border-border">
        {#each devices as device (device.id)}
          <li class="flex items-center gap-3 px-3 py-2 text-sm">
            <div class="min-w-0 flex-1">
              <span class="font-medium text-text">
                {device.user_agent || t("settings.push.unknown_device")}
              </span>
              {#if device.current}
                <span class="ml-2 rounded-full bg-surface px-2 py-0.5 text-[11px] text-text-muted">
                  {t("settings.push.this_device")}
                </span>
              {/if}
              <span class="block text-[11px] text-text-muted">
                {t("settings.push.last_seen")}: {fmtDateTime(device.last_seen_at)}
              </span>
            </div>
            <Button
              variant="danger-outline"
              size="xs"
              onclick={() => onRevoke(device.id)}
              loading={pending === `revoke-${device.id}`}
              disabled={busy}
            >
              {t("settings.push.revoke")}
            </Button>
          </li>
        {/each}
      </ul>
    {/if}
  {/if}
</section>
