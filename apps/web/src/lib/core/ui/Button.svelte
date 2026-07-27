<script lang="ts">
  /**
   * The shared button (#242). The house styles live here instead of being re-typed per
   * call site — and, the reason it exists, so does the in-flight state: pass `loading`
   * while the form action is under way and the button disables (no double submit) and
   * shows the shared `Spinner` beside its label. Convention in docs/UX.md → "Loading /
   * in-flight state". Inside a form the native default type is submit, as with a bare
   * `<button>`; pass `type="button"` for cancel/close actions.
   */
  import type { Snippet } from "svelte";
  import type { HTMLButtonAttributes } from "svelte/elements";

  import Spinner from "./Spinner.svelte";

  let {
    variant = "primary",
    size = "md",
    loading = false,
    disabled = false,
    class: klass = "",
    children,
    ...rest
  }: {
    variant?: "primary" | "secondary" | "success" | "danger" | "danger-outline";
    size?: "md" | "sm" | "xs";
    /** True while the request is in flight: disables the button and shows the spinner. */
    loading?: boolean;
    class?: string;
    children: Snippet;
  } & HTMLButtonAttributes = $props();

  const variants = {
    primary: "bg-brand font-medium text-white hover:opacity-90",
    secondary: "border border-border text-text hover:border-brand",
    success: "bg-green-600 font-medium text-white hover:opacity-90",
    danger: "bg-red-600 font-medium text-white hover:opacity-90",
    "danger-outline": "border border-border text-text hover:border-red-400 hover:text-red-500",
  };
  const sizes = {
    md: "px-4 py-2 text-sm",
    sm: "px-3 py-1.5 text-sm",
    xs: "px-2 py-1 text-xs",
  };
</script>

<button
  {...rest}
  disabled={disabled || loading}
  class="inline-flex items-center justify-center gap-2 rounded-lg disabled:opacity-50 {variants[
    variant
  ]} {sizes[size]} {klass}"
>
  {#if loading}
    <Spinner size={14} />
  {/if}
  {@render children()}
</button>
