// Backend base URL.
//
// Defaults to "/api", which is same-origin in every deployment: the Vite dev
// server proxies it (see vite.config.ts), and in the cluster the frontend's
// nginx proxies it to the backend Service (see front/nginx.conf). Staying
// same-origin means CORS never applies and there is no allowlist to maintain.
//
// window.__ENV__.BACKEND_HOST is injected at pod start by
// front/docker-entrypoint.sh and can override this with an absolute URL if the
// backend is ever exposed separately — that makes requests cross-origin, so the
// backend's CORS_ORIGINS must then list this frontend's origin.
//
// Note: an unset variable is substituted as an empty string, not undefined, so
// "??" alone would accept "" as a valid host. Treat blank as absent, and drop
// any trailing slash so `${HOST}/upload` cannot produce a double slash.
const configured: string = ((window as any).__ENV__?.BACKEND_HOST ?? "").trim();

export const HOST: string = (configured || "/api").replace(/\/+$/, "");

export const TITLE: string =
  (window as any).__ENV__?.TITLE ?? "";
