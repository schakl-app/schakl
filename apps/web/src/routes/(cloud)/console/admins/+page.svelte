<script lang="ts">
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";

  let { data, form } = $props();

  const busy = new InFlight();

  const inputClass =
    "w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  // The catalog comes from the API, so the console never hardcodes capability keys — a new
  // one appears here the moment it ships.
  const groups = $derived(
    [...new Set(data.catalog.map((c) => c.group))].map((group) => ({
      group,
      items: data.catalog.filter((c) => c.group === group),
    })),
  );
</script>

<svelte:head>
  <title>{t("instance.admins.title")}</title>
</svelte:head>

<h1 class="text-xl font-semibold text-text">{t("instance.admins.title")}</h1>
<p class="mt-1 max-w-2xl text-sm text-text-muted">{t("instance.admins.hint")}</p>

{#if form?.error}
  <p class="mt-4 rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:bg-rose-950/40 dark:text-rose-300">
    {t(form.error)}
  </p>
{/if}
{#if form?.saved || form?.invited}
  <p class="mt-4 text-sm text-emerald-700 dark:text-emerald-400">
    {t(form.invited ? "instance.admins.invited" : "instance.admins.saved")}
  </p>
{/if}

<!-- Existing principals -->
<div class="mt-6 space-y-4">
  {#each data.principals as principal (principal.user_id)}
    <section class="rounded-xl border border-border bg-surface-raised p-5">
      <div class="flex flex-wrap items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="truncate font-medium text-text">
            {principal.full_name || principal.email}
          </p>
          <p class="truncate text-sm text-text-muted">{principal.email}</p>
        </div>
        <div class="flex shrink-0 items-center gap-2">
          {#if !principal.is_active}
            <span class="rounded-full bg-surface px-2 py-0.5 text-xs text-text-muted">
              {t("instance.admins.inactive")}
            </span>
          {/if}
          <span
            class="rounded-full px-2 py-0.5 text-xs font-medium {principal.is_owner
              ? 'bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200'
              : 'bg-surface text-text-muted'}"
          >
            {t(principal.is_owner ? "instance.admins.owner" : "instance.admins.admin")}
          </span>
        </div>
      </div>

      {#if principal.is_owner}
        <!-- An owner holds everything implicitly, so there is nothing to tick. -->
        <p class="mt-3 text-sm text-text-muted">{t("instance.admins.hint")}</p>
      {:else}
        <form
          method="POST"
          action="?/update"
          use:enhance={busy.keep(`u-${principal.user_id}`)}
          class="mt-4"
        >
          <input type="hidden" name="user_id" value={principal.user_id} />
          <fieldset>
            <legend class="text-sm font-medium text-text">
              {t("instance.admins.capabilities")}
            </legend>
            <div class="mt-2 grid gap-x-6 gap-y-2 sm:grid-cols-2">
              {#each groups as { group, items } (group)}
                <div>
                  <p class="text-xs font-semibold uppercase tracking-wide text-text-muted">
                    {t(`instance.cap_group.${group}`)}
                  </p>
                  {#each items as cap (cap.key)}
                    <label class="mt-1 flex items-start gap-2 text-sm text-text">
                      <input
                        type="checkbox"
                        name="capabilities"
                        value={cap.key}
                        checked={principal.capabilities.includes(cap.key)}
                        class="mt-0.5"
                      />
                      <span>
                        {t(cap.label_key)}
                        {#if cap.sensitive}
                          <span
                            class="ml-1 text-xs text-rose-700 dark:text-rose-400"
                            title={t("instance.admins.sensitive_hint")}>●</span
                          >
                        {/if}
                      </span>
                    </label>
                  {/each}
                </div>
              {/each}
            </div>
          </fieldset>
          <div class="mt-4 flex flex-wrap gap-2">
            <Button
              variant="secondary"
              loading={busy.is(`u-${principal.user_id}`)}
              disabled={busy.active}
            >
              {t("common.save")}
            </Button>
          </div>
        </form>
      {/if}

      <div class="mt-3 flex flex-wrap gap-2">
        {#if !principal.is_owner}
          <form method="POST" action="?/promote" use:enhance={busy.wrap(`p-${principal.user_id}`)}>
            <input type="hidden" name="user_id" value={principal.user_id} />
            <Button
              variant="secondary"
              loading={busy.is(`p-${principal.user_id}`)}
              disabled={busy.active}
            >
              {t("instance.admins.make_owner")}
            </Button>
          </form>
        {/if}
        <form
          method="POST"
          action="?/revoke"
          use:enhance={busy.wrap(`r-${principal.user_id}`)}
          onsubmit={(event) => {
            if (!confirm(t("instance.admins.revoke_confirm", { email: principal.email })))
              event.preventDefault();
          }}
        >
          <input type="hidden" name="user_id" value={principal.user_id} />
          <Button
            variant="danger-outline"
            loading={busy.is(`r-${principal.user_id}`)}
            disabled={busy.active}
          >
            {t("instance.admins.revoke")}
          </Button>
        </form>
      </div>
    </section>
  {/each}
</div>

<!-- Invite -->
<section class="mt-8 max-w-xl rounded-xl border border-border bg-surface-raised p-5">
  <h2 class="text-base font-semibold text-text">{t("instance.admins.invite")}</h2>
  <p class="mt-1 text-sm text-text-muted">{t("instance.admins.invite_hint")}</p>
  <form method="POST" action="?/invite" use:enhance={busy.clear("invite")} class="mt-4 space-y-3">
    <input
      name="email"
      type="email"
      required
      placeholder={t("instance.admins.email")}
      aria-label={t("instance.admins.email")}
      class={inputClass}
    />
    <input
      name="full_name"
      type="text"
      placeholder={t("instance.admins.name")}
      aria-label={t("instance.admins.name")}
      class={inputClass}
    />
    <fieldset>
      <legend class="text-sm font-medium text-text">{t("instance.admins.capabilities")}</legend>
      <div class="mt-2 grid gap-x-6 gap-y-2 sm:grid-cols-2">
        {#each groups as { group, items } (group)}
          <div>
            <p class="text-xs font-semibold uppercase tracking-wide text-text-muted">
              {t(`instance.cap_group.${group}`)}
            </p>
            {#each items as cap (cap.key)}
              <label class="mt-1 flex items-start gap-2 text-sm text-text">
                <input type="checkbox" name="capabilities" value={cap.key} class="mt-0.5" />
                <span>{t(cap.label_key)}</span>
              </label>
            {/each}
          </div>
        {/each}
      </div>
    </fieldset>
    <Button loading={busy.is("invite")} disabled={busy.active}>
      {t("instance.admins.invite")}
    </Button>
  </form>
</section>
