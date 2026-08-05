<script lang="ts">
  /**
   * Client portal (#193, #296): give this person a login that lands on their own companies'
   * dashboards. Enable/disable is reversible; the API is the boundary.
   *
   * The card is drawn by the portal module rather than by the page that hosts it, so a second
   * kind of subject renders the same thing by passing different `data` — and so the locked
   * state, which is the module's own commercial concern, is stated once.
   */
  import { enhance } from "$app/forms";
  import { Lock, VenetianMask } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";
  import LockedButton from "$lib/core/ui/LockedButton.svelte";

  import type { PortalCardData } from "./types";

  let {
    data,
    subjectName,
    form,
  }: {
    data: PortalCardData;
    /** Whose login this is — the impersonation confirmation is about a person, not an id. */
    subjectName: string;
    /**
     * The host page's whole `form` result. Deliberately untyped beyond "an object": a route's
     * `ActionData` is the union of *every* action on that page, so a narrow type here would
     * only ever match a page whose sole action set is ours. The three keys the portal actions
     * return are read off it below.
     */
    form?: Record<string, unknown> | null;
  } = $props();

  const portalError = $derived(typeof form?.portalError === "string" ? form.portalError : null);
  const inviteMailFailed = $derived(form?.portalSaved === true && form?.portalEmail === false);

  // Submits in flight (#242): the firing button spins, its siblings freeze — enable, resend and
  // disable all mutate the same login, so only one may run at a time.
  const busy = new InFlight();
  let confirmImpersonate = $state(false);

  const status = $derived(data.state?.status ?? "none");
  const badge = $derived(
    status === "active"
      ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300"
      : status === "invited"
        ? "bg-amber-100 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
        : "text-text-muted ring-1 ring-inset ring-border",
  );
</script>

<section class="rounded-xl border border-border bg-surface-raised p-5">
  <div class="mb-3 flex flex-wrap items-center gap-2">
    <h2 class="text-sm font-semibold text-text">{t("portal.title")}</h2>
    {#if data.locked}
      <span
        class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium
          text-text-muted ring-1 ring-inset ring-border"
      >
        <Lock size={11} aria-hidden="true" />
        {t("portal.locked_badge")}
      </span>
    {:else}
      <span class="rounded-full px-2 py-0.5 text-[11px] font-medium {badge}">
        {t(`portal.status.${status}`)}
      </span>
    {/if}
  </div>

  <p class="mb-3 text-sm text-text-muted">
    {data.locked ? t("portal.locked_hint") : t("portal.hint")}
  </p>

  {#if portalError}
    <p class="mb-3 text-sm text-red-600 dark:text-red-400">{t(portalError)}</p>
  {/if}
  {#if inviteMailFailed}
    <p class="mb-3 text-sm text-amber-700 dark:text-amber-400">{t("portal.email_not_sent")}</p>
  {/if}

  <div class="flex flex-wrap gap-2">
    {#if data.locked}
      <!-- The invite the agency could have. Shown, not hidden: an entitlement is something the
           org can change, so the affordance stays and says how (docs/UX.md, #137). -->
      <LockedButton
        label={t("portal.locked")}
        feature={t("module.portal.label")}
        title={t("portal.locked_hint")}
        deployment={data.deployment}
        isInstanceOwner={data.isInstanceOwner}
      />
    {:else if status === "none" || status === "disabled"}
      <form method="POST" action="?/portalEnable" use:enhance={busy.wrap("enable")}>
        <Button size="sm" loading={busy.is("enable")} disabled={busy.active}>
          {status === "disabled" ? t("portal.reenable") : t("portal.enable")}
        </Button>
      </form>
    {:else}
      {#if status === "invited"}
        <form method="POST" action="?/portalResend" use:enhance={busy.wrap("resend")}>
          <Button variant="secondary" size="sm" loading={busy.is("resend")} disabled={busy.active}>
            {t("portal.resend")}
          </Button>
        </form>
      {/if}
      <form method="POST" action="?/portalDisable" use:enhance={busy.wrap("disable")}>
        <Button
          variant="danger-outline"
          size="sm"
          loading={busy.is("disable")}
          disabled={busy.active}
        >
          {t("portal.disable")}
        </Button>
      </form>
      <!-- Sign in as them (#296): its own permission, never implied by managing the login,
           and confirmed first — it is recorded on this record's trail under your name. -->
      {#if data.canImpersonate}
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={busy.active}
          onclick={() => (confirmImpersonate = true)}
        >
          <VenetianMask size={14} class="mr-1.5 inline" />
          {t("portal.impersonate")}
        </Button>
      {/if}
    {/if}
  </div>

  {#if data.canImpersonate && (status === "active" || status === "invited")}
    <p class="mt-3 text-xs text-text-muted">{t("portal.impersonate_hint")}</p>
  {/if}
</section>

<!-- Destroys nothing, so a primary confirm rather than the default red one (ConfirmDialog). -->
<ConfirmDialog
  bind:open={confirmImpersonate}
  title={t("portal.impersonate")}
  message={t("portal.impersonate_confirm", { name: subjectName })}
  confirmLabel={t("portal.impersonate")}
  variant="primary"
  action="?/portalImpersonate"
/>
