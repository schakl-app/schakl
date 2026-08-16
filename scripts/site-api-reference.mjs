#!/usr/bin/env node
// site:api — generate the public API reference pages from the API's own OpenAPI document.
//
// Why this exists: schakl.dev documented the REST API in prose and never listed a single
// endpoint. The interactive reference (Swagger UI) lives at `/api/docs` on *your own instance*,
// which is exactly the wrong place for the two people most likely to want it — somebody
// deciding whether to buy, and somebody writing an integration before they have a box to run
// it on. So the map is published here, and the schemas stay where they can never drift: on the
// instance, generated from the running code.
//
// The generated pages carry three things per endpoint, in the order a caller needs them:
//
//   1. the method and path,
//   2. **the permission it declares** — the thing an API key has to be minted with, and the one
//      fact the OpenAPI document does not contain (it is a FastAPI dependency, not a schema);
//      `app/openapi_docs_export.py` recovers it by introspecting the route tree,
//   3. its query parameters, because those are what a caller gets wrong.
//
// Request and response *schemas* are deliberately named and not expanded. Inlining 700 Pydantic
// models would quadruple the site and would be the half most likely to go stale between
// releases, while `/api/openapi.json` on any instance is always exactly right. A reference that
// is 90% correct about field names is worse than one that says where the authoritative list is.
//
// Regenerate (the output is committed, so the site build never needs a Python toolchain):
//
//   uv run --directory apps/api python -m app.openapi_docs_export > /tmp/api-doc.json
//   node scripts/site-api-reference.mjs /tmp/api-doc.json
//
// Or in one go:  node scripts/site-api-reference.mjs --export

import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const OUT = 'apps/site/src/content/docs';
const LOCALES = ['en', 'nl'];
const METHODS = ['get', 'post', 'put', 'patch', 'delete'];

// Tags that are not part of the tenant API surface. The MCP tool builder excludes the same
// three (CLAUDE.md §12) for the same reason: they are the instance console and the first-run
// wizard, they are gated on a different axis entirely, and an agency integrating with their own
// workspace can never call them.
const EXCLUDED_TAGS = new Set(['instance', 'provisioning', 'setup']);

