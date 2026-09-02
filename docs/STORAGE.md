# File storage — one object per distinct content

> How stored bytes are addressed, shared and reclaimed. Read this before writing anything
> that stores or deletes a file. For *where* the bytes live (volume vs S3, backups, env
> vars) see `docs/DEPLOY.md`.

## The one rule

**A file row is not its bytes.** `files` says what a file *is to its entity* — its name, its
type, who uploaded it, what it hangs off. `file_blobs` says what the bytes *are* — one row per
distinct sha256, per org. Many files may point at one blob, so **no single file row may ever
delete an object**.

If you are about to write `storage_for(row.backend).delete(row.storage_key)`, stop and call
`app.core.storage.service.drop_file` instead. It is the only correct answer, and it is the
reason that function is module-level rather than a `FileService` method: three surfaces store
files for a person — the generic upload, a client's logo (`companies`), an HR dossier document
(`hr`) — and each is gated on its *own* permission, so they cannot all go through
`FileService`. They can, and do, all go through `write_file` / `drop_file`.

## Why

An e-mail signature logo arriving on 500 messages used to be 500 objects, because a `files`
row minted its own key (`<org_id>/<file_id>`) and always wrote the bytes. The same holds for
the PDF a client sends twice, the price list forwarded around a thread, and a logo re-uploaded
after a crop that changed nothing. This is the pathology object storage bills for.

## Per org, never across orgs

The uniqueness key is `(org_id, backend, sha256)` and every key stays under `<org_id>/`.
Cross-org de-duplication would save more and break Golden Rule 1: terminating a tenant deletes
its whole key prefix (`delete_prefix`), which would take another tenant's bytes with it, and
one org's data would be reachable from another's key space. The duplicates are inside one
agency's mailbox anyway — that is where the saving actually is.

`backend` is part of the key too: an instance mid-migration from the volume to S3 holds the
same bytes in both, and those are two different objects.

## Writing

`app.core.storage.blobs.reserve` is one statement, and it is the lock:

```sql
INSERT INTO file_blobs (…) VALUES (…, '<org_id>/sha256/<hex>', …)
ON CONFLICT (org_id, backend, sha256) DO UPDATE SET unreferenced_since = NULL
RETURNING id, storage_key, (xmax = 0) AS inserted
```

It answers *where the bytes belong* and *whether you must write them* in one round trip, while
holding the row lock the sweeper contends for. Two writers storing identical bytes at the same
instant serialize on it and exactly one is told to upload. `inserted` false is a
de-duplication hit: **skip the put entirely** — on S3 that removes a whole upload round trip
from the request, not just the object.

New keys are content-addressed (`<org_id>/sha256/<hex>`), so even if two writers were somehow
both told to upload they write identical bytes to the same key.

Blocking IO goes off the event loop, and an S3 put runs inside `ctx.release_db()`
(`docs/PERFORMANCE.md`). Because that *commits*, a put that raises must drop its reservation
(`blobs.release`) — otherwise the next write de-duplicates onto a blob with no bytes.

## Reading

**Reads never touch `file_blobs`.** `files.backend` and `files.storage_key` are mirrored from
the blob on every write, so serving a file is the same single row read it always was — no
join, no second query — and a rolled-back release still finds every file from the row alone.

## Deleting, and the maintenance cron

Deleting a file deletes the **row**. The bytes stay, because another file may hold the same
content and only a whole-table view can tell. That also means a delete makes no external call
on the request path at all.

`app.core.storage.jobs.storage_maintenance` (daily, per org via `run_per_org`) does the rest:

- **Fold** — the retroactive half. De-duplication only helps writes made after it shipped, so
  this hashes pre-de-duplication rows (`blob_id IS NULL`) in batches of
  `SCHAKL_STORAGE_FOLD_BATCH` (500) and collapses identical content. The first row of a digest
  **adopts its own existing key**: the blob takes over the key that file already had, so
  folding a terabyte costs a read per object and no writes. Only the redundant copies are
  deleted. Deliberately not a migration — a self-hosted instance migrates itself unattended on
  upgrade (`docs/WORKFLOW.md`), and hashing every object an agency has ever stored is unbounded
  work. A batch a night converges within days.
- **Sweep** — the collector, and the only thing that reclaims space. Two passes: the first
  sighting of an unreferenced blob only *stamps* `unreferenced_since`, and the bytes go
  `SCHAKL_STORAGE_BLOB_GRACE_HOURS` (24) later. So a blob is never collected in the same breath
  as the delete that unreferenced it, and "I deleted the wrong file" is recoverable by
  inserting a row rather than by restoring a backup.

The sweep holds the blob's row lock **across** the byte delete. A background job may — the
`release_db` rule is about not pinning a *request's* pooled connection — and it is what closes
the race: a concurrent write either wins the lock and clears the stamp (the sweep re-reads it
and skips), or waits, finds the row gone, and reserves a fresh blob it is then told to upload.

## Attachments on a record: images, the JSON upload, and the client's view

