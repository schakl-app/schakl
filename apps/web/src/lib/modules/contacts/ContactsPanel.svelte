<script lang="ts">
  /** Company-detail panel: the contacts attached to this company (CLAUDE.md §6). */
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