// The areas, and what falls in each. A tag not named here lands in `platform`, so a module that
// ships tomorrow appears in the reference rather than vanishing from it.
const AREAS = [
  {
    slug: 'clients',
    order: 1,
    tags: ['companies', 'contacts', 'activity', 'addresslookup'],
    title: { en: 'Clients and contacts', nl: 'Klanten en contactpersonen' },
    lead: {
      en: 'The hub every other module attaches to: the client record, the people at that client, the paper trail of what changed, and the address lookup the forms use.',
      nl: 'De spil waar elke andere module aan hangt: het klantrecord, de mensen bij die klant, het spoor van wat er veranderd is, en de adresopzoeker die de formulieren gebruiken.',
    },
  },
  {
    slug: 'work',
    order: 2,
    tags: ['projects', 'tasks', 'time', 'interactions'],
    title: { en: 'Work: projects, tasks, hours', nl: 'Werk: projecten, taken, uren' },
    lead: {
      en: 'What the agency actually does, and what it bills: projects with budgets, task boards and checklists, the timer and the timesheet, and every contact moment on one timeline.',
      nl: 'Wat het bureau werkelijk doet, en wat het factureert: projecten met budgetten, taakborden en checklists, de timer en de weekstaat, en elk contactmoment op één tijdlijn.',
    },
  },
  {
    slug: 'money',
    order: 3,
    tags: ['invoicing', 'subscriptions', 'mollie', 'snelstart'],
    title: { en: 'Money: invoices, quotes, retainers', nl: 'Geld: facturen, offertes, abonnementen' },
    lead: {
      en: 'Invoices and quotes built from the work that produced them, recurring agreements, the payment provider that settles them and the bookkeeping package they end up in.',
      nl: 'Facturen en offertes die uit het werk zelf komen, terugkerende afspraken, de betaalprovider die ze vereffent en het boekhoudpakket waar ze in belanden.',
    },
  },
  {
    slug: 'assets',
    order: 4,
    tags: ['domains', 'websites', 'hosting', 'cloudflare', 'oxxa', 'uptime', 'wordpress'],
    title: { en: 'Assets: domains, sites, hosting', nl: 'Assets: domeinen, sites, hosting' },
    lead: {
      en: "What an agency looks after on a client's behalf, and the four services it reads that from: the registrar, the DNS zone, the monitor and the site itself.",
      nl: 'Wat een bureau voor een klant beheert, en de vier diensten waar dat uit gelezen wordt: de registrar, de DNS-zone, de monitor en de site zelf.',
    },
  },
  {
    slug: 'marketing',
    order: 5,
    tags: ['marketing', 'google_ads', 'google_tag_manager', 'reporting'],
    title: { en: 'Marketing and reporting', nl: 'Marketing en rapportage' },
    lead: {
      en: "The client's numbers, the advertising that moves them, the container that measures them, and the monthly document that explains them.",
      nl: 'De cijfers van de klant, de advertenties die ze bewegen, de container die ze meet, en het maandelijkse document dat ze uitlegt.',
    },
  },
  {
    slug: 'people',
    order: 6,
    tags: ['leave', 'hr', 'portal'],
    title: { en: 'People: leave, contracts, portal', nl: 'Mensen: verlof, contracten, portaal' },
    lead: {
      en: 'Employees rather than client contacts: leave balances and requests, employment periods and work schedules, and the login a client gets to look in.',
      nl: 'Medewerkers in plaats van klantcontacten: verlofsaldi en aanvragen, dienstverbanden en werkroosters, en de inlog waarmee een klant meekijkt.',
    },
  },
  {
    slug: 'google',
    order: 7,
    tags: ['google'],
    title: { en: 'Google Workspace', nl: 'Google Workspace' },
    lead: {
      en: 'Calendar, Drive, Gmail and Contacts, connected per organisation with your own OAuth client and switched on per employee.',
      nl: 'Agenda, Drive, Gmail en Contacten, per organisatie gekoppeld met je eigen OAuth-client en per medewerker aangezet.',
    },
  },
  {
    slug: 'access',
    order: 8,
    tags: [
      'members',
      'roles',
      'users',
      'company-groups',
      'api-keys',
      'oauth',
      'auth',
      'sso-settings',
      'service-access',
    ],
    title: { en: 'Access: members, roles, keys', nl: 'Toegang: leden, rollen, sleutels' },
    lead: {
      en: 'Who may sign in, what they may do once they have, which clients they may see, and the keys that let a script act on their behalf.',
      nl: 'Wie er mag inloggen, wat diegene daarna mag, welke klanten diegene ziet, en de sleutels waarmee een script namens hen handelt.',
    },
  },
  {
    slug: 'platform',
    order: 9,
    tags: [],
    title: { en: 'Platform', nl: 'Platform' },
    lead: {
      en: 'Everything cross-cutting: the tenant metadata a client fetches first, files, notifications, automation rules, AI, custom fields, spreadsheets and bulk edits.',
      nl: 'Alles wat overal doorheen loopt: de organisatiegegevens die een client als eerste ophaalt, bestanden, meldingen, automatiseringsregels, AI, eigen velden, spreadsheets en bulkbewerkingen.',
    },
  },
];

const T = {
  en: {
    indexTitle: 'API reference',
    indexDesc:
      'Every endpoint of the schakl. REST API, grouped by area, with the permission each one requires.',
    permission: 'Permission',
    endpoint: 'Endpoint',
    what: 'What it does',
    none: 'None declared',
    query: 'Query parameters',
    path: 'Path parameters',
    body: 'Request body',
    returns: 'Returns',
    required: 'required',
    openReason: 'Deliberately open',
    generated:
      'This page is generated from the API\'s own OpenAPI document. Field-level schemas are not repeated here; your instance serves the authoritative ones.',
    scopeNote:
      'A permission written `module.resource.action:own` is scoped: `:own` covers rows that are yours, `:any` covers everyone\'s, and `:any` satisfies a check for `:own`.',
  },
  nl: {
    indexTitle: 'API-referentie',
    indexDesc:
      'Elk endpoint van de REST API van schakl., gegroepeerd per gebied, met het recht dat ervoor nodig is.',
    permission: 'Recht',
    endpoint: 'Endpoint',
    what: 'Wat het doet',
    none: 'Geen recht vereist',
    query: 'Queryparameters',
    path: 'Padparameters',
    body: 'Request body',
    returns: 'Antwoord',
    required: 'verplicht',
    openReason: 'Bewust open',
    generated:
      'Deze pagina wordt gegenereerd uit het OpenAPI-document van de API zelf. De velden per schema staan er niet bij; jouw eigen omgeving serveert de gezaghebbende versie.',
    scopeNote:
      'Een recht geschreven als `module.resource.action:own` heeft een bereik: `:own` dekt rijen die van jou zijn, `:any` die van iedereen, en `:any` voldoet aan een controle op `:own`.',
  },
};

