/**
 * pentest_site.com API proxy — forwards path+query to pentest_site.com with X-Key auth.
 * Deploy: python cf_worker/deploy.py <CF_API_TOKEN>
 */
const TARGET_HOST = "pentest_site.com";

const CF_HEADERS = [
  "CF-Connecting-IP",
  "CF-IPCountry",
  "CF-Ray",
  "CF-Visitor",
  "CF-Worker",
  "CF-EW-Via",
];

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/health") {
      return new Response("ok", { headers: { "Cache-Control": "no-store" } });
    }

    const key = request.headers.get("X-Key") || url.searchParams.get("key");
    if (!env.SECRET || key !== env.SECRET) {
      return new Response(JSON.stringify({ error: { message: "forbidden" } }), {
        status: 403,
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
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

    const response = await fetch(targetUrl, {
      method: request.method,
      headers,
      body:
        request.method !== "GET" && request.method !== "HEAD"
          ? request.body
          : undefined,
      redirect: "follow",
    });

    const responseHeaders = new Headers(response.headers);
    responseHeaders.set("Cache-Control", "no-store");
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  },
};