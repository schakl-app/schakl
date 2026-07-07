import type { RequestHandler } from "./$types";

// Per-tenant PWA manifest built at runtime from org branding (Golden Rule 4).
export const GET: RequestHandler = async ({ locals }) => {
  const theme = locals.theme;
  const name = theme.brandName || "vlotr"; // internal default only if unbranded
  const manifest = {
    name,
    short_name: name,
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: theme.primaryColor,
    icons: theme.faviconUrl
      ? [{ src: theme.faviconUrl, sizes: "any", type: "image/png" }]
      : [],
  };
  return new Response(JSON.stringify(manifest), {
    headers: {
      "content-type": "application/manifest+json",
      "cache-control": "no-cache",
    },
  });
};
