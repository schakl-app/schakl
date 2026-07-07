/**
 * Web module + nav registry (CLAUDE.md §4, §6) — mirrors the API registry.
 *
 * Each web module self-registers nav items and any `CompanyPanel` components it contributes.
 * The shell renders nav from enabled modules; the company detail page composes their panels —
 * so a new attachable module needs no edits to the shell or the company page.
 */
import type { Component } from "svelte";

export interface NavItem {
  key: string;
  href: string;
  /** Returns the translated label (call a Paraglide accessor inside). */
  label: () => string;
  module: string;
  position?: number;
}

export interface CompanyPanelSpec {
  /** Matches the API PanelSpec.key it renders (e.g. "companies.details"). */
  key: string;
  module: string;
  component: Component<{ companyId: string; data: Record<string, unknown> }>;
  position?: number;
}

export interface WebModule {
  name: string;
  nav?: NavItem[];
  companyPanels?: CompanyPanelSpec[];
}

const _modules = new Map<string, WebModule>();

export function registerWebModule(mod: WebModule): void {
  _modules.set(mod.name, mod);
}

export function enabledWebModules(enabled: string[]): WebModule[] {
  return enabled
    .map((name) => _modules.get(name))
    .filter((m): m is WebModule => Boolean(m));
}

export function navItemsFor(enabled: string[]): NavItem[] {
  return enabledWebModules(enabled)
    .flatMap((m) => m.nav ?? [])
    .sort((a, b) => (a.position ?? 100) - (b.position ?? 100));
}

export function companyPanelComponent(
  enabled: string[],
  key: string,
): CompanyPanelSpec | undefined {
  return enabledWebModules(enabled)
    .flatMap((m) => m.companyPanels ?? [])
    .find((p) => p.key === key);
}
