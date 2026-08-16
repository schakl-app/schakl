/**
 * Dictated input (#246) — the browser records, the tenant's own speech provider transcribes.
 *
 * Barrel, mirroring `core/ai/`. The quick-add field on /time was the first host and the
 * dictated task (#382) the second — which is what this living in `core/` rather than in the
 * time module was for. The rich-text editor and the assistant panel are next.
 */
export { blobToBase64, stripDataUrl } from "./encode";
export {
  MAX_RECORD_MS,
  MAX_TASK_RECORD_MS,
  Recorder,
  micErrorKey,
  recordingSupported,
} from "./recorder.svelte";
export type { RecorderState } from "./recorder.svelte";
export { default as VoiceButton } from "./VoiceButton.svelte";
