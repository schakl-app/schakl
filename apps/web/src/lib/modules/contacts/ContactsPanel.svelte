<script lang="ts">
  /** Company-detail panel: the contacts attached to this company, with quick-add (CLAUDE.md §6). */
  import { enhance } from "$app/forms";
  import { t } from "$lib/core/i18n";

  let { data }: { companyId: string; data: Record<string, unknown> } = $props();

  interface PanelContact {
    id: string;
    first_name: string;
    last_name: string | null;
    email: string | null;
    job_title: string | null;
  }
  const contacts = $derived((data.contacts ?? []) as PanelContact[]);

  let showAdd = $state(false);

  const inputClass =
    "w-full rounded-lg border border-neutral-300 px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand";
</script>

{#if contacts.length === 0}
  <p class="text-sm text-neutral-500">{t("contacts.empty")}</p>
{:else}
  <ul class="divide-y divide-neutral-100">
    {#each contacts as contact (contact.id)}
      <li class="flex items-center justify-between py-2">
        <a href="/contacts/{contact.id}" class="text-sm font-medium text-neutral-900 hover:text-brand">
          {contact.first_name}
          {contact.last_name ?? ""}
        </a>
        <span class="text-xs text-neutral-500">
          {contact.job_title ?? contact.email ?? ""}
        </span>
      </li>
    {/each}
  </ul>
{/if}

{#if showAdd}
  <form method="POST" action="?/addContact"
    use:enhance={() => ({ update }) => { showAdd = false; void update(); }}
    class="mt-3 space-y-2 rounded-lg border border-neutral-200 bg-neutral-50/50 p-3">
    <div class="grid grid-cols-2 gap-2">
      <input name="first_name" required placeholder={t("contacts.first_name")} class={inputClass} />
      <input name="last_name" placeholder={t("contacts.last_name")} class={inputClass} />
      <input name="email" type="email" placeholder={t("contacts.email")} class={inputClass} />
      <input name="job_title" placeholder={t("contacts.job_title")} class={inputClass} />
    </div>
    <div class="flex gap-2">
      <button class="rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90">
        {t("common.save")}
      </button>
      <button type="button" class="rounded-lg border border-neutral-300 px-3 py-1.5 text-xs"
        onclick={() => (showAdd = false)}>{t("common.cancel")}</button>
    </div>
  </form>
{:else}
  <button type="button"
    class="mt-3 rounded-lg border border-dashed border-neutral-300 px-3 py-1.5 text-xs text-neutral-500 hover:border-brand hover:text-brand"
    onclick={() => (showAdd = true)}>
    ＋ {t("contacts.new")}
  </button>
{/if}
