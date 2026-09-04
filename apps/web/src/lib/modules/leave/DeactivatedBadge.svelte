<script lang="ts">
  /**
   * The pill beside a former colleague's name inside the fold (`DeactivatedMembersRow`). It is
   * drawn on the row rather than implied by the section, so the row still says what it is once
   * the fold is open and the strip has scrolled away — and so a row copied out of the fold by a
   * later change cannot read as an ordinary member.
   */
  import { t } from "$lib/core/i18n";
</script>

<span
  class="rounded-full bg-amber-100 px-2 py-0.5 text-[11px] font-medium text-amber-700 dark:bg-amber-950 dark:text-amber-300"
  title={t("leave.team.deactivated_hint")}
>
  {t("members.status.inactive")}
</span>
