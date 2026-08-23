/**
 * google_tag_manager web module: the third item in the Marketing group, and the connect surface
 * it contributes to the marketing picker. Self-registers on import via the `lib/modules` barrel.
 *
 * It sits beside Marketing → Overzicht and Google Ads because the three answer different
 * questions about the same client: how they are doing, what the advertising is doing, and what
 * is measuring any of it.
 *
 * **No company panel** (#411). It used to draw its own card on the client hub, beside the Ads
 * card and the marketing panel, printing largely the same facts one card lower down. The one
 * fact it carried that nothing else did — `workspace_changes`, a change staged weeks ago and
 * never published — now rides the marketing panel's connections row, through the API's
 * `app/core/tagmanager.py` seam. What replaces the card's connect button is the
 * `marketingConnectors` entry below, which puts it in the one control that attaches every
 * marketing source.
 */
import { Tags } from "@lucide/svelte";

import { t } from "$lib/core/i18n";
import { registerWebModule } from "$lib/core/registry";

import GtmConnectSection from "./GtmConnectSection.svelte";

registerWebModule({
  name: "google_tag_manager",
  // A conversation with somebody else's service (CLAUDE.md §6a).
  kind: "integration",
  nav: [
    {
      // A fresh, stable key: tenant nav renames and saved sidebar orderings key on it, so it
      // must never be derived from the href.
      key: "gtm",
      href: "/marketing/tag-manager",
      label: () => t("nav.gtm"),
      module: "google_tag_manager",
      group: "marketing",
      icon: Tags,
      // After Marketing → Overzicht (45) and Google Ads (46).
      position: 47,
      // UX-only hide; the page load and the API both re-check (docs/UX.md).
      requiresPermission: "google_tag_manager.container.read",
    },
  ],
  marketingConnectors: [
    {
      kind: "gtm",
      module: "google_tag_manager",
      labelKey: "gtm.connector.label",
      // The key the *call* makes, not the one the surface is about (#310): this control posts
      // `POST /gtm/containers`, which declares `google_tag_manager.settings.manage`. Gating it
      // on `marketing.link.manage` — the key its five neighbours in the picker use — would draw
      // a control the API then refuses, with a bare "geen toegang" no label on the screen
      // explains. It is deliberately *not* implied by the read permission either: attaching a
      // client's container is a settings act.
      requiresPermission: "google_tag_manager.settings.manage",
      component: GtmConnectSection,
    },
  ],
});
