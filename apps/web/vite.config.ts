import { sveltekit } from "@sveltejs/kit/vite";
import tailwindcss from "@tailwindcss/vite";
import { SvelteKitPWA } from "@vite-pwa/sveltekit";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [
    tailwindcss(),
    sveltekit(),
    SvelteKitPWA({
      registerType: "autoUpdate",
      // Branding is per-tenant and resolved at runtime, so we serve a dynamic
      // /manifest.webmanifest route instead of a build-time manifest (Golden Rule 4).
      manifest: false,
      injectRegister: "auto",
      workbox: {
        globPatterns: ["**/*.{js,css,html,svg,png,webp,woff2}"],
      },
    }),
  ],
  // Bundle server-imported runtime deps into the adapter-node output so the production
  // image is self-contained (no node_modules needed at runtime). `Markdown.svelte` renders
  // the escaped source during SSR, so `markdown.ts` (and its `dompurify` / `marked` imports)
  // is reachable on the server and must be bundled too, not left as a bare `import` the
  // node_modules-less runtime image can't resolve (#66).
  ssr: {
    noExternal: ["openapi-fetch", "dompurify", "marked", "libphonenumber-js"],
  },
  server: {
    host: true,
    port: 5173,
  },
});
