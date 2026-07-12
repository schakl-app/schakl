import { error as httpError, json } from "@sveltejs/kit";

import { apiBaseUrl } from "$lib/core/api/client";

import type { RequestEvent } from "./$types";

/**
 * Streaming proxy for the AI endpoints (epic #131). The browser has no API client
 * (Golden Rule 6): it POSTs here same-origin and this route forwards the session cookie +
 * tenant hostname and pipes the API's SSE bytes straight through — the CSV-export seam,
 * with `text/event-stream` instead of a file.
 */
const UUID = "[0-9a-f-]{36}";
const ALLOWED: Record<string, RegExp[]> = {
  POST: [
    /^assist\/write$/,
    /^assistant$/,
    /^time\/parse$/,
    /^time\/reconstruct$/,
    new RegExp(`^companies/${UUID}/digest$`),
    /^reports\/generate$/,
    /^reports$/,
    /^settings\/models$/,
  ],
  GET: [/^reports$/, new RegExp(`^reports/${UUID}$`)],
  PUT: [new RegExp(`^reports/${UUID}$`)],
  DELETE: [new RegExp(`^reports/${UUID}$`)],
};

async function forward(event: RequestEvent, method: string): Promise<Response> {
  const path = event.params.path;
  if (!ALLOWED[method]?.some((re) => re.test(path))) throw httpError(404);
  const hasBody = method === "POST" || method === "PUT";
  const response = await event.fetch(`${apiBaseUrl()}/api/v1/ai/${path}${event.url.search}`, {
    method,
    body: hasBody ? await event.request.text() : undefined,
    headers: {
      ...(hasBody ? { "content-type": "application/json" } : {}),
      cookie: event.request.headers.get("cookie") ?? "",
      "x-forwarded-host": event.request.headers.get("host") ?? "",
    },
  });
  if (!response.ok) {
    // Refusals (409 budget/config, 402 license, 403) arrive as the standard envelope;
    // hand it to the client as JSON so it can translate the message key.
    let payload: unknown = {
      error: { code: "ai_provider_error", message: "errors.ai_provider_error" },
    };
    try {
      payload = await response.json();
    } catch {
      // keep the fallback envelope
    }
    return json(payload, { status: response.status });
  }
  if (response.status === 204) return new Response(null, { status: 204 });
  if (!response.body) throw httpError(502);
  const contentType = response.headers.get("content-type") ?? "application/json";
  return new Response(response.body, {
    headers: {
      "content-type": contentType,
      "cache-control": "no-store",
    },
  });
}

export const POST = (event: RequestEvent) => forward(event, "POST");
export const GET = (event: RequestEvent) => forward(event, "GET");
export const PUT = (event: RequestEvent) => forward(event, "PUT");
export const DELETE = (event: RequestEvent) => forward(event, "DELETE");
