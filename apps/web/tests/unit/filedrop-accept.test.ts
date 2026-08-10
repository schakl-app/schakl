/**
 * The `accept` filter behind `use:filedrop` (docs/UX.md, the upload drag-and-drop convention).
 *
 * This is the one thing a drop has to do that a click never did: the file dialog was already
 * narrowed by `accept`, and a desktop is not. Every case below is a real one of the app's
 * accept strings — the `.eml` import, whose files are typed `message/rfc822` by some mail
 * clients and not at all by others; the image fields, which list mime types only; and the
 * spreadsheet import, which lists extensions *and* mime types because `.xlsx` is a zip to
 * anything that guesses from bytes.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { acceptsFile } from "../../src/lib/core/ui/filedrop.ts";

const EML = ".eml,message/rfc822";
const IMAGE = "image/png,image/jpeg,image/webp,image/gif";
const SHEET =
  ".csv,.tsv,.txt,.xlsx,text/csv,text/tab-separated-values,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

describe("acceptsFile", () => {
  test("an empty accept takes anything — the HR document upload lists none", () => {
    assert.equal(acceptsFile({ name: "contract.pdf", type: "application/pdf" }, ""), true);
  });

  test("an extension token matches on the name, whatever the case", () => {
    assert.equal(acceptsFile({ name: "Re: offerte.EML", type: "" }, EML), true);
    assert.equal(acceptsFile({ name: "list.CSV", type: "" }, SHEET), true);
  });

  test("a mime token matches exactly", () => {
    assert.equal(acceptsFile({ name: "logo.png", type: "image/png" }, IMAGE), true);
    assert.equal(acceptsFile({ name: "scan.tiff", type: "image/tiff" }, IMAGE), false);
  });

  test("a wildcard token matches its whole type", () => {
    assert.equal(acceptsFile({ name: "cover.avif", type: "image/avif" }, "image/*"), true);
    assert.equal(acceptsFile({ name: "notes.pdf", type: "application/pdf" }, "image/*"), false);
  });

  test("a plainly wrong file is refused", () => {
    assert.equal(acceptsFile({ name: "logo.png", type: "image/png" }, EML), false);
    assert.equal(
      acceptsFile({ name: "sheet.xlsx", type: "application/vnd.ms-excel" }, EML),
      false,
      "a typed file the accept list does not name is refused on its type, not waved through",
    );
  });

  test("an untypeable file goes through for the server to judge", () => {
    // The native dialog's "All files" escape hatch does the same, and every one of these
    // uploads is validated server-side regardless. Refusing here would block a `.eml` saved by
    // a client that gives it no type at all — the exact file this screen exists for.
    assert.equal(acceptsFile({ name: "message", type: "" }, EML), true);
    assert.equal(acceptsFile({ name: "logo", type: "" }, IMAGE), true);
  });

  test("the xlsx a browser types as a zip still lands, on its extension", () => {
    assert.equal(
      acceptsFile({ name: "klanten.xlsx", type: "application/zip" }, SHEET),
      true,
      "extension and mime are alternatives, not both required",
    );
  });
});
