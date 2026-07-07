<script lang="ts">
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
</script>

<div class="flex min-h-screen items-center justify-center px-4">
  <div class="text-center">
    <p class="text-4xl font-bold text-neutral-900">{page.status}</p>
    <p class="mt-2 text-sm text-neutral-600">{t(page.error?.message ?? "errors.server")}</p>
    <a href="/" class="mt-4 inline-block text-sm text-brand underline">{t("nav.dashboard")}</a>
  </div>
</div>
