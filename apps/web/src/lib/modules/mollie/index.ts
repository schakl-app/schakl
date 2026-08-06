/**
 * mollie web module (CLAUDE.md §6, epic #269 / issue #267) — mirrors the API module.
 *
 * It contributes no nav item and no panel, for the reason `portal` contributes none: a payment
 * credential is not a place you go. The surface you *work* on lives on the invoice — start a
 * checkout, read whether it settled — and that is `invoicing`'s screen for exactly the reason
 * the payment routes are `invoicing`'s: what has been paid is invoicing's question, reached
 * through the seam in `app/core/payments` so that neither module ever imports the other. The day
 * a second provider ships, that screen must not have to learn a second module's components.
 *
 * What is left over is org-wide configuration — which key, whose account, is it live — and
 * docs/UX.md principle 6 puts that under Instellingen, never as a button inside a working
 * screen.
 *
 * So what this registers is the module's *existence*, and that is not a formality: it is what
 * lets Instellingen → Modules list and label it (`module.mollie.label`), and what makes the
 * settings entry's `module: "mollie"` mean something — a tenant who switched the module off
 * gets no card and no rail item pointing at a screen whose API routes are not even mounted.
 */
import { registerWebModule } from "$lib/core/registry";

registerWebModule({ name: "mollie" });
