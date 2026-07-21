/**
 * Default @/# candidates for the shared rich-text editor (issue #237).
 *
 * #197 shipped the `#` trigger, but only the task-comment box ever received a candidate list,
 * so everywhere else the key silently did nothing. This module is the one place that knows how
 * to build the lists: org members (plus the host company's contacts, #165) for `@`, recent
 * tasks for `#` — shaped with status, assignee and due date so the dropdown can tell two
 * same-titled tasks apart. `RichTextEditor` calls these on first focus when no explicit list
 * was passed, so a page pays nothing for an editor nobody touches (docs/PERFORMANCE.md), and
 * the caches below mean five editors on one form still cost one fetch.
 */
import type { Candidate } from "$lib/core/richtext/editor";

export interface CandidateScope {
  companyId?: string | null;
  projectId?: string | null;
}

interface MemberRow {
  user_id: string;
  full_name: string | null;
  email: string;
}
interface StatusRow {
  key: string;
  name: string;
  is_terminal: boolean;
}
interface TaskRow {
  id: string;
  title: string;
  status: string;
  assignee_user_id?: string | null;
  due_date?: string | null;
}
interface ContactRow {
  id: string;
  first_name: string;
  last_name?: string | null;
  companies?: { name: string }[];
}

/** A candidate list is a convenience: any failure (offline, missing permission) degrades to
 *  "the trigger offers nothing", never to a broken editor. */
async function get(url: string): Promise<unknown> {
  try {
    const response = await fetch(url, { headers: { accept: "application/json" } });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

// Org-stable lookups, cached for the session (the interactions module's `manualKinds` pattern).
let membersPromise: Promise<MemberRow[]> | null = null;
function members(): Promise<MemberRow[]> {
  membersPromise ??= get("/api/v1/members/lookup").then(
    (rows) => (rows as MemberRow[] | null) ?? [],
  );
  return membersPromise;
}

let statusesPromise: Promise<StatusRow[]> | null = null;
function statuses(): Promise<StatusRow[]> {
  statusesPromise ??= get("/api/v1/tasks/statuses").then(
    (rows) => (rows as StatusRow[] | null) ?? [],
  );
  return statusesPromise;
}

// Task and contact lists change while you work: cached per scope, briefly, so a burst of
// editors on one page shares a fetch without a task created a minute ago staying invisible.
const TTL_MS = 60_000;
const scoped = new Map<string, { at: number; hit: Promise<Candidate[]> }>();

function cached(key: string, build: () => Promise<Candidate[]>): Promise<Candidate[]> {
  const entry = scoped.get(key);
  if (entry && Date.now() - entry.at < TTL_MS) return entry.hit;
  const hit = build();
  scoped.set(key, { at: Date.now(), hit });
  return hit;
}

/** Org members, plus the scope company's contacts (#165), for the `@` trigger. */
export function loadMentionCandidates(scope: CandidateScope = {}): Promise<Candidate[]> {
  const companyId = scope.companyId ?? "";
  return cached(`mentions:${companyId}`, async () => {
    const [team, contactsPage] = await Promise.all([
      members(),
      companyId ? get(`/api/v1/contacts?limit=200&company_id=${companyId}`) : null,
    ]);
    const contacts = ((contactsPage as { items?: ContactRow[] } | null)?.items ?? []).map(
      (c) => ({
        id: c.id,
        name: `${c.first_name} ${c.last_name ?? ""}`.trim(),
        kind: "contact" as const,
        subtitle: c.companies?.[0]?.name,
      }),
    );
    return [
      ...team.map((m) => ({ id: m.user_id, name: m.full_name || m.email, kind: "user" as const })),
      ...contacts,
    ];
  });
}

/**
 * Recent tasks for the `#` trigger — the deeper host link wins, like the interaction link
 * pickers (#222): project, else company, else org-wide. Status, assignee and due date ride
 * along so the dropdown says which task you're about to reference (#237).
 */
export function loadTaskCandidates(scope: CandidateScope = {}): Promise<Candidate[]> {
  const filter = scope.projectId
    ? `&project_id=${scope.projectId}`
    : scope.companyId
      ? `&company_id=${scope.companyId}`
      : "";
  return cached(`tasks:${filter}`, async () => {
    const [page, defs, team] = await Promise.all([
      get(`/api/v1/tasks?limit=200&meta=false&count=false${filter}`),
      statuses(),
      members(),
    ]);
    const today = new Date().toISOString().slice(0, 10);
    const memberName = (id: string) => {
      const m = team.find((row) => row.user_id === id);
      return m ? m.full_name || m.email : undefined;
    };
    return ((page as { items?: TaskRow[] } | null)?.items ?? []).map((row) => {
      const def = defs.find((s) => s.key === row.status);
      return {
        id: row.id,
        name: row.title,
        kind: "task" as const,
        subtitle: def?.name ?? row.status,
        assignee: row.assignee_user_id ? memberName(row.assignee_user_id) : undefined,
        due: row.due_date ?? undefined,
        overdue: !!row.due_date && row.due_date < today && !(def?.is_terminal ?? false),
      };
    });
  });
}
