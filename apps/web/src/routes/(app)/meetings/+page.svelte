<script lang="ts">
  /**
   * The meetings register: every recorded meeting, newest first — its client, when, how long,
   * where it is in its lifecycle. Ends in the shared pager (CLAUDE.md §9).
   */
  import Mic from "@lucide/svelte/icons/mic";
  import Plus from "@lucide/svelte/icons/plus";

  import { invalidate } from "$app/navigation";
  import { page } from "$app/state";
  import { aiEnabled } from "$lib/core/ai";
  import FilterBar from "$lib/core/filters/FilterBar.svelte";
  import type { FilterDef } from "$lib/core/filters/types";
  import { fmtDateTime } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { pollWhile } from "$lib/core/poll.svelte";
  import { navLabel, pageTitle } from "$lib/core/title";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import Pagination from "$lib/core/ui/Pagination.svelte";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";
  import { fmtDuration, inFlight, kindLabel } from "$lib/modules/meetings/format";
  import MeetingStatusPill from "$lib/modules/meetings/MeetingStatusPill.svelte";

  let { data } = $props();

  const meetings = $derived(data.meetings);
  // The recorder needs a provider that can transcribe *and* the feature on; a control that
  // could only refuse is not drawn (off means invisible, #126).
  const canRecord = $derived(data.canWrite && aiEnabled(page.data.user, "meeting_assist"));

  // A worker is still holding one of these rows: re-read the list until it is not.
  pollWhile(
    () => meetings.some((m) => inFlight(m.status)),
    () => invalidate("meetings:list"),
    6000,
  );

  const companyPicker = $derived(
    splitCompanyOptions(data.companies, { selectedId: data.filters.company_id }),
  );
  const filterDefs: FilterDef[] = $derived([
    { kind: "search", key: "q", placeholder: t("meetings.list.search_placeholder") },
    {
      kind: "select",
      key: "company",
      placeholder: t("meetings.list.all_clients"),
      options: companyPicker.live,
      archived: companyPicker.retired,
      archivedLabel: companyArchivedLabel(),
    },
    {
      kind: "pills",
      key: "status",
      options: [
        { value: "ready", label: t("meetings.status.ready") },
        { value: "failed", label: t("meetings.status.failed") },
      ],
    },
  ]);
</script>

<svelte:head>
  <title>{pageTitle(navLabel("meetings", t("nav.meetings")))}</title>
</svelte:head>

<PageHeader title={navLabel("meetings", t("nav.meetings"))}>
  {#snippet subtitle()}{t("meetings.list.subtitle")}{/snippet}
  {#snippet actions()}
    {#if canRecord}
      <a
        href="/meetings/new"
        class="inline-flex items-center gap-1.5 rounded-lg bg-brand px-4 py-2 text-sm font-medium text-white hover:opacity-90"
        data-sveltekit-preload-data="hover"
      >
        <Plus size={15} />
        {t("meetings.list.new")}
      </a>
    {/if}
  {/snippet}
</PageHeader>

<FilterBar filters={filterDefs} idPrefix="meeting-filter" />

{#if meetings.length === 0}
  <div
    class="rounded-xl border border-dashed border-border bg-surface-raised px-6 py-12 text-center"
  >
    <Mic size={28} class="mx-auto mb-3 text-text-muted" />
    <p class="text-sm text-text-muted">{t("meetings.list.empty")}</p>
    {#if canRecord}
      <p class="mx-auto mt-2 max-w-md text-sm text-text-muted">{t("meetings.list.empty_hint")}</p>
    {/if}
  </div>
{:else}
  <div class="overflow-x-auto rounded-xl border border-border bg-surface-raised">
    <table class="w-full text-sm">
      <thead
        class="border-b border-border text-left text-xs uppercase tracking-wide text-text-muted"
      >
        <tr>
          <th class="px-4 py-3 font-medium">{t("meetings.list.title")}</th>
          <th class="px-4 py-3 font-medium">{t("meetings.list.client")}</th>
          <th class="px-4 py-3 font-medium">{t("meetings.list.when")}</th>
          <th class="px-4 py-3 font-medium">{t("meetings.list.duration")}</th>
          <th class="px-4 py-3 font-medium">{t("meetings.list.status")}</th>
        </tr>
      </thead>
      <tbody class="divide-y divide-border">
        {#each meetings as meeting (meeting.id)}
          <tr class="hover:bg-surface">
            <td class="px-4 py-3">
              <a
                href={`/meetings/${meeting.id}`}
                class="font-medium text-text hover:underline"
                data-sveltekit-preload-data="hover">{meeting.title}</a
              >
              <span class="mt-0.5 block text-xs text-text-muted">
                {kindLabel(meeting.kind)}{meeting.action_item_count
                  ? ` · ${t("meetings.panel.action_items", { count: String(meeting.action_item_count) })}`
                  : ""}
              </span>
            </td>
            <td class="px-4 py-3 text-text-muted">{meeting.company_name ?? "—"}</td>
            <td class="px-4 py-3 whitespace-nowrap text-text-muted"
              >{fmtDateTime(meeting.occurred_at)}</td
            >
            <td class="px-4 py-3 whitespace-nowrap text-text-muted"
              >{fmtDuration(meeting.duration_seconds) || "—"}</td
            >
            <td class="px-4 py-3"><MeetingStatusPill status={meeting.status} /></td>
          </tr>
        {/each}
      </tbody>
    </table>
  </div>
  <Pagination total={data.total} page={data.paging.page} limit={data.paging.limit} />
{/if}
