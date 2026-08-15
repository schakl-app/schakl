<script lang="ts">
  /**
   * The zone's DNS records, fetched **on demand** (epic #278).
   *
   * Deliberately not part of the page load: reading records is a live Cloudflare call, and
   * putting it on the domain page's `load` would make every visit wait on an outside API for
   * a table most visits never open (docs/PERFORMANCE.md). So it hides behind one button and
   * comes back through the host page's `?/cfLoadDns` action.
   *
   * Export is the same shape: the API hands back a filename and a body, and the browser turns
   * that into a download without a second round trip.
   *
   * **Host contract:** `?/cfLoadDns`, `?/cfExportDns`, `?/cfSaveDnsRecord`,
   * `?/cfDeleteDnsRecord`.
   */
  import { enhance } from "$app/forms";
  import { page } from "$app/state";
  import { t } from "$lib/core/i18n";
  import { InFlight } from "$lib/core/submit.svelte";
  import Button from "$lib/core/ui/Button.svelte";
  import ConfirmDialog from "$lib/core/ui/ConfirmDialog.svelte";

  import type { DnsRecord, ZoneRecords } from "./types";

  let {
    zoneId,
    zoneName,
    canManage,
  }: { zoneId: string; zoneName: string; canManage: boolean } = $props();

  const busy = new InFlight();
  let open = $state(false);
  let editing = $state<DnsRecord | null>(null);
  let confirmDelete = $state(false);
  let deleteTarget = $state<DnsRecord | null>(null);

  // Latched, not derived: `page.form` is whatever the *last* action returned, so an export or a
  // delete would otherwise blank the table the previous action filled. Every action that changes
  // records returns the refreshed list, so this stays truthful without a second fetch.
  let records = $state<DnsRecord[]>([]);
  $effect(() => {
    const payload = page.form?.cfDns as ZoneRecords | undefined;
    if (payload && payload.zone_id === zoneId) records = payload.records ?? [];
  });

  const TYPES = ["A", "AAAA", "CNAME", "TXT", "MX", "NS", "SRV", "CAA"];
  const PROXIABLE = ["A", "AAAA", "CNAME"];

  const inputClass =
    "w-full min-w-0 rounded-lg border border-border px-2 py-1.5 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";

  // The export comes back as a body plus a filename; hand it to the browser as a file rather
  // than dumping a zone file into the page.
  let downloaded = $state<string | null>(null);
  $effect(() => {
    const payload = page.form?.cfExport as
      | { filename: string; content_type: string; content: string }
      | undefined;
    if (!payload || downloaded === payload.filename + payload.content.length) return;
    downloaded = payload.filename + payload.content.length;
    const blob = new Blob([payload.content], { type: payload.content_type });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = payload.filename;
    anchor.click();
    URL.revokeObjectURL(url);
  });
</script>

