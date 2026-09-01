/**
 * The form actions behind every contactmomenten panel. SvelteKit actions live on the page, so
 * each host detail page (company / project / contact / task) spreads these into its own
 * `actions` — the panel body posts to `?/createInteraction` etc. wherever it renders.
 */
import { fail, type RequestEvent } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";
import { parseAssignees } from "$lib/core/assignees";
import { apiErrorKey } from "$lib/core/errors";
import { checked } from "$lib/core/forms";
import { apiFor } from "$lib/core/session";

const LINK_FIELDS = ["company_id", "project_id", "task_id", "contact_id"] as const;

/**
 * The contact roster the form posted (#300): `ContactChips` serialises its chips into one
 * comma-separated hidden field, in chip order, because an edit surface has one save button.
 *
 * `null` when the form carried no such field at all — the API reads that as "leave the roster
 * alone", which is what keeps a form that never rendered the picker (there isn't one today, but
 * there was a contact-only one yesterday) from clearing what it never showed.
 */
function contactIds(form: FormData): string[] | null {
  if (!form.has("contact_ids")) return null;
  return String(form.get("contact_ids") ?? "")
    .split(",")
    .map((id) => id.trim())
    .filter(Boolean);
}

/** The link fields plus the roster, as every write path sends them. */
function linkBody(form: FormData): Record<string, string | string[] | null> {
  const roster = contactIds(form);
  return {
    ...Object.fromEntries(
      LINK_FIELDS.map((field) => [field, String(form.get(field) ?? "").trim() || null]),
    ),
    ...(roster ? { contact_ids: roster } : {}),
  };
}

/** "2026-07-10" + "14:30" → the tenant's wall clock, naive; the API attaches the org zone. */
function occurredAt(form: FormData): string | null {
  const date = String(form.get("occurred_date") ?? "").trim();
  if (!date) return null;
  const time = String(form.get("occurred_time") ?? "").trim() || "12:00";
  return `${date}T${time}:00`;
}

function parseCustom(raw: FormDataEntryValue | null): Record<string, unknown> {
  try {
    return JSON.parse(String(raw ?? "{}"));
  } catch {
    return {};
  }
}

function links(form: FormData): Record<string, string | string[]> {
  const out: Record<string, string | string[]> = {};
  for (const field of LINK_FIELDS) {
    const value = String(form.get(field) ?? "").trim();
    if (value) out[field] = value;
  }
  const roster = contactIds(form);
  if (roster) out.contact_ids = roster;
  return out;
}

/**
 * The three bulk review actions, which differ only in endpoint and payload (#299).
 *
 * `ids` arrives as the comma-joined selection the bulk bar posted. Only link fields the user
 * actually filled are forwarded — see `bulkApproveInteractions` for why an unfilled one must
 * be *absent* rather than `null` here.
 *
 * An approve may carry `approve_ids` instead: the file-and-approve button in the bulk assign
 * dialog shares that form with plain "file", and the two act on genuinely different sets —
 * every reviewable row can be re-filed, only a pending one can be approved. One form cannot
 * hold two `ids` fields, so the narrower set travels under its own name.
 */
async function bulkReview(event: RequestEvent, kind: "approve" | "assign" | "reject") {
  const form = await event.request.formData();
  const raw = (kind === "approve" && form.get("approve_ids")) || form.get("ids");
  const ids = String(raw ?? "")
    .split(",")
    .map((id) => id.trim())
    .filter(Boolean);
  if (ids.length === 0) return fail(400, { error: "errors.required" });

  const body: Record<string, unknown> = { ids };
  if (kind === "reject") {
    body.suppress_thread = form.get("suppress_thread") === "1";
  } else {
    for (const field of LINK_FIELDS) {
      const value = String(form.get(field) ?? "").trim();
      if (value) body[field] = value;
    }
  }
  const { data, error } = await apiFor(event).POST(`/api/v1/interactions/bulk/${kind}` as const, {
    body: body as never,
  });
  if (error || !data) return fail(400, { error: apiErrorKey(error).key });
  // `failed` carries a server-side default, so the generated client types it optional even
  // though the response always has it.
  const failed = data.failed ?? [];
  return {
    ok: true,
    bulkResult: {
      kind,
      succeeded: data.succeeded,
      // The distinct reasons, so the bar can say *why* rows were skipped rather than only
      // how many — "already reviewed" and "someone else's mailbox" need different answers.
      failed: failed.length,
      reasons: [...new Set(failed.map((f) => f.error))],
    },
  };
}

