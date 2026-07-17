<script lang="ts">
  /**
   * A checkbox for posting forms whose mark is **component state, never one-way DOM**
   * (docs/UX.md): an input rendered with a bare `checked={…}` can lose its mark on
   * hydration or a form reset, and the next save then silently strips what the user never
   * touched — the modules-settings bug, once. Each instance seeds its state once from
   * `checked`, so rows inside an `{#each}` each keep their own mark.
   */
  let {
    name,
    checked = false,
    value = "true",
    id,
    form,
    disabled = false,
    class: klass = "",
    onchange,
  }: {
    name: string;
    /** Initial mark, from the record. Deliberately captured once. */
    checked?: boolean;
    value?: string;
    id?: string;
    form?: string;
    disabled?: boolean;
    class?: string;
    onchange?: (event: Event & { currentTarget: HTMLInputElement }) => void;
  } = $props();

  // svelte-ignore state_referenced_locally
  let isChecked = $state(checked);
</script>

<input
  type="checkbox"
  {name}
  {value}
  {id}
  {form}
  {disabled}
  class={klass}
  bind:checked={isChecked}
  {onchange}
/>
