<script lang="ts">
  /**
   * Password field with a show/hide (eye) toggle (#235). Owns the input so the button can
   * sit inside it; the toggle is a real button (focusable, aria-pressed) that never submits
   * the surrounding form. The `name` stays on the native input, so form posts and
   * `input[name=…]` test selectors are unchanged.
   */
  import { Eye, EyeOff } from "@lucide/svelte";
  import type { HTMLInputAttributes } from "svelte/elements";

  import { t } from "$lib/core/i18n";

  let {
    name,
    id = name,
    value = $bindable(""),
    required = false,
    minlength,
    autocomplete = "current-password",
    placeholder,
    class: klass = "",
  }: {
    name: string;
    id?: string;
    value?: string;
    required?: boolean;
    minlength?: number;
    autocomplete?: HTMLInputAttributes["autocomplete"];
    placeholder?: string;
    /** Wrapper classes — width/layout only (e.g. `w-56`); the input itself fills it. */
    class?: string;
  } = $props();

  let visible = $state(false);
</script>

<div class="relative {klass}">
  <input
    {id}
    {name}
    type={visible ? "text" : "password"}
    {required}
    {minlength}
    {autocomplete}
    {placeholder}
    bind:value
    class="w-full rounded-lg border border-border py-2 pl-3 pr-10 text-sm text-text outline-none focus:border-brand focus:ring-1 focus:ring-brand"
  />
  <button
    type="button"
    class="absolute inset-y-0 right-0 flex w-10 items-center justify-center rounded-r-lg text-text-muted hover:text-text"
    aria-label={t("common.show_password")}
    aria-pressed={visible}
    onclick={() => (visible = !visible)}
  >
    {#if visible}
      <EyeOff size={16} />
    {:else}
      <Eye size={16} />
    {/if}
  </button>
</div>
