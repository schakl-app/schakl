<script lang="ts">
  /**
   * A checkbox for posting forms whose mark is **component state, never one-way DOM**
   * (docs/UX.md): an input rendered with a bare `checked={…}` can lose its mark on
   * hydration or a form reset, and the next save then silently strips what the user never
   * touched — the modules-settings bug, once. Each instance seeds its state once from
   * `checked`, so rows inside an `{#each}` each keep their own mark.
   *
   * It also survives a form reset — `use:enhance`'s default success path calls
   * `form.reset()`, which reverts the DOM to its server-rendered mark without firing any
   * event Svelte can see. State and DOM then disagree, the UI shows the save "reverting",
   * and the *next* submit posts the reverted mark (the roles save bug, #253). The reset
   * listener re-asserts the state once the reset has applied, so the mark the user set is
   * the mark that renders and posts, always.
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

  let element = $state<HTMLInputElement | null>(null);

  // The reset event fires *before* the browser reverts the controls, so the re-assert is
  // queued to land just after — state wins over the reset, per the contract above.
  $effect(() => {
    const owner = element?.form;
    if (!owner) return;
    const reassert = () =>
      queueMicrotask(() => {
        if (element) element.checked = isChecked;
      });
    owner.addEventListener("reset", reassert);
    return () => owner.removeEventListener("reset", reassert);
  });
</script>

<input
  bind:this={element}
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
