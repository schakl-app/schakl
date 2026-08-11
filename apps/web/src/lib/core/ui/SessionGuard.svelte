<script lang="ts">
  /**
   * What a tab does when the session ends underneath it.
   *
   * The failure it replaces: you sign out in one tab, and every other tab keeps drawing a
   * working CRM until you happen to click something. The data is stale, the controls all
   * refuse, and nothing on screen says why — the page is still *claiming* to be signed in.
   *
   * Three decisions are worth writing down, because the obvious version of each is worse.
   *
   * **It signs you back in here, rather than sending you to the login screen.** A redirect
   * throws the page away: the half-written note, the filters you set, the scroll position, the
   * row you had open. The whole point of catching this is that nothing needed to be lost —
   * `/session/signin` re-establishes the cookie and `invalidateAll()` re-reads the data, and
   * the screen you were on is still the screen you are on. The login screen stays available
   * (`?next=` brings you back), for SSO orgs and for signing in as someone else.
   *
   * **It can be put away, and then it nags.** A blocking dialog you cannot dismiss is the
   * right instinct and the wrong control: the page behind it may hold text that only exists in
   * that textarea, and refusing to let someone copy it out is data loss committed in the name
   * of preventing confusion. So "Nu niet" collapses it to a bar that will not go away.
   *
   * **A prompt is only raised on evidence.** The broadcast comes from a tab of this same
   * browser that had just deleted the cookie; the probe is the server's own answer. A network
   * failure is neither, and `probeSession` reads it as "keep going" for that reason.
   */
  import { LogIn, ShieldAlert } from "@lucide/svelte";

  import { invalidateAll } from "$app/navigation";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import {
    announceSignedIn,
    guardMounted,
    onSessionMessage,
    PROBE_INTERVAL_MS,
    probeSession,
    type SessionState,
  } from "$lib/core/session-watch";
  import Button from "./Button.svelte";
  import PasswordInput from "./PasswordInput.svelte";

  /** Why the session ended — the sentence differs, and so does how sure we are. */
  type Reason = "signed_out" | "expired";

  let ended = $state<Reason | null>(null);
  /** Put away for this tab: the bar replaces the dialog until they ask for it back. */
  let minimized = $state(false);
  /** What this org offers as a way back in; fetched once, when it first matters. */
  let options = $state<SessionState | null>(null);

  let email = $state("");
  let password = $state("");
  let code = $state("");
  let challenge = $state<{ token: string; methods: string[]; smsSentTo?: string } | null>(null);
  let method = $state<"totp" | "backup">("totp");
  let busy = $state(false);
  let errorKey = $state<string | null>(null);
  let lastProbe = 0;

  const localLogin = $derived(options?.localLogin ?? true);
  const canSms = $derived(challenge?.methods.includes("sms") ?? false);
  /** Sign-in lands them back here rather than on the dashboard, if they take the long way. */
  const loginHref = $derived(
    `/login?next=${encodeURIComponent(page.url.pathname + page.url.search)}`,
  );

  function raise(reason: Reason) {
    if (ended) return;
    ended = reason;
    minimized = false;
    // The address is not a secret and it is almost always the right one: this tab was being
    // used by that person a moment ago. Saves the most tedious keystrokes of the recovery.
    email = page.data.user?.email ?? "";
    password = "";
    challenge = null;
    errorKey = null;
    void probeSession(true).then((state) => {
      options = state;
      // The probe that fetches the options is also a probe: if it says the session is fine, a
      // broadcast we acted on was wrong (a sign-out that failed, a race with a sign-in).
      if (state.signedIn) void recover();
    });
  }

  /**
   * Stand down.
   *
   * `reread` is the whole question of whether work survives. `invalidateAll()` re-runs every
   * load and hands the page a fresh `data` — which is right when a *different* person is now
   * signed in (the screen must stop showing what the previous one could see) and actively
   * harmful otherwise: 51 inputs in this app take their value straight from `data`, and a
   * re-read is the only thing in this whole flow that could overwrite what somebody had typed.
   *
   * For the same person there is nothing to fix. The page's data is exactly as stale as it was
   * a minute ago; the session ending did not make it staler. So: leave it alone.
   */
  async function recover(reread = false) {
    ended = null;
    minimized = false;
    password = "";
    code = "";
    challenge = null;
    errorKey = null;
    if (reread) await invalidateAll();
  }

  /** Ask the server, but not more than once every {@link PROBE_INTERVAL_MS}. */
  async function check(force = false) {
    const now = performance.now();
    if (!force && now - lastProbe < PROBE_INTERVAL_MS) return;
    lastProbe = now;
    const state = await probeSession();
    if (!state.signedIn) raise("expired");
    else if (ended) await recover(state.userId !== page.data.user?.id);
  }

  // Let the rest of the app know something is listening, so a screen with no guard on it (the
  // login page's own failed submits) never pays for a question nobody would answer.
  $effect(() => guardMounted());

  $effect(() =>
    onSessionMessage((message) => {
      if (message.kind === "signed-out") raise("signed_out");
      else if (message.kind === "expired") raise("expired");
      else void recover(message.userId !== page.data.user?.id);
    }),
  );

  // A tab is worth asking about when somebody looks at it again — not on a timer. `pageshow`
  // is the bfcache case (Back onto a page rendered before the sign-out), where `visibilitychange`
  // does not reliably fire.
  $effect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible") void check();
    };
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("focus", onVisible);
    window.addEventListener("pageshow", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("focus", onVisible);
      window.removeEventListener("pageshow", onVisible);
    };
  });

  async function post(body: Record<string, unknown>) {
    busy = true;
    errorKey = null;
    try {
      const response = await fetch("/session/signin", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      return (await response.json().catch(() => null)) as Record<string, unknown> | null;
    } catch {
      return { error: "errors.server" };
    } finally {
      busy = false;
    }
  }

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    const result = challenge
      ? await post({
          challengeToken: challenge.token,
          code,
          method: challenge.smsSentTo ? "sms" : method,
        })
      : await post({ email, password });

    if (result?.ok) {
      const userId = typeof result.userId === "string" ? result.userId : null;
      // Tell the other tabs first: they are stuck on the same wall, and the id is what lets
      // each of them decide whether re-reading its own page is necessary or destructive.
      announceSignedIn(userId);
      await recover(userId !== (page.data.user?.id ?? null));
      return;
    }
    if (result?.twoFactor) {
      challenge = {
        token: String(result.challengeToken),
        methods: (result.methods as string[]) ?? ["totp"],
      };
      code = "";
      return;
    }
    // A challenge that can never be redeemed again drops back to the password step rather
    // than leaving them typing codes into a dead token.
    if (result?.restart) challenge = null;
    errorKey = typeof result?.error === "string" ? result.error : "errors.server";
  }

  async function sendSms() {
    if (!challenge) return;
    const result = await post({ challengeToken: challenge.token, sms: true });
    if (typeof result?.smsSentTo === "string") {
      challenge = { ...challenge, smsSentTo: result.smsSentTo };
    } else {
      errorKey = typeof result?.error === "string" ? result.error : "errors.server";
    }
  }

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  /** Put the caret in the field they actually have to fill in, whichever step this is. */
  function focusOnMount(node: HTMLElement) {
    const field = node.matches("input") ? node : node.querySelector<HTMLElement>("input");
    field?.focus();
  }
