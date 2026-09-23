<script lang="ts">
  /**
   * Record a meeting: the microphone, a browser tab's call, or a file somebody already has.
   *
   * Three rules hold this screen up. **Nobody is recorded without being told**: the API refuses
   * to open a recording until the person states the others know, so the statement is the one
   * required control, said in the words the AVG asks for (why, who reads it, how long it is
   * kept). **The recording is uploaded while it runs**, one piece a minute, and the screen says
   * how much is safe — "opgeslagen tot 12:00" — because a counter alone is a promise the tab
   * cannot keep. And **every way out releases the microphone** (docs/VOICE.md): leaving the page
   * aborts the capture and deletes the half-made row, so a forgotten recorder is not a bill.
   *
   * Two more since the roster shipped. **Who is at the table is asked here**, before the
   * recording, because that is when the person knows it — and it is what the minutes ground
   * "who took this on" in. And **a speech model that does not label speakers is said by name
   * before a minute is recorded** (`speech_diarize`): a transcript without speakers looks exactly
   * like one that failed to find any, so the honest place to say it is above the record button.
   *
   * And one about leaving. While a recording runs, this page is the recorder: a reload, a closed
   * tab or a mis-clicked nav link ends the capture and — through `leave()` — deletes the row, so
   * both are asked about first (`beforeunload` for the browser's own exits, `beforeNavigate` for
   * the app's). A piece that is not landing is *not* a reason to stop: the upload retries in the
   * background for minutes (`upload.ts`), and the screen says so in amber, because a redeploy or
   * a dropped connection ends on its own and the recording must not.
   */
  import ArrowLeft from "@lucide/svelte/icons/arrow-left";
  import Mic from "@lucide/svelte/icons/mic";
  import MonitorUp from "@lucide/svelte/icons/monitor-up";
  import Square from "@lucide/svelte/icons/square";
  import Upload from "@lucide/svelte/icons/upload";
  import { onMount } from "svelte";

  import { beforeNavigate, goto } from "$app/navigation";
  import { page } from "$app/state";
  import { aiEnabled } from "$lib/core/ai";
  import { fmtNumericDate } from "$lib/core/format";
  import { t } from "$lib/core/i18n";
  import { can } from "$lib/core/permissions";
  import { orgToday } from "$lib/core/today";
  import Button from "$lib/core/ui/Button.svelte";
  import Combobox from "$lib/core/ui/Combobox.svelte";
  import PageHeader from "$lib/core/ui/PageHeader.svelte";
  import { formatClock } from "$lib/core/voice";
  import { companyArchivedLabel, splitCompanyOptions } from "$lib/modules/companies/picker";
  import {
    MeetingRecorder,
    fileDuration,
    recordingSupported,
    supportsTabAudio,
    uploadFile,
    type CaptureSource,
  } from "$lib/modules/meetings/recorder.svelte";
  import { kindLabel } from "$lib/modules/meetings/format";
  import ParticipantsEditor, {
    type Participant,
  } from "$lib/modules/meetings/ParticipantsEditor.svelte";
  import { projectArchivedLabel, splitProjectOptions } from "$lib/modules/projects/picker";

  let { data, form } = $props();

  type Source = CaptureSource | "upload";

  const recorder = new MeetingRecorder();
  let title = $state("");
  let kind = $state<"physical" | "online">("physical");
  let source = $state<Source>("microphone");
  // The chip that opened this screen chose the client; the picker owns it from here on.
  // svelte-ignore state_referenced_locally
  let companyId = $state(data.companyId ?? "");
  let projectId = $state("");
  let informed = $state(false);
  let participants = $state<Participant[]>([]);
  let file = $state<File | null>(null);
  let error = $state<string | null>(null);
  let fieldError = $state<string | null>(null);
  let micSupported = $state(false);
  let tabSupported = $state(false);
  let meetingId = $state<string | null>(null);
  /** The upload's progress: pieces done of total. */
  let uploadDone = $state(0);
  let uploadTotal = $state(0);
  let phase = $state<"form" | "recording" | "uploading" | "finishing">("form");

  onMount(() => {
    micSupported = recordingSupported();
    tabSupported = supportsTabAudio();
    if (!micSupported) source = "upload";
    if (!title) title = t("meetings.record.default_title", { date: fmtNumericDate(orgToday()) });
    return () => void leave();
  });

  /** Is leaving now the end of a recording? (`finish` hands the row over first.) */
  const guarded = $derived(recorder.active || phase === "uploading");

  // The browser's own exits — reload, close, a typed URL — get the native "leave site?" prompt.
  $effect(() => {
    if (!guarded) return;
    const ask = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      // Older browsers read a returnValue; modern ones show their own sentence regardless.
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", ask);
    return () => window.removeEventListener("beforeunload", ask);
  });

  // The app's own exits — a nav link, the back button — ask in our words and can be refused.
  beforeNavigate((navigation) => {
    if (!guarded || navigation.type === "leave") return;
    if (!confirm(t("meetings.record.leave_confirm"))) navigation.cancel();
  });

  const enabled = $derived(
    aiEnabled(page.data.user, "meeting_assist") && aiEnabled(page.data.user, "speech"),
  );
  const diarizes = $derived(aiEnabled(page.data.user, "speech_diarize"));
  const companyPicker = $derived(splitCompanyOptions(data.companies, { selectedId: companyId }));
  const companyName = $derived(data.companies.find((c) => c.id === companyId)?.name ?? null);
  const projectPicker = $derived(
    splitProjectOptions(data.projects, { selectedId: projectId, companyId }),
  );
  const retentionDays = $derived(data.retentionDays);
  // Off means the checkbox is not drawn and the API does not refuse (the org's policy).
  const consentRequired = $derived(data.consentRequired);

  /** Leaving mid-recording: stop the capture and drop the half-made row. */
  async function leave() {
    if (recorder.active) recorder.abort();
    if (meetingId && phase !== "finishing") {
      await fetch(`/api/v1/meetings/${meetingId}`, { method: "DELETE" }).catch(() => undefined);
    }
  }

  async function createMeeting(): Promise<string | null> {
    error = null;
    fieldError = null;
    const res = await fetch("/api/v1/meetings", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        title: title.trim(),
        kind,
        source,
        company_id: companyId || null,
        project_id: projectId || null,
        language: page.data.locale ?? "nl",
        participants_informed: informed,
        participants,
      }),
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => null);
      const fields = payload?.error?.fields ?? {};
      fieldError = fields.participants_informed ?? fields.title ?? null;
      error = fieldError ? null : (payload?.error?.message ?? "errors.server");
      return null;
    }
    const body = await res.json();
    meetingId = body.id;
    return body.id;
  }

  async function finish(durationSeconds: number | null) {
    if (!meetingId) return;
    phase = "finishing";
    const res = await fetch(`/api/v1/meetings/${meetingId}/finish`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ duration_seconds: durationSeconds }),
    });
    if (!res.ok) {
      const payload = await res.json().catch(() => null);
      error = payload?.error?.message ?? "errors.server";
      phase = "form";
      return;
    }
    const id = meetingId;
    meetingId = null; // handed over: `leave()` must not delete it
    await goto(`/meetings/${id}`);
  }

  /**
   * The microphone first, the row second, the capture third.
   *
   * The order is the point. Creating the row first and asking for the microphone second is how
   * a colleague's three-hour meeting came to exist on the server as a row stamped `recording`
   * with no audio behind it and no way to end it: the capture never really began (a permission
   * prompt a phone froze while it was up), and everything downstream — the screen, the poller,
   * the reaper — went on describing a recording that had never started. `arm()` makes the
   * capture the precondition rather than the consequence, so a recording that cannot start
   * leaves nothing to clean up.
   */
  async function startRecording() {
    if (!title.trim()) {
      fieldError = "errors.required";
      return;
    }
    error = null;
    fieldError = null;
    if (!(await recorder.arm(source as CaptureSource))) {
      if (recorder.error) error = recorder.error;
      return;
    }
    const id = await createMeeting();
    if (!id) {
      // The capture is live and there is nowhere to put it: let the microphone go, or the
      // recorder holds it (and the phone shows it holding it) for a recording nobody has.
      recorder.abort();
      return;
    }
    phase = "recording";
    const ok = await recorder.begin(id);
    if (!ok) {
      if (recorder.error) error = recorder.error;
      if (phase === "recording" && !recorder.uploadError) {
        await fetch(`/api/v1/meetings/${id}`, { method: "DELETE" }).catch(() => undefined);
        meetingId = null;
        phase = "form";
      }
      return;
    }
    await finish(recorder.elapsed);
  }

  async function startUpload() {
    if (!file) return;
    if (!title.trim()) {
      fieldError = "errors.required";
      return;
    }
    const id = await createMeeting();
    if (!id) return;
    phase = "uploading";
    const duration = await fileDuration(file);
    const failed = await uploadFile(id, file, (done, total) => {
      uploadDone = done;
      uploadTotal = total;
    });
    if (failed) {
      error = failed;
      phase = "form";
      return;
    }
    await finish(duration);
  }

  function onfile(event: Event) {
    const input = event.currentTarget as HTMLInputElement;
    file = input.files?.[0] ?? null;
    if (
      file &&
      title.trim() === t("meetings.record.default_title", { date: fmtNumericDate(orgToday()) })
    ) {
      title = file.name.replace(/\.[a-z0-9]+$/i, "");
    }
  }

  const inputClass =
    "w-full rounded-lg border border-border bg-surface-raised px-3 py-2 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand";
  const sourceOptions = $derived(
    [
      micSupported ? ("microphone" as const) : null,
      tabSupported ? ("tab" as const) : null,
      "upload" as const,
    ].filter((s): s is Source => s !== null),
  );