// --- load ------------------------------------------------------------------------------------
let source = process.argv[2];
if (source === '--export' || !source) {
  const out = execFileSync(
    'uv',
    ['run', '--directory', 'apps/api', 'python', '-m', 'app.openapi_docs_export'],
    { encoding: 'utf8', maxBuffer: 256 * 1024 * 1024 },
  );
  source = null;
  var doc = JSON.parse(out);
} else {
  var doc = JSON.parse(readFileSync(source, 'utf8'));
}

const spec = doc.spec;
const permissions = doc.permissions ?? {};
const exemptions = doc.exemptions ?? {};

// --- shape the operations --------------------------------------------------------------------
const byTag = new Map();
for (const [path, ops] of Object.entries(spec.paths)) {
  for (const [method, op] of Object.entries(ops)) {
    if (!METHODS.includes(method)) continue;
    const tag = (op.tags ?? ['platform'])[0];
    if (EXCLUDED_TAGS.has(tag)) continue;
    if (!byTag.has(tag)) byTag.set(tag, []);
    byTag.get(tag).push({ path, method, op });
  }
}
for (const list of byTag.values()) {
  list.sort((a, b) => a.path.localeCompare(b.path) || METHODS.indexOf(a.method) - METHODS.indexOf(b.method));
}

const assigned = new Set(AREAS.flatMap((a) => a.tags));
const platform = AREAS.find((a) => a.slug === 'platform');
platform.tags = [...byTag.keys()].filter((t) => !assigned.has(t)).sort();

// --- render helpers ---------------------------------------------------------------------------
const esc = (s) =>
  String(s ?? '')
    .replace(/\|/g, '\\|')
    .replace(/</g, '&lt;')
    .replace(/\{/g, '&#123;')
    .replace(/\}/g, '&#125;')
    .replace(/\r?\n+/g, ' ')
    .trim();

const refName = (schema) => {
  if (!schema) return null;
  if (schema.$ref) return schema.$ref.split('/').pop();
  if (schema.items?.$ref) return `${schema.items.$ref.split('/').pop()}[]`;
  if (schema.anyOf) {
    const first = schema.anyOf.find((s) => s.$ref || s.items?.$ref);
    if (first) return refName(first);
  }
  if (schema.type) return schema.type;
  return null;
};

const paramType = (schema = {}) => {
  if (schema.$ref) return schema.$ref.split('/').pop();
  if (schema.anyOf) {
    const real = schema.anyOf.filter((s) => s.type !== 'null');
    return real.map((s) => paramType(s)).join(' | ') || 'string';
  }
  if (schema.type === 'array') return `${paramType(schema.items ?? {})}[]`;
  if (schema.enum) return schema.enum.map((v) => `\`${v}\``).join(' · ');
  return schema.type ?? 'string';
};

const title = (op, path, method) =>
  op.summary || `${method.toUpperCase()} ${path.replace('/api/v1', '')}`;

function permissionCell(op, locale) {
  const id = op.operationId;
  const declared = permissions[id];
  if (declared?.length) {
    return declared
      .map(([key, scope]) => `\`${key}${scope ? `:${scope}` : ''}\``)
      .join(', ');
  }
  const why = exemptions[id];
  if (why?.length) return `_${T[locale].openReason}_`;
  return `_${T[locale].none}_`;
}

