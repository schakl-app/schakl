import { defineCollection, z } from 'astro:content';
import { glob } from 'astro/loaders';
import { docsLoader } from '@astrojs/starlight/loaders';
import { docsSchema } from '@astrojs/starlight/schema';

export const collections = {
  docs: defineCollection({ loader: docsLoader(), schema: docsSchema() }),
  // CMS-created pages (Keystatic "Pagina's"): entry ids are 'nl/<slug>' / 'en/<slug>',
  // rendered by the [...slug] catch-all routes with the site chrome.
  pages: defineCollection({
    loader: glob({ pattern: '**/*.mdx', base: './src/content/pages' }),
    schema: z.object({
      title: z.string(),
      description: z.string().optional(),
    }),
  }),
};
