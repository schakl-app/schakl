import type { RequestHandler } from "./$types";

// Per-tenant PWA manifest built at runtime from org branding (Golden Rule 4).
//
// The icon story (#198): a tenant's uploaded app icon (a square raster, `app_icon_url`) is
// served in real size variants by the API (`?size=192/512`, `maskable` pads into the safe
// zone), so Android/Lighthouse get proper 192+512 entries with both purposes. A tenant with
// only a favicon keeps the old single entry (their brand beats a perfect-but-wrong icon);
// an unbranded instance ships the bundled defaults — never an empty `icons` array.
export const GET: RequestHandler = async ({ locals }) => {
  const theme = locals.theme;
  const name = theme.brandName || "schakl."; // internal default only if unbranded
  const icon = (size: number, maskable = false) => ({
    src: `${theme.appIconUrl}?size=${size}${maskable ? "&maskable=true" : ""}`,
    sizes: `${size}x${size}`,
    type: "image/png",
    purpose: maskable ? "maskable" : "any",
  });
  const icons = theme.appIconUrl
    ? [icon(192), icon(512), icon(192, true), icon(512, true)]
    : theme.faviconUrl
      ? [{ src: theme.faviconUrl, sizes: "any", type: "image/png" }]
      : [
          { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png", purpose: "any" },
          { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png", purpose: "any" },
          {
            src: "/icons/icon-maskable-192.png",
            sizes: "192x192",
            type: "image/png",
            purpose: "maskable",
          },
          {
            src: "/icons/icon-maskable-512.png",
            sizes: "512x512",
            type: "image/png",
            purpose: "maskable",
          },
        ];
  const manifest = {
    name,
    short_name: name,
    start_url: "/",
    scope: "/",
    display: "standalone",
    // Tenant-derived, not hardcoded white (#198): the splash renders in the brand colour.
    background_color: theme.primaryColor,
    theme_color: theme.primaryColor,
    icons,
  };
  return new Response(JSON.stringify(manifest), {
    headers: {
      "content-type": "application/manifest+json",
      "cache-control": "no-cache",
    },
  });
};
