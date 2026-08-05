/**
 * Container liveness for the SSR server. Deliberately dependency-free.
 *
 * Its whole job is to answer "is this Node process listening yet?", which is the question an
 * orchestrator asks before it puts a new task into rotation. Without it, Swarm considers a
 * `start-first` web task eligible for traffic the moment the container is *running* — a second
 * or two before the server actually binds — so every deploy leaked a handful of 502s.
 *
 * It must NOT call the API. Web being able to serve is a different question from the API being
 * up: a probe that fetched /meta/tenant would pull every web replica out of rotation during an
 * API restart, turning a brief API blip into a total outage. `/health/ready` on the API answers
 * the dependency question, for the API's own probe.
 *
 * Not a `+page` and outside `(app)`, so it inherits no layout load and no auth guard.
 */
import type { RequestHandler } from "./$types";

export const prerender = false;

export const GET: RequestHandler = () =>
  new Response("ok\n", {
    status: 200,
    headers: { "content-type": "text/plain", "cache-control": "no-store" },
  });
