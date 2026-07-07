import type { OrgTheme } from "$lib/core/theme";
import type { SessionUser } from "$lib/core/session";

declare global {
  namespace App {
    interface Locals {
      user: SessionUser | null;
      theme: OrgTheme;
      locale: string;
    }
    interface PageData {
      user?: SessionUser | null;
      theme?: OrgTheme;
    }
    interface Error {
      code?: string;
    }
  }
}

export {};