</script>

<svelte:head>
  <title>{t("meetings.record.title")}</title>
</svelte:head>

<a
  href="/meetings"
  class="mb-4 inline-flex items-center gap-1.5 text-sm text-text-muted hover:text-text"
>
  <ArrowLeft size={15} />
  {t("nav.meetings")}
</a>

<PageHeader title={t("meetings.record.title")}>
  {#snippet subtitle()}{t("meetings.record.subtitle")}{/snippet}
</PageHeader>

{#if enabled && !diarizes && phase === "form"}
  <p
    class="mb-4 rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200"
  >
    {t("meetings.record.no_diarize")}
    {#if can(page.data.user, "ai.settings.manage")}
      <a href="/settings/ai" class="underline">{t("meetings.record.no_diarize_link")}</a>
    {/if}
  </p>
{/if}

{#if !enabled}
  <p
    class="rounded-lg bg-amber-50 px-4 py-3 text-sm text-amber-800 dark:bg-amber-950 dark:text-amber-200"
  >
    {t("meetings.record.unavailable")}
  </p>
{:else if phase === "recording" || phase === "finishing"}
  <!-- The live surface: one number, one sentence about what is safe, one button. -->
  <div class="rounded-xl border border-border bg-surface-raised p-6 text-center">
    <p class="text-sm text-text-muted">{title}</p>
    <p class="mt-2 text-4xl font-semibold tabular-nums text-text" aria-live="off">
      {formatClock(recorder.elapsed)}
    </p>
    <p class="mt-2 text-sm text-text-muted" aria-live="polite">
      {#if phase === "finishing"}
        {t("meetings.record.finishing")}
      {:else if recorder.uploadError}
        <span class="text-red-700 dark:text-red-300">{t(recorder.uploadError)}</span>
      {:else if recorder.retrying}
        <!-- A piece is being retried: the recording goes on and nothing is lost yet, so amber,
             with what *is* safe beside it. -->
        <span class="text-amber-800 dark:text-amber-200">
          {t("meetings.record.reconnecting", { clock: formatClock(recorder.savedSeconds) })}
        </span>
      {:else if recorder.uploaded === 0 && recorder.pending === 0}
        {t("meetings.record.saving_soon")}
      {:else}
        {t("meetings.record.saved_until", { clock: formatClock(recorder.savedSeconds) })}
      {/if}
    </p>
    {#if recorder.captureLost}
      <!-- The capture ended without anybody stopping it. What landed is being handed over; the
           sentence is here because a recording that stops by itself must never look like one
           that was stopped. -->
      <p class="mt-2 text-sm text-amber-800 dark:text-amber-200" role="alert">
        {t("meetings.record.capture_lost")}
      </p>
    {/if}
    <div class="mt-6 flex flex-wrap items-center justify-center gap-3">
      {#if recorder.uploadError}
        <Button type="button" variant="secondary" onclick={() => recorder.retryFailed()}>
          {t("meetings.record.retry_upload")}
        </Button>
      {/if}
      <Button
        type="button"
        variant="danger"
        loading={phase === "finishing" || recorder.state === "stopping"}
        disabled={phase === "finishing"}
        onclick={() => recorder.stop()}
        aria-label={t("meetings.record.stop")}
      >
        <Square size={14} class="fill-current" />
        {t("meetings.record.stop")}
      </Button>
    </div>
    {#if recorder.stoppedAtLimit}
      <p class="mt-3 text-sm text-text-muted">{t("meetings.record.limit_reached")}</p>
    {/if}
    <!-- Said while it runs, not in the help: a locked phone freezes the tab and the recording
         with it, and this is the one thing the person can do about it. -->
    <p class="mt-3 text-xs text-text-muted">{t("meetings.record.keep_screen_on")}</p>
  </div>
{:else if phase === "uploading"}
  <div class="rounded-xl border border-border bg-surface-raised p-6 text-center">
    <p class="text-sm text-text" aria-live="polite">
      {t("meetings.record.uploading", { done: String(uploadDone), total: String(uploadTotal) })}
    </p>
  </div>
{:else}
  <form
    class="max-w-2xl space-y-5"
    onsubmit={(e) => {
      e.preventDefault();
      void (source === "upload" ? startUpload() : startRecording());
    }}
  >
    <div>
      <label for="meeting-title" class="mb-1 block text-sm font-medium text-text"
        >{t("meetings.field.title")}</label
      >
      <input id="meeting-title" name="title" bind:value={title} class={inputClass} required />
    </div>

    <div class="grid gap-4 sm:grid-cols-2">
      <div>
        <label for="meeting-company" class="mb-1 block text-sm font-medium text-text"
          >{t("meetings.field.client")}</label
        >
        <Combobox
          id="meeting-company"
          name="company_id"
          bind:value={companyId}
          items={companyPicker.live}
          archived={companyPicker.retired}
          archivedLabel={companyArchivedLabel()}
          placeholder={t("meetings.field.client_placeholder")}
          onselect={() => (projectId = "")}
        />
        <p class="mt-1 text-xs text-text-muted">{t("meetings.field.client_hint")}</p>
      </div>
      <div>
        <label for="meeting-project" class="mb-1 block text-sm font-medium text-text"
          >{t("meetings.field.project")}</label
        >
        <Combobox
          id="meeting-project"
          name="project_id"
          bind:value={projectId}
          items={projectPicker.live}
          archived={projectPicker.retired}
          archivedLabel={projectArchivedLabel()}
          placeholder={t("meetings.field.project_placeholder")}
        />
      </div>
    </div>

    <fieldset>
      <legend class="mb-1 text-sm font-medium text-text">{t("meetings.field.participants")}</legend>
      <p class="mb-2 text-xs text-text-muted">{t("meetings.field.participants_hint")}</p>
      <ParticipantsEditor
        bind:participants
        members={data.members}
        {companyId}
        {companyName}
        definitions={data.contactDefinitions}
        locale={data.locale}
        created={form?.inlineCreated ?? null}
        qcError={form?.qcError ?? null}
      />
    </fieldset>

    <fieldset>
      <legend class="mb-2 text-sm font-medium text-text">{t("meetings.field.kind")}</legend>
      <div class="flex flex-wrap gap-2">
        {#each ["physical", "online"] as const as option (option)}
          <label
            class="inline-flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm {kind ===
            option
              ? 'border-brand bg-brand/10 text-text'
              : 'border-border text-text-muted'}"
          >
            <input type="radio" name="kind" value={option} bind:group={kind} class="sr-only" />
            {kindLabel(option)}
          </label>
        {/each}
      </div>
    </fieldset>

    <fieldset>
      <legend class="mb-2 text-sm font-medium text-text">{t("meetings.field.source")}</legend>
      <div class="grid gap-2 sm:grid-cols-3">
        {#each sourceOptions as option (option)}
          <label
            class="flex cursor-pointer items-start gap-3 rounded-lg border p-3 text-sm {source ===
            option
              ? 'border-brand bg-brand/10'
              : 'border-border'}"
          >
            <input type="radio" name="source" value={option} bind:group={source} class="sr-only" />
            {#if option === "microphone"}
              <Mic size={18} class="mt-0.5 shrink-0 text-text-muted" />
            {:else if option === "tab"}
              <MonitorUp size={18} class="mt-0.5 shrink-0 text-text-muted" />
            {:else}
              <Upload size={18} class="mt-0.5 shrink-0 text-text-muted" />
            {/if}
            <span>
              <span class="block font-medium text-text">{t(`meetings.source.${option}`)}</span>
              <span class="block text-xs text-text-muted"
                >{t(`meetings.source.${option}_hint`)}</span
              >
            </span>
          </label>
        {/each}
      </div>
    </fieldset>

    {#if source === "upload"}
      <div>
        <label for="meeting-file" class="mb-1 block text-sm font-medium text-text"
          >{t("meetings.field.file")}</label
        >
        <input
          id="meeting-file"
          name="file"
          type="file"
          accept="audio/*,video/webm,.webm,.m4a,.mp3,.wav,.ogg"
          class="block w-full text-sm text-text file:mr-3 file:rounded-lg file:border file:border-border file:bg-surface file:px-3 file:py-1.5 file:text-sm"
          onchange={onfile}
        />
        <p class="mt-1 text-xs text-text-muted">{t("meetings.field.file_hint")}</p>
      </div>
    {/if}

    {#if consentRequired}
      <!-- The one required statement. Not a nicety: the API refuses without it. -->
      <label class="flex items-start gap-3 rounded-lg border border-border p-3 text-sm">
        <input
          type="checkbox"
          name="participants_informed"
          bind:checked={informed}
          class="mt-0.5 size-4 rounded border-border"
        />
        <span>
          <span class="block font-medium text-text">{t("meetings.record.informed")}</span>
          <span class="block text-xs text-text-muted"
            >{t("meetings.record.informed_hint", { days: String(retentionDays) })}</span
          >
        </span>
      </label>
    {:else}
      <p class="text-xs text-text-muted">
        {t("meetings.record.consent_off", { days: String(retentionDays) })}
      </p>
    {/if}

    {#if fieldError}
      <p class="text-sm text-red-700 dark:text-red-300" role="alert">{t(fieldError)}</p>
    {:else if error}
      <p class="text-sm text-red-700 dark:text-red-300" role="alert">{t(error)}</p>
    {/if}

    <div class="flex flex-wrap items-center gap-3">
      <Button type="submit" disabled={source === "upload" && !file}>
        {#if source === "upload"}
          <Upload size={15} />
          {t("meetings.record.upload")}
        {:else}
          <Mic size={15} />
          {t("meetings.record.start")}
        {/if}
      </Button>
      <a href="/meetings" class="text-sm text-text-muted hover:text-text">{t("common.cancel")}</a>
    </div>
  </form>
{/if}
