<script lang="ts">
  /**
   * Download the minutes: tick the chapters, get the PDF — and the transcript as a file of its
   * own. The chapters start ticked the way Instellingen → Vergaderingen says, minus what this
   * meeting has nothing for (`document_sections` on the row); the transcript is off unless the
   * person ticks it, because it is the longest thing on the record and the one a reader of the
   * minutes least often wants on paper.
   *
   * Both downloads are navigations (`<a href>`), never fetches: a download is a navigation
   * (#307), and the browser's own download UI is the progress indicator.
   */
  import Download from "@lucide/svelte/icons/download";
  import ExternalLink from "@lucide/svelte/icons/external-link";

  import { t } from "$lib/core/i18n";
  import Modal from "$lib/core/ui/Modal.svelte";

  const ALL_SECTIONS = [
    "participants",
    "summary",
    "topics",
    "decisions",
    "action_items",
    "open_questions",
    "evidence",
    "transcript",
  ] as const;
  const FORMATS = ["txt", "md", "srt", "vtt"] as const;

  let {
    open = $bindable(false),
    meetingId,
    defaults,
    hasTranscript,
  }: {
    open?: boolean;
    meetingId: string;
    /** The chapters ticked when the dialog opens. */
    defaults: string[];
    hasTranscript: boolean;
  } = $props();

  let ticked = $state<string[]>([]);
  let seededFor = $state<string | null>(null);
  $effect(() => {
    // Re-seed each time the dialog opens: what the person unticked last time is not a setting.
    if (open && seededFor !== `${meetingId}:${open}`) {
      ticked = [...defaults];
      seededFor = `${meetingId}:${open}`;
    }
    if (!open) seededFor = null;
  });

  function toggle(key: string) {
    ticked = ticked.includes(key) ? ticked.filter((k) => k !== key) : [...ticked, key];
  }
  const query = $derived(encodeURIComponent(ticked.join(",")));
  const pdfHref = $derived(`/meetings/${meetingId}/pdf?sections=${query}`);
  const previewHref = $derived(`/meetings/${meetingId}/preview?sections=${query}`);
</script>

<Modal bind:open title={t("meetings.export.title")}>
  <p class="mb-3 text-sm text-text-muted">{t("meetings.export.hint")}</p>
  <ul class="divide-y divide-border rounded-lg border border-border">
    {#each ALL_SECTIONS as key (key)}
      {@const disabled = key === "transcript" && !hasTranscript}
      <li class="px-3 py-2">
        <label class="flex items-center gap-2 text-sm text-text {disabled ? 'opacity-50' : ''}">
          <input
            type="checkbox"
            checked={ticked.includes(key)}
            onchange={() => toggle(key)}
            {disabled}
            class="rounded border-border"
          />
          <span>{t(`meetings.doc.section.${key}`)}</span>
        </label>
      </li>
    {/each}
  </ul>
  {#if ticked.length === 0}
    <p class="mt-2 text-xs text-amber-700 dark:text-amber-400">
      {t("meetings.export.none_selected")}
    </p>
  {/if}
  <div class="mt-4 flex flex-wrap items-center gap-2">
    <a
      href={ticked.length ? pdfHref : undefined}
      class="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-2 text-sm font-medium text-white hover:bg-brand/90 {ticked.length
        ? ''
        : 'pointer-events-none opacity-50'}"
      data-sveltekit-reload
      aria-disabled={ticked.length === 0}
    >
      <Download size={15} />
      {t("meetings.export.pdf")}
    </a>
    <a
      href={ticked.length ? previewHref : undefined}
      target="_blank"
      rel="noopener"
      class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm text-text hover:bg-surface {ticked.length
        ? ''
        : 'pointer-events-none opacity-50'}"
      aria-disabled={ticked.length === 0}
    >
      <ExternalLink size={15} />
      {t("meetings.export.preview")}
    </a>
  </div>

  {#if hasTranscript}
    <div class="mt-6 border-t border-border pt-4">
      <h3 class="text-sm font-semibold text-text">{t("meetings.export.transcript_heading")}</h3>
      <p class="mb-2 text-xs text-text-muted">{t("meetings.export.transcript_hint")}</p>
      <div class="flex flex-wrap gap-2">
        {#each FORMATS as format (format)}
          <a
            href={`/meetings/${meetingId}/transcript?format=${format}`}
            class="inline-flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1.5 text-xs text-text hover:bg-surface"
            data-sveltekit-reload
          >
            <Download size={13} />
            {t(`meetings.export.format_${format}`)}
          </a>
        {/each}
      </div>
    </div>
  {/if}
</Modal>