function renderOperation({ path, method, op }, locale) {
  const t = T[locale];
  const lines = [];
  lines.push(`#### \`${method.toUpperCase()} ${path}\``);
  lines.push('');
  const summary = op.summary ? `**${esc(op.summary)}**` : '';
  const desc = op.description ? esc(op.description.split('\n\n')[0]) : '';
  if (summary || desc) lines.push([summary, desc].filter(Boolean).join(' — '));
  lines.push('');
  lines.push(`${t.permission}: ${permissionCell(op, locale)}`);

  const params = op.parameters ?? [];
  const pathParams = params.filter((p) => p.in === 'path');
  const queryParams = params.filter((p) => p.in === 'query');
  if (pathParams.length) {
    lines.push('');
    lines.push(
      `${t.path}: ${pathParams.map((p) => `\`${p.name}\``).join(', ')}`,
    );
  }
  if (queryParams.length) {
    lines.push('');
    lines.push(`${t.query}:`);
    lines.push('');
    lines.push(
      locale === 'nl' ? '| Naam | Type | Betekenis |' : '| Name | Type | Meaning |',
    );
    lines.push('| --- | --- | --- |');
    for (const p of queryParams) {
      const d = p.description || p.schema?.description || '';
      const def =
        p.schema?.default !== undefined ? ` (default \`${esc(p.schema.default)}\`)` : '';
      lines.push(
        `| \`${p.name}\` | ${paramType(p.schema)}${p.required ? ` · ${t.required}` : ''} | ${esc(d)}${def} |`,
      );
    }
  }
  const bodyRef = refName(
    op.requestBody?.content?.['application/json']?.schema ??
      op.requestBody?.content?.['multipart/form-data']?.schema,
  );
  if (bodyRef) {
    lines.push('');
    lines.push(`${t.body}: \`${bodyRef}\``);
  }
  const ok = Object.entries(op.responses ?? {}).find(([code]) => code.startsWith('2'));
  const outRef = ok && refName(ok[1]?.content?.['application/json']?.schema);
  if (ok) {
    lines.push('');
    lines.push(`${t.returns}: \`${ok[0]}\`${outRef ? ` · \`${outRef}\`` : ''}`);
  }
  lines.push('');
  return lines.join('\n');
}

function renderArea(area, locale) {
  const t = T[locale];
  const tags = area.tags.filter((tag) => byTag.has(tag));
  const count = tags.reduce((n, tag) => n + byTag.get(tag).length, 0);
  const out = [];
  out.push('---');
  // Quoted: half these titles carry a colon ("Money: invoices, quotes"), and an unquoted YAML
  // scalar with a colon in it is a mapping, not a string. The build fails on the frontmatter
  // with a stack trace pointing at js-yaml rather than at the sentence that caused it.
  out.push(`title: "${area.title[locale].replace(/"/g, "'")}"`);
  out.push(`description: "${area.lead[locale].replace(/"/g, "'")}"`);
  out.push('sidebar:');
  out.push(`  order: ${area.order}`);
  out.push('---');
  out.push('');
  out.push(area.lead[locale]);
  out.push('');
  out.push(
    locale === 'nl'
      ? `${count} endpoints, verdeeld over ${tags.length} ${tags.length === 1 ? 'groep' : 'groepen'}. ${t.scopeNote}`
      : `${count} endpoints across ${tags.length} ${tags.length === 1 ? 'group' : 'groups'}. ${t.scopeNote}`,
  );
  out.push('');

  for (const tag of tags) {
    const ops = byTag.get(tag);
    out.push(`## \`${tag}\``);
    out.push('');
    out.push(`| ${t.endpoint} | ${t.what} | ${t.permission} |`);
    out.push('| --- | --- | --- |');
    for (const entry of ops) {
      out.push(
        `| \`${entry.method.toUpperCase()}\` \`${entry.path.replace('/api/v1', '')}\` | ${esc(
          title(entry.op, entry.path, entry.method),
        )} | ${permissionCell(entry.op, locale)} |`,
      );
    }
    out.push('');
    out.push(
      locale === 'nl'
        ? '<details>\n<summary>Parameters en schema\'s per endpoint</summary>\n'
        : '<details>\n<summary>Parameters and schemas per endpoint</summary>\n',
    );
    for (const entry of ops) out.push(renderOperation(entry, locale));
    out.push('</details>');
    out.push('');
  }
  return out.join('\n');
}

