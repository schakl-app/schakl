<script lang="ts">
  import { enhance } from "$app/forms";
  import { localeLabel, t } from "$lib/core/i18n";
  import { pageTitle } from "$lib/core/title";
  import { moduleLabel } from "$lib/core/registry";

  let { data, form } = $props();

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const labelClass = "mb-1 block text-sm font-medium text-text";
  const sectionTitle = "text-sm font-semibold uppercase tracking-wide text-text-muted";

  let orgName = $state(form?.values?.org_name ?? "");
  let slug = $state(form?.values?.slug ?? "");
  let slugTouched = $state(Boolean(form?.values?.slug));

  // Suggest a slug from the organisation name until the operator edits it by hand.
  function suggestSlug(name: string): string {
    return name
      .toLowerCase()
      .normalize("NFKD")
      .replace(/[\u0300-\u036f]/g, "")
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 63);
  }
  $effect(() => {
    if (!slugTouched) slug = suggestSlug(orgName);
  });
</script>

<svelte:head>
  <title>{pageTitle(t("setup.title"))}</title>
</svelte:head>

<div class="flex min-h-screen items-center justify-center px-4 py-10">
  <div class="w-full max-w-xl rounded-2xl border border-border bg-surface-raised p-8 shadow-sm">
    <h1 class="text-xl font-semibold text-text">{t("setup.title")}</h1>
    <p class="mt-1 text-sm text-text-muted">
      {data.cloud ? t("setup.cloud_subtitle") : t("setup.subtitle")}
    </p>

    <form method="POST" use:enhance class="mt-6 space-y-8">
      <input type="hidden" name="cloud" value={data.cloud ? "true" : "false"} />
      {#if !data.cloud}
        <section class="space-y-4">
          <h2 class={sectionTitle}>{t("setup.section_org")}</h2>
          <div>
            <label for="org_name" class={labelClass}>{t("setup.org_name")}</label>
            <input
              id="org_name"
              name="org_name"
              required
              maxlength="255"
              bind:value={orgName}
              class={inputClass}
            />
          </div>
          <div>
            <label for="slug" class={labelClass}>{t("setup.slug")}</label>
            <input
              id="slug"
              name="slug"
              required
              maxlength="63"
              pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?"
              bind:value={slug}
              oninput={() => (slugTouched = true)}
              class="{inputClass} font-mono"
            />
            <p class="mt-1 text-xs text-text-muted">{t("setup.slug_hint")}</p>
            {#if form?.fields?.slug}
              <p class="mt-1 text-sm text-red-600 dark:text-red-400">{t(form.fields.slug)}</p>
            {/if}
          </div>
        </section>

        <section class="space-y-4">
          <h2 class={sectionTitle}>{t("setup.section_brand")}</h2>
          <div>
            <label for="brand_name" class={labelClass}>{t("settings.branding.brand_name")}</label>
            <input
              id="brand_name"
              name="brand_name"
              maxlength="255"
              placeholder={orgName}
              value={form?.values?.brand_name ?? ""}
              class={inputClass}
            />
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div>
              <label for="primary_color" class={labelClass}>
                {t("settings.branding.primary_color")}
              </label>
              <input
                id="primary_color"
                name="primary_color"
                type="color"
                value="#4f46e5"
                class="h-10 w-full cursor-pointer rounded-lg border border-border"
              />
            </div>
            <div>
              <label for="accent_color" class={labelClass}>
                {t("settings.branding.accent_color")}
              </label>
              <input
                id="accent_color"
                name="accent_color"
                type="color"
                value="#0ea5e9"
                class="h-10 w-full cursor-pointer rounded-lg border border-border"
              />
            </div>
          </div>
          <div>
            <label for="locale" class={labelClass}>{t("settings.branding.default_locale")}</label>
            <select id="locale" name="locale" class={inputClass}>
              {#each data.locales as locale (locale)}
                <option value={locale} selected={locale === data.defaultLocale}>
                  {localeLabel(locale)}
                </option>
              {/each}
            </select>
          </div>
        </section>

        <section class="space-y-2">
          <h2 class={sectionTitle}>{t("setup.section_modules")}</h2>
          <p class="text-xs text-text-muted">{t("setup.modules_hint")}</p>
          <div class="grid grid-cols-2 gap-2">
            {#each data.availableModules as module (module)}
              <label
                class="flex items-center gap-2 rounded-lg border border-border px-3 py-2 text-sm"
              >
                <input
                  type="checkbox"
                  name="modules"
                  value={module}
                  checked
                  disabled={module === "companies"}
                  class="accent-brand"
                />
                {moduleLabel(module)}
              </label>
            {/each}
            <!-- The hub module is mandatory; a disabled checkbox posts nothing, so mirror it. -->
            <input type="hidden" name="modules" value="companies" />
          </div>
        </section>
      {/if}

      <section class="space-y-4">
        <h2 class={sectionTitle}>{t("setup.section_owner")}</h2>
        <p class="text-xs text-text-muted">{t("setup.owner_hint")}</p>
        <div>
          <label for="owner_full_name" class={labelClass}>{t("setup.owner_full_name")}</label>
          <input
            id="owner_full_name"
            name="owner_full_name"
            maxlength="255"
            autocomplete="name"
            value={form?.values?.owner_full_name ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="owner_email" class={labelClass}>{t("auth.email")}</label>
          <input
            id="owner_email"
            name="owner_email"
            type="email"
            required
            autocomplete="username"
            value={form?.values?.owner_email ?? ""}
            class={inputClass}
          />
        </div>
        <div>
          <label for="owner_password" class={labelClass}>{t("auth.password")}</label>
          <input
            id="owner_password"
            name="owner_password"
            type="password"
            required
            minlength="8"
            autocomplete="new-password"
            class={inputClass}
          />
          <p class="mt-1 text-xs text-text-muted">{t("setup.password_hint")}</p>
        </div>
      </section>

      {#if form?.error}
        <p class="text-sm text-red-600 dark:text-red-400">{t(form.error)}</p>
      {/if}

      <button
        type="submit"
        class="w-full rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white transition hover:opacity-90"
      >
        {t("setup.submit")}
      </button>
    </form>
  </div>
</div>
