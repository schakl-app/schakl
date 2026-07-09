import js from "@eslint/js";
import svelte from "eslint-plugin-svelte";
import tseslint from "typescript-eslint";

export default tseslint.config(
  {
    ignores: [".svelte-kit/**", "build/**", "src/lib/paraglide/**", "src/lib/core/api/schema.d.ts"],
  },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...svelte.configs.recommended,
  ...svelte.configs.prettier,
  {
    languageOptions: {
      globals: { console: "readonly" },
    },
  },
  {
    files: ["**/*.svelte"],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
        extraFileExtensions: [".svelte"],
      },
    },
  },
  {
    // Svelte 5 `.svelte.ts` modules (runes usable outside components) are picked up by
    // eslint-plugin-svelte's recommended config, but without the inner TS parser it needs
    // to read type annotations — same fix as the plain `**/*.svelte` block above.
    files: ["**/*.svelte.ts"],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
      },
    },
  },
  {
    rules: {
      // Svelte 5 runes ($state, $derived, …) and event-handler props read as unused to the
      // base no-unused-vars rule; svelte-eslint already understands the runes themselves.
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_" }],
      "no-undef": "off", // TypeScript + svelte-eslint-parser already catch real undefined refs.
      // Adopting SvelteKit's typed resolve() on every href/goto() is a separate, sitewide
      // architectural change (every existing link would need rewriting) — not a style rule.
      "svelte/no-navigation-without-resolve": "off",
    },
  },
);