function renderIndex(locale) {
  const t = T[locale];
  const total = [...byTag.values()].reduce((n, l) => n + l.length, 0);
  const nl = locale === 'nl';
  const out = [];
  out.push('---');
  out.push(`title: "${t.indexTitle}"`);
  out.push(`description: "${t.indexDesc}"`);
  out.push('sidebar:');
  out.push('  order: 0');
  out.push('---');
  out.push('');
  out.push("import { Aside, CardGrid, LinkCard } from '@astrojs/starlight/components';");
  out.push('');
  out.push(
    nl
      ? `Alles wat de app doet loopt over deze API: de webapp praat nooit rechtstreeks met de database, dus er is geen achterdeur die een script mist. Hieronder staan alle **${total} endpoints**, gegroepeerd per gebied, met per endpoint het recht dat je sleutel moet dragen.`
      : `Everything the app does travels this API: the web app never talks to the database directly, so there is no back door a script misses. Below are all **${total} endpoints**, grouped by area, each with the permission your key has to carry.`,
  );
  out.push('');
  out.push(
    nl
      ? 'Hoe je een sleutel maakt en hoe de foutafhandeling, paginering en beperkingen werken, staat in [REST API](/nl/docs/integrations/rest-api/). Deze pagina is de kaart; die pagina is de handleiding.'
      : 'How to mint a key, and how errors, paging and rate limits work, is in [REST API](/en/docs/integrations/rest-api/). This page is the map; that page is the manual.',
  );
  out.push('');
  out.push(`<Aside type="note">`);
  out.push(t.generated);
  out.push('');
  out.push(
    nl
      ? 'Op je eigen omgeving staat de interactieve versie op `/api/docs`, met het document zelf op `/api/openapi.json`. Die kent jouw ingeschakelde modules, dus hij is korter dan deze lijst en altijd precies goed.'
      : 'On your own instance the interactive version sits at `/api/docs`, with the document itself at `/api/openapi.json`. That one knows which modules you switched on, so it is shorter than this list and always exactly right.',
  );
  out.push('</Aside>');
  out.push('');
  out.push(nl ? '## De gebieden' : '## The areas');
  out.push('');
  out.push('<CardGrid>');
  for (const area of AREAS) {
    const tags = area.tags.filter((tag) => byTag.has(tag));
    if (!tags.length) continue;
    const count = tags.reduce((n, tag) => n + byTag.get(tag).length, 0);
    out.push(
      `  <LinkCard title="${area.title[locale]} (${count})" href="/${locale}/docs/api/${area.slug}/" description="${area.lead[
        locale
      ].replace(/"/g, "'")}" />`,
    );
  }
  out.push('</CardGrid>');
  out.push('');
  out.push(nl ? '## Wat er niet in staat' : '## What is not here');
  out.push('');
  out.push(
    nl
      ? 'Drie groepen endpoints zijn bewust weggelaten, dezelfde drie die ook geen MCP-tool worden: het beheerconsole van de installatie (`/instance`), de inrichtingsroutes van de cloudversie, en de eerste-installatiewizard (`/setup`). Ze horen niet bij de werkruimte van een bureau en zijn op een andere as afgeschermd dan de rechten hieronder.'
      : 'Three groups of endpoints are deliberately left out, the same three that never become MCP tools: the installation console (`/instance`), the cloud provisioning routes, and the first-run wizard (`/setup`). They are not part of an agency workspace, and they are gated on a different axis from the permissions below.',
  );
  out.push('');
  return out.join('\n');
}

// --- write ------------------------------------------------------------------------------------
let written = 0;
for (const locale of LOCALES) {
  const dir = join(OUT, locale, 'docs', 'api');
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  writeFileSync(join(dir, 'index.mdx'), `${renderIndex(locale)}\n`, 'utf8');
  written++;
  for (const area of AREAS) {
    if (!area.tags.filter((tag) => byTag.has(tag)).length) continue;
    writeFileSync(join(dir, `${area.slug}.mdx`), `${renderArea(area, locale)}\n`, 'utf8');
    written++;
  }
}

const total = [...byTag.values()].reduce((n, l) => n + l.length, 0);
const withPerm = [...byTag.values()]
  .flat()
  .filter((e) => (permissions[e.op.operationId] ?? []).length).length;
console.log(
  `site:api ok — ${written} pages, ${total} endpoints, ${withPerm} carrying a declared permission, ` +
    `${total - withPerm} deliberately open.`,
);
