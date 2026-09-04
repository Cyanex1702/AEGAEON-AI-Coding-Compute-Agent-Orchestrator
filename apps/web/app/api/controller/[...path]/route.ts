const controllerUrl = process.env.AEGAEON_CONTROLLER_URL ?? "http://127.0.0.1:8000";
const controlToken = process.env.AEGAEON_CONTROL_TOKEN ?? "";

type ProxyContext = {
  params: Promise<{ path: string[] }>;
};

async function forward(request: Request, context: ProxyContext) {
  const { path } = await context.params;
  const incoming = new URL(request.url);
  const upstream = new URL("/" + path.map(encodeURIComponent).join("/"), controllerUrl);
  upstream.search = incoming.search;

  const headers = new Headers(request.headers);
  headers.delete("connection");
  headers.delete("content-length");
  headers.delete("host");
  if (controlToken) headers.set("authorization", "Bearer " + controlToken);

  const body =
    request.method === "GET" || request.method === "HEAD"
      ? undefined
      : await request.arrayBuffer();
  const response = await fetch(upstream, {
    method: request.method,
    headers,
    body,
    cache: "no-store",
    redirect: "manual",
    signal: AbortSignal.timeout(30_000),
  });
  const responseHeaders = new Headers(response.headers);
  responseHeaders.delete("content-encoding");
  responseHeaders.delete("content-length");
  responseHeaders.set("cache-control", "no-store");
  return new Response(await response.arrayBuffer(), {
    status: response.status,
    statusText: response.statusText,
    headers: responseHeaders,
  });
}

export const GET = forward;
export const HEAD = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
