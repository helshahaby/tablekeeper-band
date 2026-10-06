// Production counterpart of the dev proxy in vite.config.ts: /backend/* -> $BACKEND_URL/*,
// prefix stripped. The backend's /_test/* control endpoints are never forwarded.

const PREFIX = "/backend";
// Hop-by-hop and length/encoding headers are recomputed by fetch on each side.
const DROP = ["connection", "keep-alive", "transfer-encoding", "content-length", "content-encoding", "host"];

// The backend URL-decodes each path segment, so compare decoded segments (and refuse
// anything that does not decode) rather than the raw URL.
function isTestControlPath(pathname: string) {
  try {
    return pathname.split("/").some((s) => decodeURIComponent(s) === "_test");
  } catch {
    return true;
  }
}

function copyHeaders(from: Headers) {
  const headers = new Headers(from);
  DROP.forEach((h) => headers.delete(h));
  return headers;
}

/** Returns the proxied response, or null when the request is not for /backend. */
export async function proxyBackend(request: Request): Promise<Response | null> {
  const base = process.env["BACKEND_URL"];
  if (!base) return null;
  const url = new URL(request.url);
  if (url.pathname !== PREFIX && !url.pathname.startsWith(PREFIX + "/")) return null;

  const path = url.pathname.slice(PREFIX.length) || "/";
  if (isTestControlPath(path)) return new Response("Not Found", { status: 404 });

  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  let upstream: Response;
  try {
    upstream = await fetch(base.replace(/\/$/, "") + path + url.search, {
      method: request.method,
      headers: copyHeaders(request.headers),
      body: hasBody ? await request.arrayBuffer() : undefined,
      redirect: "manual",
    });
  } catch {
    return new Response("Backend unavailable", { status: 502 });
  }
  return new Response(upstream.body, { status: upstream.status, headers: copyHeaders(upstream.headers) });
}