<section class="mt-5 border-t border-border pt-4">
  <div class="flex flex-wrap items-center justify-between gap-2">
    <h3 class="text-sm font-medium text-text">{t("cloudflare.dns.title")}</h3>
    <div class="flex flex-wrap items-center gap-2">
      <form
        method="POST"
        action="?/cfLoadDns"
        use:enhance={busy.wrap("load", () => async ({ update }) => {
          open = true;
          await update({ reset: false });
        })}
      >
        <input type="hidden" name="zone_id" value={zoneId} />
        <Button variant="secondary" size="xs" loading={busy.is("load")} disabled={busy.active}>
          {open ? t("cloudflare.dns.title") : t("cloudflare.dns.show")}
        </Button>
      </form>
      {#if open}
        <form method="POST" action="?/cfExportDns" use:enhance={busy.wrap("bind")}>
          <input type="hidden" name="zone_id" value={zoneId} />
          <input type="hidden" name="format" value="bind" />
          <Button variant="secondary" size="xs" loading={busy.is("bind")} disabled={busy.active}>
            {t("cloudflare.dns.export_bind")}
          </Button>
        </form>
        <form method="POST" action="?/cfExportDns" use:enhance={busy.wrap("csv")}>
          <input type="hidden" name="zone_id" value={zoneId} />
          <input type="hidden" name="format" value="csv" />
          <Button variant="secondary" size="xs" loading={busy.is("csv")} disabled={busy.active}>
            {t("cloudflare.dns.export_csv")}
          </Button>
        </form>
      {/if}
    </div>
  </div>

  {#if open}
    {#if records.length === 0}
      <p class="mt-2 text-sm text-text-muted">{t("cloudflare.dns.empty")}</p>
    {:else}
      <div class="mt-3 overflow-x-auto">
        <table class="w-full min-w-[36rem] text-sm">
          <thead class="text-left text-xs text-text-muted">
            <tr>
              <th class="py-1 pr-3 font-medium">{t("cloudflare.dns.type")}</th>
              <th class="py-1 pr-3 font-medium">{t("cloudflare.dns.name")}</th>
              <th class="py-1 pr-3 font-medium">{t("cloudflare.dns.content")}</th>
              <th class="py-1 pr-3 font-medium">{t("cloudflare.dns.proxied")}</th>
              {#if canManage}<th class="py-1"><span class="sr-only">…</span></th>{/if}
            </tr>
          </thead>
          <tbody>
            {#each records as record (record.id)}
              <tr class="border-t border-border align-top">
                <td class="py-1.5 pr-3 text-text">{record.type}</td>
                <td class="min-w-0 break-all py-1.5 pr-3 text-text">{record.name}</td>
                <td class="min-w-0 break-all py-1.5 pr-3 text-text">{record.content}</td>
                <td class="py-1.5 pr-3 text-text-muted">{record.proxied ? "✓" : "—"}</td>
                {#if canManage}
                  <td class="whitespace-nowrap py-1.5 text-right">
                    <Button
                      type="button"
                      variant="secondary"
                      size="xs"
                      onclick={() => (editing = editing?.id === record.id ? null : record)}
                    >
                      {t("cloudflare.dns.edit")}
                    </Button>
                    <Button
                      type="button"
                      variant="danger-outline"
                      size="xs"
                      onclick={() => {
                        deleteTarget = record;
                        confirmDelete = true;
                      }}
                    >
                      {t("common.delete")}
                    </Button>
                  </td>
                {/if}
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {/if}

    {#if canManage}
      <!-- One form, two jobs: it edits an existing record (keep what is typed) or adds a new
           one (empty it for the next). `busy.wrap` states which, per docs/UX.md. -->
      <form
        method="POST"
        action="?/cfSaveDnsRecord"
        use:enhance={busy.wrap("record", () => async ({ update }) => {
          await update({ reset: !editing });
          editing = null;
        })}
        class="mt-4 grid gap-2 sm:grid-cols-[6rem_1fr_1fr_auto]"
      >
        <input type="hidden" name="zone_id" value={zoneId} />
        <input type="hidden" name="record_id" value={editing?.id ?? ""} />
        <div class="min-w-0">
          <label class="sr-only" for="cf-dns-type">{t("cloudflare.dns.type")}</label>
          <select id="cf-dns-type" name="type" value={editing?.type ?? "A"} class={inputClass}>
            {#each TYPES as type (type)}
              <option value={type}>{type}</option>
            {/each}
          </select>
        </div>
        <div class="min-w-0">
          <label class="sr-only" for="cf-dns-name">{t("cloudflare.dns.name")}</label>
          <input
            id="cf-dns-name"
            name="name"
            value={editing?.name ?? ""}
            placeholder={zoneName}
            class={inputClass}
          />
        </div>
        <div class="min-w-0">
          <label class="sr-only" for="cf-dns-content">{t("cloudflare.dns.content")}</label>
          <input
            id="cf-dns-content"
            name="content"
            value={editing?.content ?? ""}
            placeholder={t("cloudflare.dns.content")}
            class={inputClass}
          />
        </div>
        <div class="flex items-center gap-2">
          <label class="flex items-center gap-1 whitespace-nowrap text-xs text-text">
            <input
              type="checkbox"
              name="proxied"
              checked={editing?.proxied ?? false}
              class="rounded border-border"
            />
            {t("cloudflare.dns.proxied")}
          </label>
          <Button type="submit" size="xs" loading={busy.is("record")} disabled={busy.active}>
            {editing ? t("common.save") : t("cloudflare.dns.add")}
          </Button>
        </div>
        <p class="text-xs text-text-muted sm:col-span-4">
          {t("cloudflare.dns.proxied")}: {PROXIABLE.join(", ")}
        </p>
      </form>
    {/if}
  {/if}
</section>

<ConfirmDialog
  bind:open={confirmDelete}
  title={t("common.delete")}
  message={t("cloudflare.dns.delete_confirm", {
    type: deleteTarget?.type ?? "",
    name: deleteTarget?.name ?? "",
  })}
  action="?/cfDeleteDnsRecord"
  fields={{ zone_id: zoneId, record_id: deleteTarget?.id ?? "" }}
/>
