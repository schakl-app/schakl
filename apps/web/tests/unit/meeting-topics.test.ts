/**
 * The discussed topics are edited as one markdown field and stored as a list
 * (`$lib/modules/meetings/topics.ts`). The round trip must never lose words: text above the
 * first heading, a heading inside a code block, and an empty field are the cases that would.
 *
 * Run with `pnpm web test:unit`.
 */
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { parseTopics, topicsMarkdown } from "../../src/lib/modules/meetings/topics.ts";

describe("meeting topics as one field", () => {
  test("round-trips a list through markdown", () => {
    const topics = [
      { heading: "Planning", text: "Live op **3 oktober**." },
      { heading: "Content", text: "- Teksten\n- Logo" },
    ];
    assert.deepEqual(parseTopics(topicsMarkdown(topics), "Algemeen"), topics);
  });

  test("text above the first heading is kept under the fallback heading", () => {
    assert.deepEqual(parseTopics("Losse notitie\n\n### Planning\nVrijdag", "Algemeen"), [
      { heading: "Algemeen", text: "Losse notitie" },
      { heading: "Planning", text: "Vrijdag" },
    ]);
  });

  test("a heading line inside a code block is text", () => {
    const parsed = parseTopics("### Code\n```\n# niet een kop\n```", "Algemeen");
    assert.equal(parsed.length, 1);
    assert.match(parsed[0].text, /# niet een kop/);
  });

  test("an empty field is no topics", () => {
    assert.deepEqual(parseTopics("  \n", "Algemeen"), []);
  });
});
