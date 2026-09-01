/**
 * Registers the core documents panel on the company hub (the image-attachment task).
 *
 * Tasks and projects render their attachments in their own page; the hub composes API panel
 * providers, so the client's documents ride that seam — a core `CompanyPanelSpec` keyed to the
 * API's `files.documents` panel, registered beside the activity trail for the same reason:
 * storing a file against a record is a platform capability, not something a module opts other
 * modules into.
 */
import { registerCoreCompanyPanel } from "$lib/core/registry";

import FilesCompanyPanel from "./FilesCompanyPanel.svelte";

registerCoreCompanyPanel({
  key: "files.documents",
  module: "files",
  component: FilesCompanyPanel,
  // Mirrors `core/storage/panels.py`: under the working surfaces, above the trail.
  position: 80,
  // No `emptyHref`: the chip unfolds the strip in place, because the upload control *is* the
  // panel — there is nowhere else to go (the rule #411 wrote down for Drive and Ads).
});
