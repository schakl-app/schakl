<script lang="ts">
  /**
   * Microphone toggle for a dictated text field (#246).
   *
   * Recording state never rides colour alone (docs/UX.md): `aria-pressed` changes, the label
   * changes, and an elapsed counter appears beside it. The control is only rendered where it
   * can actually work — the caller checks `recordingSupported()` after mount, never a user
   * agent — because a typed field sits right next to it as the fallback.
   */
  import { Mic, Square } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import Button from "$lib/core/ui/Button.svelte";

  import type { Recorder } from "./recorder.svelte";
  import { formatClock } from "./transcribe";

  let {
    recorder,
    onstart,
    onstop,
    disabled = false,
  }: {
    recorder: Recorder;
    onstart: () => void;
    onstop: () => void;
    disabled?: boolean;
  } = $props();

  const recording = $derived(recorder.state === "recording");
  // The cap is printed beside the counter ("0:42 / 5:00") rather than discovered when the
  // recording stops on its own: a limit the user can see is a limit, one they cannot is a fault.
  const clock = $derived(
    `${formatClock(recorder.elapsed)} / ${formatClock(Math.round(recorder.maxMs / 1000))}`,
  );
</script>

<Button
  type="button"
  variant="secondary"
  loading={recorder.state === "working"}
  {disabled}
  aria-pressed={recording}
  aria-label={recording ? t("voice.stop") : t("voice.start")}
  title={recording ? t("voice.stop") : t("voice.start")}
  onclick={() => (recording ? onstop() : onstart())}
>
  {#if recorder.state !== "working"}
    {#if recording}
      <Square size={14} class="fill-current text-red-600 dark:text-red-400" />
    {:else}
      <Mic size={14} />
    {/if}
  {/if}
  {#if recording}
    <span class="tabular-nums">{clock}</span>
  {/if}
</Button>
