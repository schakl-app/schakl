/**
 * Split a plaintext email body into the current message and its quoted conversation trail —
 * Gmail's "trimmed content" behaviour for the detail modal: a long thread shows only the
 * newest words; the history sits behind the ⋯ toggle.
 *
 * Detection is heuristic and deliberately conservative: when no marker is found (or the
 * marker sits at the very top, a quoted-only reply) the whole body renders untrimmed —
 * a false negative shows too much, a false positive would hide someone's words.
 */

/** Attribution lines and forwarded/original-message separators (EN + NL mail clients). */
const MARKERS: RegExp[] = [
  // Gmail / Apple Mail attribution: "On Mon, 13 Jul 2026 at 16:31, Stan <s@x> wrote:"
  /^On .{4,200} wrote:\s*$/,
  // Dutch Gmail: "Op ma 13 jul 2026 om 16:31 schreef Stan Marcusse <s@x>:"
  /^Op .{4,200} schreef .{1,120}:\s*$/,
  // Dutch Apple Mail: "Op 13 jul. 2026 om 16:43 heeft Stan <s@x> het volgende geschreven:"
  /^Op .{4,200} geschreven:\s*$/,
  // Outlook & friends: "-----Original Message-----" / forwarded separators
  /^-{2,}\s*(Original Message|Oorspronkelijk bericht|Forwarded message|Doorgestuurd bericht)\s*-{0,}/i,
];

/** Outlook-style top header block: "Van: …" / "From: …" followed shortly by more headers. */
const HEADER_START = /^(Van|From):\s.+$/;
const HEADER_FOLLOW = /^(Verzonden|Sent|Aan|To|Onderwerp|Subject|Datum|Date|Cc):\s.*$/;

const QUOTED = /^>/;

/** The trail must be worth folding: fewer lines than this stay visible. */
const MIN_TRAIL_LINES = 3;

export interface SplitBody {
  head: string;
  /** `null` when there is nothing (worth) collapsing. */
  trail: string | null;
}

function markerIndex(lines: string[]): number {
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i].trim();
    if (MARKERS.some((marker) => marker.test(line))) return i;
    // A "Van:/From:" line only counts with another header right under it — a plain sentence
    // starting with "From: " (someone quoting a form) must not swallow the rest of the mail.
    if (
      HEADER_START.test(line) &&
      lines.slice(i + 1, i + 4).some((next) => HEADER_FOLLOW.test(next.trim()))
    ) {
      return i;
    }
    // A run of ">"-quoted lines: fold from the first one.
    if (QUOTED.test(line) && lines.slice(i).filter((l) => QUOTED.test(l.trim())).length >= 2) {
      return i;
    }
  }
  return -1;
}

export function splitQuotedTrail(body: string): SplitBody {
  const lines = body.split("\n");
  const index = markerIndex(lines);
  if (index === -1) return { head: body, trail: null };
  const head = lines.slice(0, index).join("\n").trimEnd();
  const trail = lines.slice(index).join("\n").trim();
  // A quoted-only reply, or a trail too small to be clutter: show everything.
  if (!head.trim() || lines.length - index < MIN_TRAIL_LINES) return { head: body, trail: null };
  return { head, trail };
}
