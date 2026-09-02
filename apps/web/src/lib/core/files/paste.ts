/**
 * Naming a pasted image (shared by the attachment strip and the rich-text editor).
 *
 * A pasted screenshot arrives named `image.png`, which says nothing on a strip of five; name
 * it for the moment it was taken — on the org's clock, like every date the app computes
 * (CLAUDE.md §8, `$lib/core/today`).
 */
import { t } from "$lib/core/i18n";
import { getTimeZone } from "$lib/core/timezone";
import { orgToday } from "$lib/core/today";

export function pastedImageName(file: File): string {
  const now = new Date();
  const time = new Intl.DateTimeFormat("en-GB", {
    timeZone: getTimeZone(),
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  })
    .format(now)
    .replaceAll(":", "");
  const stamp = `${orgToday(now)}-${time}`;
  const ext = file.type === "image/jpeg" ? "jpg" : (file.type.split("/")[1] ?? "png");
  return `${t("files.pasted_name")}-${stamp}.${ext}`;
}
