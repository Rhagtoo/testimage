/**
 * postimg.cc API proxy v2 — diagnostic edition.
 * Returns debug headers on every response for client-side analysis.
 *
 * Response headers added:
 *   X-Debug-CF-Ray         — Cloudflare ray ID from origin response
 *   X-Debug-CF-Colo        — Cloudflare colo that served this Worker request
 *   X-Debug-Cache-Status   — CF-Cache-Status from origin response (HIT/MISS/etc)
 *   X-Debug-Age            — Age header from origin (cache TTL remaining)
 *   X-Debug-Origin-Status  — HTTP status from origin (before Worker processing)
 *   X-Debug-Worker         — fixed identifier for this Worker deployment
 *
 * Deploy: python cf_worker/deploy.py <CF_API_TOKEN> --only postimg-diag
 */
const TARGET_HOST = "postimg.cc";
const WORKER_ID = "diag-v2";

const CF_HEADERS = [
  "CF-Connecting-IP",
  "CF-IPCountry",
  "CF-Ray",
  "CF-Visitor",
  "CF-Worker",
  "CF-EW-Via",
];

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // health check (no auth)
    if (url.pathname === "/health") {
      return new Response("ok", {
        headers: {
          "Cache-Control": "no-store",
          "X-Debug-Worker": WORKER_ID,
        },
      });
    }

    // debug endpoint: returns CF metadata about this request
    if (url.pathname === "/debug") {
      const cf = request.cf || {};
      return new Response(JSON.stringify({
        worker: WORKER_ID,
        cf: {
          colo: cf.colo || "unknown",
          country: cf.country || "unknown",
          city: cf.city || "unknown",
          asn: cf.asn || "unknown",
          asOrganization: cf.asOrganization || "unknown",
          tlsVersion: cf.tlsVersion || "unknown",
          httpProtocol: cf.httpProtocol || "unknown",
        },
      }, null, 2), {
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
          "X-Debug-Worker": WORKER_ID,
        },
      });
    }

    // auth check
    const key = request.headers.get("X-Key") || url.searchParams.get("key");
    if (!env.SECRET || key !== env.SECRET) {
      return new Response(JSON.stringify({ error: { message: "forbidden" } }), {
        status: 403,
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
          "X-Debug-Worker": WORKER_ID,
        },
      });
    }

    const targetUrl = `https://${TARGET_HOST}${url.pathname}${url.search}`;
    const headers = new Headers(request.headers);
    headers.set("Host", TARGET_HOST);
    for (const h of CF_HEADERS) {
      headers.delete(h);
    }
    if (!headers.has("Referer")) {
      headers.set("Referer", `https://${TARGET_HOST}/`);
    }

    const guestkey = request.headers.get("X-Guestkey") || env.GUESTKEY || "";
    if (guestkey && !headers.has("Cookie")) {
      headers.set("Cookie", `GUESTKEY=${guestkey}`);
    }

    const start = Date.now();
    const response = await fetch(targetUrl, {
      method: request.method,
      headers,
      body:
        request.method !== "GET" && request.method !== "HEAD"
          ? request.body
          : undefined,
      redirect: "follow",
    });
    const elapsed = Date.now() - start;

    // build response with diagnostic headers
    const responseHeaders = new Headers(response.headers);
    responseHeaders.set("Cache-Control", "no-store");
    responseHeaders.set("X-Debug-Worker", WORKER_ID);
    responseHeaders.set("X-Debug-Origin-Status", String(response.status));
    responseHeaders.set("X-Debug-Latency-Ms", String(elapsed));

    const cfRay = response.headers.get("CF-Ray") || response.headers.get("cf-ray") || "";
    if (cfRay) responseHeaders.set("X-Debug-CF-Ray", cfRay);

    const cacheStatus = response.headers.get("CF-Cache-Status") || response.headers.get("cf-cache-status") || "";
    if (cacheStatus) responseHeaders.set("X-Debug-Cache-Status", cacheStatus);

    const age = response.headers.get("Age") || response.headers.get("age") || "";
    if (age) responseHeaders.set("X-Debug-Age", age);

    const server = response.headers.get("Server") || response.headers.get("server") || "";
    if (server) responseHeaders.set("X-Debug-Server", server);

    const cf = request.cf || {};
    responseHeaders.set("X-Debug-CF-Colo", cf.colo || "unknown");

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  },
};