/**
 * Browser-side SSE reader for the AI endpoints (epic #131).
 *
 * The browser never talks to the API directly (Golden Rule 6): it POSTs to the same-origin
 * `/ai/<path>` proxy, which forwards the session and pipes the API's `text/event-stream`
 * back untouched. This parser reads that stream incrementally so tokens render as they
 * arrive, and an AbortSignal makes generation interruptible (#127).
 */
import type { AISource } from "./index";

export interface AIStreamHandlers {
  onText?: (delta: string) => void;
  /** A quiet status line while a tool runs ("zoekt in uren…"), never silence (#127). */
  onTool?: (name: string) => void;
  onSources?: (sources: AISource[]) => void;
  /** A mid-stream failure; `message` is an i18n key for `t()`. */
  onError?: (code: string, message: string) => void;
}

export interface AIStreamFailure {
  status: number;
  /** i18n key from the API's error envelope. */
  message: string;
  code: string;
}

/**
 * POST `body` to `/ai/<path>` and dispatch its SSE events. Resolves when the stream ends;
 * returns the failure when the request was refused before streaming (409 budget /
 * not-configured, 402 license, 403), so callers can render the envelope's message key.
 */
export async function streamAI(
  path: string,
  body: unknown,
  handlers: AIStreamHandlers,
  signal?: AbortSignal,
): Promise<AIStreamFailure | null> {
  const response = await fetch(`/ai/${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok || !response.body) {
    let message = "errors.ai_provider_error";
    let code = "ai_provider_error";
    try {
      const payload = await response.json();
      message = payload?.error?.message ?? message;
      code = payload?.error?.code ?? code;
    } catch {
      // non-JSON failure body — keep the fallback key
    }
    return { status: response.status, message, code };
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "message";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl: number;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl).replace(/\r$/, "");
        buffer = buffer.slice(nl + 1);
        if (line.startsWith("event:")) {
          event = line.slice(6).trim();
        } else if (line.startsWith("data:")) {
          dispatch(event, line.slice(5).trim(), handlers);
          event = "message";
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
  return null;
}

function dispatch(event: string, raw: string, handlers: AIStreamHandlers): void {
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(raw);
  } catch {
    return;
  }
  switch (event) {
    case "text":
      handlers.onText?.(String(data.text ?? ""));
      break;
    case "tool":
      handlers.onTool?.(String(data.name ?? ""));
      break;
    case "sources":
      handlers.onSources?.((data.sources as AISource[]) ?? []);
      break;
    case "error":
      handlers.onError?.(
        String(data.code ?? "ai_provider_error"),
        String(data.message ?? "errors.ai_provider_error"),
      );
      break;
  }
}
