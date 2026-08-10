<script lang="ts">
  /**
   * An image an org uploads: shown, replaced and removed — never spelled out as a URL.
   *
   * Huisstijl used to render each asset as a file input stacked on a plain text field holding the
   * stored address, so uploading a logo wrote `/api/v1/files/<uuid>/public` into a box the admin
   * then stared at. That string is an implementation detail of where the file went; it answers no
   * question anyone has and it invites editing something that must not be edited by hand. What the
   * admin wants to know is *which image is set*, so the answer is the image.
   *
   * The URL field still exists, because pointing at an already-hosted asset is a real (if rare)
   * need — it is folded behind a disclosure and left closed unless the current value is an external
   * address the field would otherwise be unable to show as "an upload".
   *
   * The component posts exactly what the old markup did: `name` carries the URL (empty clears it),
   * `fileName` carries an optional upload that the server action prefers over the URL. So no form
   * action changes shape.
   */
  import { ImageOff, Trash2, Upload } from "@lucide/svelte";

  import { t } from "$lib/core/i18n";
  import { filedrop } from "$lib/core/ui/filedrop";

  let {
    id,
    name,
    fileName,
    label,
    value = "",
    accept,
    help,
  }: {
    id: string;
    /** Form field carrying the stored URL. Empty string clears the asset. */
    name: string;
    /** Form field carrying a newly chosen file; the action uploads it and wins over `name`. */
    fileName: string;
    label: string;
    value?: string | null;
    accept: string;
    help?: string;
  } = $props();

  // What a form reset falls back to (docs/UX.md): the value this field was born with. Without it
  // `reset()` restores the empty `value` attribute a Svelte-bound input never sets, and Svelte
  // writes that emptiness back into state — the save would visibly wipe the logo.
  const savedValue = value ?? "";

  let url = $state(value ?? "");
  let picked = $state<{ name: string; preview: string } | null>(null);
  let fileInput = $state<HTMLInputElement | null>(null);
  // Open only when there is an address the preview cannot stand in for — an upload is shown as
  // itself, so its URL is noise.
  let showUrl = $state(!!value && !value.startsWith("/api/v1/files/"));

  const preview = $derived(picked?.preview ?? (url || null));
  const hasImage = $derived(!!preview);
  // An address can rot — an external host that moved, an asset deleted behind our back. Falling
  // back to the placeholder says "nothing to show" instead of leaving a browser's broken-image
  // glyph sitting in the card, which reads as our bug rather than the picture's.
  let broken = $state(false);
  let dropError = $state<string | null>(null);

  function onPick(event: Event) {
    dropError = null;
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (picked) URL.revokeObjectURL(picked.preview);
    picked = file ? { name: file.name, preview: URL.createObjectURL(file) } : null;
    broken = false;
  }

  function clear() {
    if (picked) URL.revokeObjectURL(picked.preview);
    picked = null;
    if (fileInput) fileInput.value = "";
    url = "";
    broken = false;
  }
</script>

<div>
  <span class="mb-1 block text-sm font-medium text-text">{label}</span>
  <!-- The whole field is the drop target, thumbnail included: that picture is what the admin is
       aiming at when they drag a new logo out of a folder. -->
  <div
    class="flex items-start gap-3"
    use:filedrop={{ input: () => fileInput, onerror: (key) => (dropError = key) }}
  >
    <div
      class="flex h-16 w-16 shrink-0 items-center justify-center overflow-hidden rounded-lg border border-border bg-surface"
    >
      {#if hasImage && !broken}
        <img
          src={preview}
          alt=""
          onerror={() => (broken = true)}
          class="max-h-full max-w-full object-contain"
        />
      {:else}
        <ImageOff size={18} class="text-text-muted" aria-hidden="true" />
      {/if}
    </div>
    <div class="min-w-0 flex-1">
      <div class="flex flex-wrap items-center gap-2">
        <!-- The input stays `sr-only` rather than `hidden`: a display:none control is not
             focusable, so a keyboard user could never reach the upload at all. -->
        <label
          class="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-brand focus-within:border-brand focus-within:ring-1 focus-within:ring-brand"
        >
          <Upload size={14} aria-hidden="true" />
          {hasImage ? t("common.replace_file") : t("common.choose_file")}
          <input
            {id}
            bind:this={fileInput}
            type="file"
            name={fileName}
            {accept}
            onchange={onPick}
            class="sr-only"
          />
        </label>
        {#if hasImage}
          <button
            type="button"
            onclick={clear}
            class="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-sm text-text hover:border-red-400 hover:text-red-500"
          >
            <Trash2 size={14} aria-hidden="true" />
            {t("common.remove")}
          </button>
        {/if}
        <span class="text-xs text-text-muted">{t("common.drop_hint")}</span>
      </div>
      {#if picked}
        <p class="mt-1 truncate text-xs text-text-muted" title={picked.name}>{picked.name}</p>
      {/if}
      {#if dropError}
        <p class="mt-1 text-xs text-red-600 dark:text-red-400">{t(dropError)}</p>
      {/if}
    </div>
  </div>

  {#if showUrl}
    <label for="{id}-url" class="mb-1 mt-3 block text-xs text-text-muted">
      {t("common.image_url")}
    </label>
    <input
      id="{id}-url"
      {name}
      bind:value={url}
      defaultValue={savedValue}
      placeholder="https://…"
      class="w-full rounded-lg border border-border px-3 py-2 text-sm outline-none focus:border-brand focus:ring-1 focus:ring-brand"
    />
  {:else}
    <input type="hidden" {name} bind:value={url} defaultValue={savedValue} />
    <button
      type="button"
      onclick={() => (showUrl = true)}
      class="mt-2 text-xs text-text-muted underline-offset-2 hover:text-text hover:underline"
    >
      {t("common.use_image_url")}
    </button>
  {/if}

  {#if help}
    <p class="mt-1 text-xs text-text-muted">{help}</p>
  {/if}
</div>
