<script lang="ts">
  import { t } from "$lib/core/i18n";
  import { formatPhone } from "$lib/core/phone";
  import Markdown from "$lib/core/ui/Markdown.svelte";

  let { data }: { companyId: string; data: Record<string, unknown> } = $props();

  const website = $derived(data.website as string | null);
  const phone = $derived(data.phone as string | null);
  const invoiceEmail = $derived(data.invoice_email as string | null);
  const notes = $derived(data.notes as string | null);
  const custom = $derived((data.custom ?? {}) as Record<string, unknown>);
</script>

<dl class="grid grid-cols-1 gap-4 sm:grid-cols-2">
  <div>
    <dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">
      {t("companies.name")}
    </dt>
    <dd class="mt-1 text-sm text-neutral-900">{data.name}</dd>
  </div>

  <div>
    <dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">
      {t("companies.website")}
    </dt>
    <dd class="mt-1 text-sm">
      {#if website}
        <a class="text-brand underline" href={website} target="_blank" rel="noreferrer">
          {website}
        </a>
      {:else}
        <span class="text-neutral-400">—</span>
      {/if}
    </dd>
  </div>

  <div>
    <dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">
      {t("companies.phone")}
    </dt>
    <dd class="mt-1 text-sm">
      {#if phone}
        <a class="text-brand underline" href="tel:{phone}">{formatPhone(phone)}</a>
      {:else}
        <span class="text-neutral-400">—</span>
      {/if}
    </dd>
  </div>

  <div>
    <dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">
      {t("companies.invoice_email")}
    </dt>
    <dd class="mt-1 text-sm">
      {#if invoiceEmail}
        <a class="text-brand underline" href="mailto:{invoiceEmail}">{invoiceEmail}</a>
      {:else}
        <span class="text-neutral-400">—</span>
      {/if}
    </dd>
  </div>

  <div>
    <dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">
      {t("companies.billing_heading")}
    </dt>
    <dd class="mt-1 text-sm text-neutral-900">
      {#if data.address_line1 || data.city || data.vat_number || data.coc_number}
        {#if data.address_line1}<span class="block">{data.address_line1}</span>{/if}
        {#if data.address_line2}<span class="block">{data.address_line2}</span>{/if}
        {#if data.postal_code || data.city}
          <span class="block"
            >{[data.postal_code, data.city, data.country].filter(Boolean).join(" ")}</span
          >
        {/if}
        {#if data.vat_number}
          <span class="block text-neutral-500">{t("companies.vat_number")}: {data.vat_number}</span>
        {/if}
        {#if data.coc_number}
          <span class="block text-neutral-500">{t("companies.coc_number")}: {data.coc_number}</span>
        {/if}
      {:else}
        <span class="text-neutral-400">—</span>
      {/if}
    </dd>
  </div>

  <div class="sm:col-span-2">
    <dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">
      {t("companies.notes")}
    </dt>
    <dd class="mt-1 text-sm text-neutral-900">
      {#if notes}
        <Markdown value={notes} />
      {:else}
        —
      {/if}
    </dd>
  </div>

  {#if Object.keys(custom).length > 0}
    <div class="sm:col-span-2">
      <dt class="text-xs font-medium uppercase tracking-wide text-neutral-500">
        {t("companies.panel.custom")}
      </dt>
      <dd class="mt-1">
        <ul class="space-y-1 text-sm">
          {#each Object.entries(custom) as [key, value] (key)}
            <li>
              <span class="font-mono text-xs text-neutral-500">{key}</span>:
              <span class="text-neutral-900">{String(value)}</span>
            </li>
          {/each}
        </ul>
      </dd>
    </div>
  {/if}
</dl>