The generic upload (`POST /files?entity_type=…&entity_id=…`) hangs a file off a **task, a
project or a client**, and the first year of it taught four things (the image-attachment
research task, where a screenshot on a task took three steps through Drive).

**A browser uploads multipart; nothing else can.** The generated MCP tools and every JSON-only
automation send a JSON document, and a `multipart/form-data` route answers that with
`422 file: field required` however the bytes were meant. So `POST /files/inline` carries the same
upload as base64 inside JSON — same guardrails, same de-duplication, same activity line — and the
multipart routes are **off the MCP surface by method** (`core/mcp/server.py`, `_ROUTE_MAPS`): a
tool that can only refuse is worse than no tool (#253). The size ceiling is checked on the
*encoded* length before the decode, the "check the cap before the work it bounds" rule §17
already states for the import parser.

**An image is shown, not spelled out.** `GET /files/{id}/thumbnail?size=160|480|1200` scales a
raster down (long edge, aspect kept, EXIF orientation honoured, alpha kept as PNG, opaque as
JPEG) on demand and caches it by ETag exactly like the original. A closed size set, like the app
icons — this is a preview, never a general resizing proxy — and a file that is not a raster
(a PDF, an SVG, a decode failure) answers the original bytes so an `<img>` still draws
something. The **original is kept untouched**: a screenshot is evidence, and re-encoding at
upload would trade a few megabytes on the volume for a fact nobody can get back. Compression is
the thumbnail's job, and 10 MB stays the ceiling.

**A client reads an attachment only when the agency says so.** `files.client_visible` is a
per-file bit, off by default, mirrored on `Task.visible_to_client` one level down: a client who
may see the task must not thereby see every screenshot the team pinned to it. It is applied on
**every path** — the list, the bytes and the thumbnail — for a portal login
(`ctx.is_portal`, #274) on the three attachment hosts (`PORTAL_GATED_ENTITY_TYPES`), and on
nothing else: a report's PDF is gated by `reporting`, an avatar by nobody, and a closed set is
what keeps this from ever meaning "everything but". The task page used to hide the strip with
`!isPortal` while the API served the files to anyone who could see the task — the shape
docs/UX.md warns about, and the reason the bit lives in the API. Flipping it is
`PATCH /files/{id}` on `files.file.write`, and the record's trail says who showed the client
what. The migration is additive (`NOT NULL DEFAULT false`), so an upgrade hides every existing
attachment from the portal rather than guessing.

**An image in the words is body content, not an attachment.** `inline=true` on either upload
envelope stores the file with `content_id` (the e-mail `cid:` shape): the strip's default list
excludes it, and the text carries `![alt](file:<id> =50%)` — the one image marker the web
renders, which by construction can only name this instance's own store (`richtext/images.ts`
holds the grammar, shared by the editor's serializer and the renderer's tokenizer). The portal
rule follows the **words, not the eye**: a body file on an attachment host is served exactly
when the record that embeds it is visible to that login (`FileService.portal_may_read_serving`,
through `entity_visible` and the model's own `__portal_horizon_clause__`) — an image pasted
into a client-visible task's description is part of what the client already reads, while the
per-file `client_visible` bit keeps gating attachments. Deleting the marker from the text
leaves the row behind, like an e-mail's `cid:` parts; the rows go with the record's org and the
blob sweeper reclaims bytes nothing references.

**Documents are a core panel on the company hub** (`core/storage/panels.py`,
`files.documents`), beside the activity trail, for the same reason: storing a file against a
record is a platform capability. Tasks and projects keep their own strip; all three post through
one set of host actions (`$lib/core/files/actions.server`).

## There is no refcount column

The `files` rows *are* the reference count. A maintained counter would drift under an org
import, an archive restore or a cascade delete, and a drifted counter deletes bytes that are
still in use. The sweeper asks the question directly (`NOT EXISTS`) and asks it twice — once
to find candidates, once under the lock.

## Portability

An org archive carries `files` rows and their bytes, never `file_blobs`: those are bookkeeping
about *where this instance keeps bytes*, and their keys point into the source org's key space.
Imported files therefore land un-folded (`blob_id IS NULL`, one object each) and the
maintenance cron re-derives the blobs on the receiving side. That is also the only way two
archives that happen to share a logo ever converge.

## Upgrade and rollback

The migration (`c4a7e18b3d90`) is **expand-only**: nothing is dropped, nothing is backfilled,
and every existing row keeps `blob_id IS NULL`, its own object, and its original read/delete
behaviour until the fold job reaches it.

Rolling the image tag back **reads** fine — `files.storage_key` is populated on every row.

**The rollback caveat, and it is the one thing this cannot make safe:** the previous release's
delete path removes the object at `files.storage_key` unconditionally, so deleting a *shared*
file there also deletes it for its siblings. If you roll back, do not delete files until you
have rolled forward again. Downgrading the migration itself destroys no bytes — it drops the
column and the table, leaving some objects orphaned rather than missing. Orphaned space is
recoverable; missing bytes are not.