</script>

{#if ended && !minimized}
  <!-- Not `Modal`: this one must not close on Escape or on a backdrop click. Behind it the
       page is intact and legible on purpose — anything unsaved there is still copyable. -->
  <div class="fixed inset-0 z-[60] flex items-center justify-center overflow-y-auto p-4">
    <div class="fixed inset-0 bg-neutral-900/50 backdrop-blur-[2px]"></div>
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="session-ended-title"
      class="relative w-full max-w-sm rounded-2xl border border-border bg-surface-raised p-7 shadow-2xl"
    >
      <div class="mb-5 text-center">
        {#if page.data.theme?.logoUrl}
          <img src={page.data.theme.logoUrl} alt="" class="mx-auto mb-4 h-9" />
        {:else}
          <div
            class="mx-auto mb-4 flex h-11 w-11 items-center justify-center rounded-full bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
          >
            <ShieldAlert size={22} />
          </div>
        {/if}
        <h2 id="session-ended-title" class="text-base font-semibold text-text">
          {t("session.ended.title")}
        </h2>
        <p class="mt-1.5 text-sm text-text-muted">
          {ended === "signed_out" ? t("session.ended.signed_out") : t("session.ended.expired")}
        </p>
        <p class="mt-1 text-sm text-text-muted">{t("session.ended.hint")}</p>
      </div>

      {#if challenge}
        <!-- The second factor, in place: the challenge redeems exactly as it does on the
             login screen, because it is the same call. -->
        <form onsubmit={submit} class="space-y-4">
          <p class="text-sm text-text-muted">
            {#if challenge.smsSentTo}
              {t("auth.two_factor_sms_sent", { phone: challenge.smsSentTo })}
            {:else if method === "backup"}
              {t("auth.two_factor_backup_hint")}
            {:else}
              {t("auth.two_factor_hint")}
            {/if}
          </p>
          <div>
            <label for="session-code" class="mb-1 block text-sm font-medium text-text">
              {method === "backup" && !challenge.smsSentTo
                ? t("auth.two_factor_backup_code")
                : t("auth.two_factor_code")}
            </label>
            <input
              id="session-code"
              bind:value={code}
              use:focusOnMount
              type="text"
              required
              autocomplete="one-time-code"
              inputmode={method === "backup" ? "text" : "numeric"}
              class={inputClass}
            />
          </div>

          {#if errorKey}
            <p class="text-sm text-red-600 dark:text-red-400">{t(errorKey)}</p>
          {/if}

          <Button type="submit" class="w-full" loading={busy}>{t("auth.two_factor_verify")}</Button>

          <div class="space-y-1 text-center text-sm">
            <button
              type="button"
              class="block w-full text-text-muted hover:text-brand"
              onclick={() => (method = method === "totp" ? "backup" : "totp")}
            >
              {method === "totp" ? t("auth.two_factor_use_backup") : t("auth.two_factor_use_totp")}
            </button>
            {#if canSms && !challenge.smsSentTo}
              <button
                type="button"
                class="block w-full text-text-muted hover:text-brand"
                onclick={sendSms}
              >
                {t("auth.two_factor_send_sms")}
              </button>
            {/if}
          </div>
        </form>
      {:else if localLogin}
        <form onsubmit={submit} class="space-y-4">
          <div>
            <label for="session-email" class="mb-1 block text-sm font-medium text-text">
              {t("auth.email")}
            </label>
            <input
              id="session-email"
              bind:value={email}
              type="email"
              name="email"
              required
              autocomplete="username"
              class={inputClass}
            />
          </div>
          <div>
            <label for="session-password" class="mb-1 block text-sm font-medium text-text">
              {t("auth.password")}
            </label>
            <!-- Focus lands here, not on the address: the address is already filled in with
                 whoever was using this tab. -->
            <div use:focusOnMount>
              <PasswordInput id="session-password" name="password" bind:value={password} required />
            </div>
          </div>

          {#if errorKey}
            <p class="text-sm text-red-600 dark:text-red-400">{t(errorKey)}</p>
          {/if}

          <Button type="submit" class="w-full" loading={busy}>{t("auth.sign_in_action")}</Button>
        </form>
      {:else}
        <!-- An org that enforces SSO has no password form to offer; say so and hand over. -->
        <p class="text-center text-sm text-text-muted">{t("auth.local_login_disabled")}</p>
      {/if}

      {#if options?.oidcEnabled}
        <a
          href="/api/v1/auth/oidc/login"
          class="mt-3 block w-full rounded-lg border border-border px-4 py-2 text-center text-sm font-medium text-text hover:bg-surface"
        >
          {t("auth.sign_in_with_sso", { name: options.oidcName || "SSO" })}
        </a>
      {/if}

      <div class="mt-5 flex items-center justify-between border-t border-border pt-4 text-sm">
        <a href={loginHref} data-sveltekit-reload class="text-text-muted hover:text-brand">
          {t("session.ended.other_account")}
        </a>
        <button
          type="button"
          class="text-text-muted hover:text-text"
          onclick={() => (minimized = true)}
        >
          {t("session.ended.later")}
        </button>
      </div>
    </div>
  </div>
{:else if ended}
  <!-- Put away, not dismissed. Amber + a glyph, never the brand colour: on a tenant whose
       brand *is* gold, a coloured word carries no state at all. -->
  <div class="pointer-events-none fixed inset-x-0 bottom-0 z-[60] flex justify-center p-4">
    <div
      role="status"
      class="pointer-events-auto flex items-center gap-3 rounded-full border border-amber-500/40 bg-amber-500 px-4 py-2 text-sm font-medium text-amber-950 shadow-lg"
    >
      <ShieldAlert size={16} class="shrink-0" />
      <span>{t("session.ended.bar")}</span>
      <button
        type="button"
        class="flex shrink-0 items-center gap-1.5 rounded-full bg-amber-950/10 px-3 py-1 font-semibold hover:bg-amber-950/20"
        onclick={() => (minimized = false)}
      >
        <LogIn size={14} />
        {t("session.ended.bar_action")}
      </button>
    </div>
  </div>
{/if}
