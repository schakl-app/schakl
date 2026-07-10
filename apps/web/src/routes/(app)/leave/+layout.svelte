<script lang="ts">
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";

  let { children } = $props();

  const path = $derived(page.url.pathname);
  const canApprove = $derived(can(page.data.user, "leave.request.approve"));
  const tabClass = (active: boolean) =>
    `rounded-lg px-3 py-1.5 text-sm font-medium ${
      active ? "bg-brand text-white" : "text-text-muted hover:bg-surface"
    }`;
</script>

{#if canApprove}
  <div class="mb-4 flex items-center gap-1" data-sveltekit-preload-data="hover">
    <a href="/leave" class={tabClass(path === "/leave")}>{t("leave.tab.mine")}</a>
    <a href="/leave/team" class={tabClass(path.startsWith("/leave/team"))}>
      {t("leave.tab.team")}
    </a>
  </div>
{/if}

{@render children()}
