<script lang="ts">
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";

  const user = $derived(page.data.user);
</script>

<svelte:head>
  <title>{t("dashboard.title")}</title>
</svelte:head>

<h1 class="text-xl font-semibold text-neutral-900">{t("dashboard.title")}</h1>
<p class="mt-2 text-sm text-neutral-600">
  {t("dashboard.welcome", { name: user?.full_name || user?.email || "" })}
</p>
