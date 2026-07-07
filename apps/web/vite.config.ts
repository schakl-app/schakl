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
  // image is self-contained (no node_modules needed at runtime).
  ssr: {
    noExternal: ["openapi-fetch"],
  },
  server: {
    host: true,
    port: 5173,
  },
});
