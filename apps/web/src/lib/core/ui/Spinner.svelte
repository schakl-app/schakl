<script lang="ts">
  /**
   * The one shared in-flight indicator (#242): the lucide loader spun by Tailwind's
   * `animate-spin`, sized like every other icon. Decorative (`aria-hidden`) — it always
   * stands next to visible text that says what is happening (a button label, a loading
   * line), never alone as the only signal.
   */
  import { LoaderCircle } from "@lucide/svelte";

  let { size = 16, class: klass = "" }: { size?: number; class?: string } = $props();
</script>

<LoaderCircle {size} class="animate-spin {klass}" aria-hidden="true" />
