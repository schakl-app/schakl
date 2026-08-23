/**
 * timeon web module (CLAUDE.md §6, §6a) — mirrors the API integration. Business-licensed.
 *
 * It contributes **no nav item** (#389), and the argument it used to make for one is worth
 * keeping because it was half right. A two-way sync produces a queue: conflicts have to be
 * settled by a person, and "a surface that has to be found is one that is not kept up to date"
 * (the availability rule, CLAUDE.md §14). That is a good argument for the *queue* being
 * reachable. It is not one for a permanent top-level menu item, because of what this integration
 * actually is: **a cutover, and a cutover ends.** `timeon` exists to carry one agency's hours
 * across while two systems are both in use; the day Timeon is switched off the entry points at
 * an empty screen, and until that day it is empty most days anyway, because most days there are
 * no conflicts. A queue that shows nothing every day is exactly the queue people stop reading.
 * It is also product-shaped wrong for a white-label platform: every other tenant of this codebase
 * saw a vendor's name in their main menu for a product they have never heard of.
 *
 * So the workspace is reached the way every other integration's working surface is — from
 * **Instellingen → Integraties → Timeon**, which links straight through — and it *finds* the
 * person on the days it has something to say: `/time` draws an unsettled-conflict count beside
 * the hours the conflicts are about, drawn when it is non-zero and absent when it is not. That is
 * the honest version of "a surface that has to be found": it occupies no slot on the days it has
 * nothing, and it is in front of somebody on the days it does.
 *
 * `nav.timeon` survives as the breadcrumb label for `/timeon` (`$lib/core/breadcrumb-labels`) —
 * a page still needs a name.
 *
 * **No company panel** (#411), and the loss is deliberate rather than overlooked: the hub's card
 * carried this client's pairing count and their open conflicts, and nothing takes its place.
 * The conflicts queue is where a decision is actually made; the hub only ever said one was
 * waiting.
 */
import { registerWebModule } from "$lib/core/registry";

registerWebModule({
  name: "timeon",
  // A conversation with somebody else's service (CLAUDE.md §6a) — and what
  // `tests/unit/settings-groups.test.ts` reads to decide which Instellingen group it lands in.
  kind: "integration",
});
