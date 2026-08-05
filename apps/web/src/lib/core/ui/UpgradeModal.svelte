<script lang="ts">
  /**
   * "You don't have this yet" — the one dialog behind every locked control (issue #137).
   *
   * A licensed module the instance is not entitled to is not an error and not a missing
   * feature: it is something the agency can have. So the affordance stays visible and honest
   * (`LockedButton`), and this explains what to do about it — once, in one place, so a second
   * locked feature costs a `feature` string rather than another dialog.
   *
   * **What an upgrade means depends on the deployment**, which is the whole reason this takes
   * `deployment` rather than hardcoding a route:
   *
   * - `cloud` — a plan change. Self-service billing from inside the workspace is not built yet
   *   (epic #199 provisions orgs over the instance API), so until `upgradeHref` is passed the
   *   dialog explains rather than offering a button that goes nowhere.
   * - `self_hosted` — a licence key. That destination is **real today**: Instellingen →
   *   Licentie. It is instance-owner-only (`users.is_superuser`, a different authorization axis
   *   from any tenant role), so anyone else is told who to ask instead of being sent to a
   *   screen that will refuse them. Later this is also where a redirect out to the vendor's own
   *   portal belongs — same slot, same prop.
   *
   * A link a viewer cannot follow is a broken control (#253), which is why neither branch ever
   * renders a CTA it cannot honour.
   */
  import { Lock } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import Button from "$lib/core/ui/Button.svelte";
  import Modal from "$lib/core/ui/Modal.svelte";

  let {
    open = $bindable(false),
    feature,
    deployment = "self_hosted",
    isInstanceOwner = false,
    upgradeHref,
  }: {
    open?: boolean;
    /** What is locked, already translated — e.g. the module's own label. */
    feature: string;
    /** Instance posture, from the tenant payload the layout already loads. */
    deployment?: string;
    /** Only an instance owner can install a licence key, so only they get that CTA. */
    isInstanceOwner?: boolean;
    /**
     * Where "upgrade" goes, when the caller knows better than the defaults below. The slot the
     * cloud subscription flow and the self-hosted vendor portal will both land in.
     */
    upgradeHref?: string;
  } = $props();

  const isCloud = $derived(deployment === "cloud");
  // Self-hosted falls back to the licence screen, which exists — but only for the one account
  // allowed to use it. Cloud has no in-app destination yet, so it gets none rather than a guess.
  const href = $derived(
    upgradeHref ?? (!isCloud && isInstanceOwner ? "/settings/license" : undefined),
  );
  const body = $derived(
    isCloud
      ? t("upgrade.body_cloud")
      : isInstanceOwner
        ? t("upgrade.body_selfhosted_admin")
        : t("upgrade.body_selfhosted"),
  );
  // Who to ask, when there is no button to press. Deployment-specific for the same reason the
  // body is: a cloud tenant administers no instance — that box is ours — so pointing them at
  // "whoever administers this installation" names a person who does not exist on their side.
  // What they have is a subscription and somebody who manages it.
  const noRoute = $derived(
    isCloud ? t("upgrade.body_no_route_cloud") : t("upgrade.body_no_route"),
  );
</script>

<Modal bind:open title={t("upgrade.title", { feature })}>
  <div class="flex gap-3">
    <span
      class="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-full bg-surface
        text-text-muted ring-1 ring-inset ring-border"
      aria-hidden="true"
    >
      <Lock size={16} />
    </span>
    <div class="space-y-2">
      <p class="text-sm text-text-muted">{body}</p>
      {#if !href}
        <p class="text-sm text-text-muted">{noRoute}</p>
      {/if}
    </div>
  </div>
  <div class="mt-5 flex flex-wrap justify-end gap-2">
    <button
      type="button"
      class="rounded-lg border border-border px-4 py-2 text-sm text-text"
      onclick={() => (open = false)}>{t("upgrade.dismiss")}</button
    >
    {#if href}
      <Button onclick={() => (window.location.href = href)}>
        {isCloud ? t("upgrade.cta_cloud") : t("upgrade.cta_selfhosted")}
      </Button>
    {/if}
  </div>
</Modal>
