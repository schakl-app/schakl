/**
 * The discussed topics as one markdown document, and back.
 *
 * The minutes store topics as a list (`{heading, text}`) because the document, the contact
 * moment and the AI box address them one at a time; the review screen edits them as a single
 * field, because that is how a person writes a list of topics — a heading, the words under it,
 * the next heading. A heading is a line starting with `#`–`###` (the editor's heading button
 * writes `###`); anything above the first heading becomes a topic under `fallbackHeading`, so
 * nothing typed there is ever lost. A heading line inside a fenced code block is text.
 */

export interface Topic {
  heading: string;
  text: string;
}

const HEADING = /^#{1,3}\s+(.+?)\s*#*\s*$/;
const FENCE = /^\s*(```|~~~)/;

export function topicsMarkdown(topics: Topic[]): string {
  return topics
    .filter((topic) => topic.heading.trim() || topic.text.trim())
    .map((topic) => `### ${topic.heading.trim()}\n\n${topic.text.trim()}`)
    .join("\n\n");
}

export function parseTopics(markdown: string, fallbackHeading: string): Topic[] {
  const topics: Topic[] = [];
  let heading: string | null = null;
  let lines: string[] = [];
  let fenced = false;

  const flush = () => {
    const text = lines.join("\n").trim();
    if (heading !== null || text) {
      topics.push({ heading: heading ?? fallbackHeading, text });
    }
    lines = [];
  };

  for (const line of markdown.split("\n")) {
    if (FENCE.test(line)) fenced = !fenced;
    const match = fenced ? null : HEADING.exec(line);
    if (match) {
      flush();
      heading = match[1].trim();
    } else {
      lines.push(line);
    }
  }
  flush();
  return topics;
}
