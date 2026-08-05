<script lang="ts">
  /**
   * The control you cannot use yet (issue #137) — a button with a padlock that opens
   * `UpgradeModal` instead of doing the thing.
   *
   * The alternative was hiding it, and hiding is worse here. A feature the agency has simply
   * not bought is not a feature that does not exist: showing it locked is how anyone finds out
   * it is available at all, and it keeps the screen's shape stable across the moment a licence
   * lands. This is *not* the pattern for a permission — a colleague who may not do something
   * should not be shown a lock they can never open (docs/UX.md); it is only for entitlements,
   * which the org itself can change.
   *
   * It renders exactly like the real control it stands in for (same variant, same size), so the
   * layout does not jump when it is replaced by the working one.
   */
  import { Lock } from "@lucide/svelte";

  import Button from "$lib/core/ui/Button.svelte";
  import UpgradeModal from "$lib/core/ui/UpgradeModal.svelte";

  let {
    label,
    feature,
    deployment = "self_hosted",
    isInstanceOwner = false,
    upgradeHref,
    variant = "primary",
    size = "sm",
    title,
  }: {
    /** The label the working control would carry, already translated. */
    label: string;
    /** What is locked, already translated — passed straight to the dialog. */
    feature: string;
    deployment?: string;
    isInstanceOwner?: boolean;
    upgradeHref?: string;
    variant?: "primary" | "secondary";
    size?: "md" | "sm" | "xs";
    /** Hover text explaining the lock; the dialog says it properly on click. */
    title?: string;
  } = $props();

  let open = $state(false);
</script>

<!-- Deliberately enabled: a disabled button explains nothing and cannot be clicked to find out.
     The lock is the signal; the dialog is the explanation. -->
<Button type="button" {variant} {size} {title} onclick={() => (open = true)}>
  <Lock size={14} aria-hidden="true" />
  {label}
</Button>

<UpgradeModal bind:open {feature} {deployment} {isInstanceOwner} {upgradeHref} />