export const interactionActions = {
  createInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const occurred = occurredAt(form);
    if (!occurred) return fail(400, { error: "errors.required" });
    // "Voeg aan mijn uren toe" (#175): the linked entry rides the same request. Its start is
    // the moment's own time field (#184); only the end is extra. Times follow the time module's
    // wall-clock-as-UTC convention, on the interaction's date.
    const date = String(form.get("occurred_date") ?? "").trim();
    const logStart = String(form.get("occurred_time") ?? "").trim();
    const logEnd = String(form.get("log_end") ?? "").trim();
    const logTime =
      form.get("log_time") === "1" && date && logStart && logEnd
        ? { started_at: `${date}T${logStart}:00Z`, ended_at: `${date}T${logEnd}:00Z` }
        : undefined;
    const api = apiFor(event);
    const { data, error } = await api.POST("/api/v1/interactions", {
      body: {
        kind: String(form.get("kind") ?? "note"),
        occurred_at: occurred,
        // Optional: an empty field is `null`, not `""` — the row is then titled by its kind.
        subject: String(form.get("subject") ?? "").trim() || null,
        body_text: String(form.get("body_text") ?? "").trim() || null,
        direction: String(form.get("direction") ?? "none") as "none",
        ...(logTime ? { log_time: logTime } : {}),
        ...links(form),
      },
    });
    if (error || !data) return fail(400, { error: apiErrorKey(error).key });
    // "Close task with this" ticked in the create form (#232, mirroring the approve flow):
    // the create stands on its own — a close failure reports, it never rolls the create back.
    const task_id = String(form.get("task_id") ?? "").trim();
    const close_status = String(form.get("close_status") ?? "").trim();
    if (form.get("close_task") === "1" && task_id && close_status) {
      const { error: closeError } = await api.PATCH("/api/v1/tasks/{task_id}", {
        params: { path: { task_id } },
        body: { status: close_status, closing_interaction_id: data.id },
      });
      if (closeError) {
        return fail(400, { error: apiErrorKey(closeError).key, createdButCloseFailed: true });
      }
    }
    return { ok: true };
  },

  /**
   * Log an email from its `.eml` export (#262). Multipart through a plain fetch — the typed
   * client has no multipart serializer (the file-upload actions do the same) — carrying the
   * same cookie + tenant host the client would send.
   *
   * The API answers 409 when this `Message-ID` is already on the timeline; that is a question,
   * not a refusal, so it comes back as `emlDuplicate` and the form offers "toch vastleggen".
   */
  uploadInteractionEml: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const upload = form.get("file");
    if (!(upload instanceof File) || upload.size === 0)
      return fail(400, { error: "errors.required" });
    const body = new FormData();
    body.append("file", upload, upload.name);
    for (const [field, value] of Object.entries(links(form))) {
      // A roster is a repeated field, never a joined string: FastAPI reads `list[UUID]` from
      // `contact_ids=…&contact_ids=…`, and one comma-joined value parses as no UUID at all.
      if (Array.isArray(value)) for (const id of value) body.append(field, id);
      else body.append(field, value);
    }
    if (form.get("allow_duplicate") === "1") body.append("allow_duplicate", "true");
    const res = await event.fetch(`${apiBaseUrl()}/api/v1/interactions/upload-eml`, {
      method: "POST",
      headers: {
        cookie: event.request.headers.get("cookie") ?? "",
        "x-forwarded-host": event.request.headers.get("host") ?? "",
      },
      body,
    });
    if (!res.ok) {
      const envelope = await res.json().catch(() => null);
      const parsed = apiErrorKey(envelope);
      return fail(res.status === 413 ? 413 : 400, {
        // A field-level key (bad type, unreadable message) is the specific one; fall back to
        // the envelope's message so a 403/500 still says something true.
        error: parsed.fields?.file ?? parsed.key,
        emlDuplicate: res.status === 409,
      });
    }
    const data = await res.json();
    return {
      ok: true,
      emlUploaded: {
        stored: Number(data?.attachments_stored ?? 0),
        skipped: Number(data?.attachments_skipped ?? 0),
      },
    };
  },

  /**
   * Resolve a pasted Gmail reference, or read one conversation (#342).
   *
   * A **form action** rather than a browser `fetch`: the whole flow is server-rendered, so it
   * needs no edge route to proxy `/api/v1` and works the same in every deployment. Both shapes
   * land here because the screen is one screen — a pasted link and "mist er een bericht?"
   * produce the same list of candidates, and only where the id came from differs.
   */
  lookupGmailMessage: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const reference = String(form.get("reference") ?? "").trim();
    const threadId = String(form.get("thread_id") ?? "").trim();
    if (!reference && !threadId) return fail(400, { error: "errors.required" });
    const api = apiFor(event);
    const { data, error } = threadId
      ? await api.GET("/api/v1/google/gmail/threads/{thread_id}", {
          params: { path: { thread_id: threadId } },
        })
      : await api.GET("/api/v1/google/gmail/lookup", { params: { query: { reference } } });
    // A refusal keeps the reference on the form: it is the thing the user must correct, and
    // blanking it would make them go back to Gmail and copy it again to read the message.
    if (error) return fail(400, { error: apiErrorKey(error).key, gmailReference: reference });
    return { gmailLookup: data, gmailReference: reference };
  },

  /**
   * Search the caller's **own** mailbox for a message to file (#372).
   *
   * Its own action rather than a mode on `lookupGmailMessage`: a different question, different
   * inputs, and its own refusal ("no fields at all" is "list my mailbox", which is the one
   * thing this is not). Both end in the same candidate list, which is what the shared picker
   * is for — two ways in must never come to offer different things.
   *
   * Named fields all the way down. The API takes `participant` / `subject` / `after` /
   * `before` and builds the Gmail query itself, so nothing here forwards operator syntax.
   */
  searchGmailMessages: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const query = {
      participant: String(form.get("participant") ?? "").trim() || undefined,
      subject: String(form.get("subject") ?? "").trim() || undefined,
      after: String(form.get("after") ?? "").trim() || undefined,
      before: String(form.get("before") ?? "").trim() || undefined,
    };
    const { data, error } = await apiFor(event).GET("/api/v1/google/gmail/search", {
      params: { query },
    });
    // The lookup's rule: a refusal keeps what was typed. It is the thing the user has to
    // correct, and re-typing an address is the least helpful thing to ask of them.
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { gmailLookup: data };
  },

  /** Log one message the poller skipped, filed where the dialog says (#342). */
  importGmailMessage: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const message_id = String(form.get("message_id") ?? "").trim();
    if (!message_id) return fail(400, { error: "errors.required" });
    const body = linkBody(form);
    const { data, error } = await apiFor(event).POST("/api/v1/google/gmail/import", {
      body: {
        message_id,
        company_id: (body.company_id as string | null) ?? null,
        project_id: (body.project_id as string | null) ?? null,
        task_id: (body.task_id as string | null) ?? null,
        ...(Array.isArray(body.contact_ids) ? { contact_ids: body.contact_ids } : {}),
        allow_duplicate: form.get("allow_duplicate") === "1",
        enrich_task: checked(form, "enrich_task"),
      },
    });
    if (error)
      return fail(400, {
        error: apiErrorKey(error).key,
        // The same "log it anyway" confirm the .eml upload offers, for the same reason: a
        // colleague's mailbox having logged it is a warning, not a wall (#262).
        gmailDuplicate: apiErrorKey(error).key === "errors.interactions_eml_duplicate",
      });
    return { gmailImported: data };
  },

  updateInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const occurred = occurredAt(form);
    const { error } = await apiFor(event).PATCH("/api/v1/interactions/{interaction_id}", {
      params: { path: { interaction_id: id } },
      body: {
        kind: String(form.get("kind") ?? "note"),
        ...(occurred ? { occurred_at: occurred } : {}),
        // Emptied on purpose clears it — the form always renders this field, so a blank one
        // is a decision, unlike the conditionally-rendered fields below.
        subject: String(form.get("subject") ?? "").trim() || null,
        // Only fields the form actually rendered are sent (the API's PATCH is
        // `exclude_unset`): editing an uploaded email (#262) offers neither the note editor
        // nor the direction select, and an absent field must leave the received message
        // alone rather than blanking its body or resetting its direction to "none".
        ...(form.has("body_text")
          ? { body_text: String(form.get("body_text") ?? "").trim() || null }
          : {}),
        ...(form.has("direction")
          ? { direction: String(form.get("direction") ?? "none") as "none" }
          : {}),
        // The edit form carries all four link pickers now (#263, was contact-only since #173):
        // an edit may set, repoint or clear any of them, the same explicit-null contract the
        // move dialog's PATCH uses. The client rides along as the value the form derived from
        // the project/task — `_resolve_links(partial=True)` does not derive over an explicit
        // key, so posting a bare null here would drop the client the picker just showed. The
        // roster overrules the (now unrendered) `contact_id` at the API, by contract (#300).
        ...linkBody(form),
      },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { ok: true };
  },

  deleteInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).DELETE("/api/v1/interactions/{interaction_id}", {
      params: { path: { interaction_id: id } },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { ok: true };
  },

  approveInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    // Assign links in the same step (#183) only when the approve came from the review dialog
    // (`assign=1`); the one-click inline approve sends no links and touches none.
    //
    // "Laat schakl deze taak invullen" (#327) rides the same body. Read by **presence**, never
    // against a literal: `FormCheckbox` posts "true" and a bare input posts "on", so any
    // comparison naming one of them silently posts `false` for the other (CLAUDE.md §10).
    const body =
      form.get("assign") === "1"
        ? { ...linkBody(form), enrich_task: checked(form, "enrich_task") }
        : undefined;
    const api = apiFor(event);
    const { error } = await api.POST("/api/v1/interactions/{interaction_id}/approve", {
      params: { path: { interaction_id: id } },
      ...(body ? { body } : {}),
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    // "Close the task with this contact moment" ticked in the review dialog (#157 extended):
    // the approve stands on its own — a close failure reports, it never rolls the approve back.
    const task_id = String(form.get("task_id") ?? "").trim();
    const close_status = String(form.get("close_status") ?? "").trim();
    if (form.get("close_task") === "1" && task_id && close_status) {
      const { error: closeError } = await api.PATCH("/api/v1/tasks/{task_id}", {
        params: { path: { task_id } },
        body: { status: close_status, closing_interaction_id: id },
      });
      if (closeError) {
        return fail(400, { error: apiErrorKey(closeError).key, approvedButCloseFailed: true });
      }
    }
    // The review dialog made this task while reading this message (`review_task=1`), so the
    // reviewer's next act is checking it: hand the id back and the dialog opens the task for
    // review beside the message, rather than redirecting onto its page and losing the inbox
    // and the e-mail both. Not a redirect any more, so the close above needs no ordering rule.
    if (form.get("review_task") === "1" && task_id) {
      return { ok: true, reviewTaskId: task_id };
    }
    return { ok: true };
  },

  /**
   * The review slide-over's save (`TaskReviewDialog`): the task's defining fields, patched
   * through the task's own endpoint with exactly what the form carried — partial, like every
   * update action here. The roster and the due-date rules mirror the task page's `update`:
   * `assignees` is one JSON field or absent, an emptied deadline is refused before the API
   * says the same less clearly, and a later deadline carries its reason.
   */
  updateReviewTask: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const task_id = String(form.get("task_id") ?? "").trim();
    if (!task_id) return fail(400, { error: "errors.required" });
    if (form.has("title") && !String(form.get("title") ?? "").trim()) {
      return fail(400, { error: "errors.required" });
    }
    if (form.has("due_date") && !String(form.get("due_date") ?? "").trim()) {
      return fail(400, { error: "errors.required" });
    }
    const body: Record<string, unknown> = {};
    for (const field of [
      "title",
      "description",
      "project_id",
      "assignee_contact_id",
      "due_date",
      "due_change_reason",
    ]) {
      if (form.has(field)) {
        const raw = String(form.get(field) ?? "").trim();
        body[field] = raw || null;
      }
    }
    const assignees = parseAssignees(form.get("assignees"));
    if (assignees !== undefined) body.assignees = assignees;
    const { error } = await apiFor(event).PATCH("/api/v1/tasks/{task_id}", {
      params: { path: { task_id } },
      body,
    });
    if (error) {
      const e = apiErrorKey(error);
      return fail(400, {
        error:
          e.fields?.due_change_reason ??
          e.fields?.due_date ??
          e.fields?.assignee_contact_id ??
          e.key,
      });
    }
    return { ok: true };
  },

  /**
   * Move / re-link (#147). One dialog, two API paths: a manual row changes links through the
   * ordinary PATCH (own/any write scope); a gmail row goes through the owner-only review
   * remap. An empty picker posts "" and clears the link (explicit null).
   */
  moveInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const body = linkBody(form);
    const api = apiFor(event);
    const { error } =
      String(form.get("source") ?? "") === "gmail"
        ? await api.POST("/api/v1/interactions/{interaction_id}/remap", {
            params: { path: { interaction_id: id } },
            body,
          })
        : await api.PATCH("/api/v1/interactions/{interaction_id}", {
            params: { path: { interaction_id: id } },
            body,
          });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { ok: true };
  },

  /**
   * Create a contact from an unknown email participant (#160): the chip's ＋ opens the full
   * contact dialog prefilled with name + address; a checked "link to client" box carries the
   * interaction's company. Rides on every host page that spreads these actions, so the flow
   * exists wherever the timeline renders.
   */
  createParticipantContact: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const first_name = String(form.get("first_name") ?? "").trim();
    if (!first_name) return fail(400, { qcError: "errors.required" });
    const company_id = String(form.get("company_id") ?? "").trim();
    const { error } = await apiFor(event).POST("/api/v1/contacts", {
      body: {
        first_name,
        last_name: String(form.get("last_name") ?? "").trim() || null,
        email: String(form.get("email") ?? "").trim() || null,
        phone: String(form.get("phone") ?? "").trim() || null,
        job_title: String(form.get("job_title") ?? "").trim() || null,
        // The API links and promotes the first to primary only when the company is new to
        // the contact; an unchecked box simply creates an unlinked contact.
        company_ids: company_id ? [company_id] : undefined,
        custom: parseCustom(form.get("custom")),
      },
    });
    if (error) return fail(400, { qcError: apiErrorKey(error).key });
    return { ok: true };
  },

  /**
   * Inline-create behind the form's contact picker (#173): creates the contact immediately
   * and answers with `inlineCreated` so the picker that asked auto-selects it (docs/UX.md).
   * Distinct from `createParticipantContact`, whose chip flow has no picker to select into.
   */
  createInteractionContact: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const first_name = String(form.get("first_name") ?? "").trim();
    if (!first_name) return fail(400, { qcError: "errors.required" });
    const company_id = String(form.get("company_id") ?? "").trim();
    const { data, error } = await apiFor(event).POST("/api/v1/contacts", {
      body: {
        first_name,
        last_name: String(form.get("last_name") ?? "").trim() || null,
        email: String(form.get("email") ?? "").trim() || null,
        phone: String(form.get("phone") ?? "").trim() || null,
        job_title: String(form.get("job_title") ?? "").trim() || null,
        company_ids: company_id ? [company_id] : undefined,
        custom: parseCustom(form.get("custom")),
      },
    });
    if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
    return {
      inlineCreated: {
        slot: String(form.get("slot") ?? "") || "interaction_contact",
        id: data.id,
        // The picker labels the new option from this, not from what was typed into it: the
        // draft is a first name at best, and the dialog is where the surname was filled in.
        name: `${data.first_name} ${data.last_name ?? ""}`.trim(),
      },
    };
  },

  /**
   * Inline-create behind the client picker on every contactmoment surface (docs/UX.md — per-
   * picker definition of done). It used to live on the host *page*: the form passed what was
   * typed outwards and `/interactions` owned the dialog, so the ＋ existed on exactly one screen
   * and nowhere else — not on the edit form beside it, and on no company/project/contact/task
   * page, where a timeline renders through the very same component. Filing a moment for a client
   * nobody had entered yet meant leaving the form. Riding in `interactionActions` puts it
   * wherever the panel already posts.
   */
  createInteractionCompany: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { qcError: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/companies", {
      body: {
        name,
        website: String(form.get("website") ?? "").trim() || null,
        status: String(form.get("status") ?? "active") as "active",
        custom: parseCustom(form.get("custom")),
      },
    });
    if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
    return {
      inlineCreated: {
        slot: String(form.get("slot") ?? "") || "interaction_company",
        id: data.id,
        name: data.name,
      },
    };
  },

  /**
   * Inline-create behind the review dialog's task picker (docs/UX.md): creates the task
   * immediately and answers with `inlineCreated` so the picker auto-selects it. The dialog's
   * current client/project ride along, so the new task lands where the email is being filed.
   */
  createInteractionTask: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const title = String(form.get("title") ?? "").trim();
    if (!title) return fail(400, { qcError: "errors.required" });
    const company_id = String(form.get("company_id") ?? "").trim();
    const project_id = String(form.get("project_id") ?? "").trim();
    // The whole roster, not one id (#375): the dialog draws `AssigneePicker`, which serialises
    // every chip into one hidden field. `undefined` is "the dialog did not render the picker"
    // — an org with no roster to offer — and is not the same as `[]` ("nobody"), so it is never
    // synthesised here.
    const assignees = parseAssignees(form.get("assignees"));
    // Required (#392) — the dialog's field says so, and this is the backstop.
    const due_date = String(form.get("due_date") ?? "").trim();
    if (!due_date) return fail(400, { qcError: "errors.required" });
    const { data, error } = await apiFor(event).POST("/api/v1/tasks", {
      body: {
        title,
        company_id: company_id || undefined,
        project_id: project_id || undefined,
        assignees,
        due_date,
        priority: "normal",
        requires_interaction: false,
        visible_to_client: false,
      },
    });
    if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
    return {
      inlineCreated: {
        slot: String(form.get("slot") ?? "") || "move_task",
        id: data.id,
        // The picker labels the new option from this, not from what was typed into it: the
        // persistent ＋ opens the dialog with no draft at all, and the title is filled in
        // there — so a label taken from the picker's query read "—" on the row just created.
        name: data.title,
        project_id: data.project_id ?? null,
        company_id: data.company_id ?? null,
        // The picker's option carries the assignee for the same reason a loaded one does:
        // "sluit deze taak" is a task write, and `tasks.task.write:own` means assignee. The
        // API resolves it (the dialog may leave it blank), so read it off the created row
        // rather than off the form.
        assignee_user_id: data.assignee_user_id ?? null,
        // …and the *whole* roster beside it, because `:own` means **any** assignee, not the
        // starred one (`caller_may_write_task`). Sending only the primary would hide "sluit
        // deze taak" from the second person on a task they may certainly close.
        assignees: (data.assignees ?? []).map((entry) => ({ user_id: entry.user_id })),
      },
    };
  },

  /**
   * Inline-create behind the review dialog's project picker (docs/UX.md — per-picker definition
   * of done): creates the project immediately and answers with `inlineCreated` so the picker
   * auto-selects it, which is what puts it on the row the approve is about to assign. Until now
   * an email that turned out to be the start of new work could only be approved onto a project
   * that already existed — filing it meant leaving the review, creating the project elsewhere,
   * and finding the message again.
   *
   * The dialog's current client rides along (#247), so the new project lands on the client the
   * email is being filed to; `company_id` comes back so the picker's cascade can backfill it.
   */
  createInteractionProject: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const name = String(form.get("name") ?? "").trim();
    if (!name) return fail(400, { qcError: "errors.required" });
    // A project belongs to a client (`ProjectCreate`): named here so the dialog says
    // which field, instead of relaying a bare validation envelope.
    const company_id = String(form.get("company_id") ?? "").trim();
    if (!company_id) return fail(400, { qcError: "errors.projects_company_required" });
    const { data, error } = await apiFor(event).POST("/api/v1/projects", {
      body: {
        name,
        company_id,
        status: "active",
        budget_period: "total",
        currency: event.locals.theme.currency,
        billable_default: form.get("billable_default") !== null,
        custom: parseCustom(form.get("custom")),
      },
    });
    if (error || !data) return fail(400, { qcError: apiErrorKey(error).key });
    return {
      inlineCreated: {
        slot: String(form.get("slot") ?? "") || "move_project",
        id: data.id,
        name: data.name,
        company_id: data.company_id ?? null,
      },
    };
  },

  /**
   * Close the linked task with this contact moment (#157): sets the picked terminal status
   * and designates the interaction as the close's justification. The API validates linkage
   * and the per-status requires_interaction policy.
   */
  closeTaskWithInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const task_id = String(form.get("task_id") ?? "");
    const interaction_id = String(form.get("interaction_id") ?? "");
    const status = String(form.get("status") ?? "");
    if (!task_id || !interaction_id || !status) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).PATCH("/api/v1/tasks/{task_id}", {
      params: { path: { task_id } },
      body: { status, closing_interaction_id: interaction_id },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { ok: true };
  },

  rejectInteraction: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    if (!id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST("/api/v1/interactions/{interaction_id}/reject", {
      params: { path: { interaction_id: id } },
      body: { suppress_thread: form.get("suppress_thread") === "1" },
    });
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { ok: true };
  },

  /**
   * Bulk review (#299): approve / file / reject a whole selection.
   *
   * All three answer `bulkResult` rather than a bare `ok`, because a batch's honest answer is
   * "37 done, 3 skipped" — the API reports ineligible rows instead of rolling the good ones
   * back, and a UI that swallowed that would be claiming work it did not do.
   *
   * The link fields are only forwarded when the user actually picked one. That is the whole
   * contract difference from the single move dialog (which is prefilled, so an empty picker
   * there means "clear"): here a blank picker over a heterogeneous selection means "leave
   * every row's own link alone", and posting a bare `null` would wipe what the gmail matcher
   * already worked out on every row.
   */
  bulkApproveInteractions: (event: RequestEvent) => bulkReview(event, "approve"),
  bulkAssignInteractions: (event: RequestEvent) => bulkReview(event, "assign"),
  bulkRejectInteractions: (event: RequestEvent) => bulkReview(event, "reject"),

  /**
   * Manually glue this email onto another's conversation (#272): a reply Gmail didn't thread
   * automatically is folded onto the target the user picked. Owner-only, gmail-only — the API
   * enforces both on the row and the target.
   */
  addInteractionToConversation: async (event: RequestEvent) => {
    const form = await event.request.formData();
    const id = String(form.get("id") ?? "");
    const target_interaction_id = String(form.get("target_interaction_id") ?? "");
    if (!id || !target_interaction_id) return fail(400, { error: "errors.required" });
    const { error } = await apiFor(event).POST(
      "/api/v1/interactions/{interaction_id}/add-to-conversation",
      {
        params: { path: { interaction_id: id } },
        body: { target_interaction_id },
      },
    );
    if (error) return fail(400, { error: apiErrorKey(error).key });
    return { ok: true };
  },
};
