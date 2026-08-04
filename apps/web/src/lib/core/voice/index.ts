/**
 * Dictated input (#246) — the browser records, the tenant's own speech provider transcribes.
 *
 * Barrel, mirroring `core/ai/`. The quick-add field on /time is the first host; the assistant
 * panel and the rich-text editor are the obvious later ones, which is why this lives in
 * `core/` rather than inside the time module.
 */
export { blobToBase64, stripDataUrl } from "./encode";
export { MAX_RECORD_MS, Recorder, micErrorKey, recordingSupported } from "./recorder.svelte";
export type { RecorderState } from "./recorder.svelte";
export { default as VoiceButton } from "./VoiceButton.svelte";
