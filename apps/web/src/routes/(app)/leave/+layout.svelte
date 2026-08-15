<script lang="ts">
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

  let { children } = $props();

  const path = $derived(page.url.pathname);
  const canApprove = $derived(can(page.data.user, "leave.request.approve"));
  /**
   * Beschikbaarheid is its **own** permission, never leave approval (#368). Gating it on
   * `leave.request.approve` — which is what the roster's ⋯ item did — makes the permission the
   * module deliberately invented unholdable on its own.
   *
   * The *kind* still decides whether a plain member sees it at all: every member holds
   * `leave.availability.write:own`, so the permission alone would put an availability tab on
   * every employee's page for a thing employees do not have. `employmentType === null` (no period
   * on file) shows it to nobody rather than to everybody.
   */
  const canReadAvailability = $derived(can(page.data.user, "leave.availability.read"));
  const showAvailability = $derived(
    canReadAvailability &&
      (can(page.data.user, "leave.availability.read", "any") ||
        page.data.employmentType === "freelance"),
  );
  const showTabs = $derived(canApprove || showAvailability);
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

{#if showTabs}
  <div class="mb-4 flex items-center gap-1" data-sveltekit-preload-data="hover">
    <a href="/leave" class={tabClass(path === "/leave")}>{t("leave.tab.mine")}</a>
    {#if canApprove}
      <a href="/leave/team" class={tabClass(path.startsWith("/leave/team"))}>
        {t("leave.tab.team")}
      </a>
    {/if}
    {#if showAvailability}
      <a href="/leave/availability" class={tabClass(path.startsWith("/leave/availability"))}>
        {t("leave.availability.title")}
      </a>
    {/if}
  </div>
{/if}

{@render children()}
