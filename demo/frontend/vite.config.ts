// @lovable.dev/vite-tanstack-config already includes the following — do NOT add them manually
// or the app will break with duplicate plugins:
//   - TanStack devtools (dev-only, first), tanstackStart, viteReact, tailwindcss, tsConfigPaths,
//     nitro (build-only using cloudflare as a default target), VITE_* env injection, @ path alias,
//     React/TanStack dedupe, error logger plugins, and sandbox detection (port/host/strictPort).
// You can pass additional config via defineConfig({ vite: { ... }, etc... }) if needed.
import { defineConfig } from "@lovable.dev/vite-tanstack-config";

// The backend URL-decodes each path segment, so compare decoded segments (and refuse
// anything that does not decode) rather than the raw URL.
function isTestControlPath(url: string) {
  try {
    return (url.split("?")[0] ?? "").split("/").some((s) => decodeURIComponent(s) === "_test");
  } catch {
    return true;
  }
}

export default defineConfig({
  tanstackStart: {
    // Redirect TanStack Start's bundled server entry to src/server.ts (our SSR error wrapper).
    // nitro/vite builds from this
    server: { entry: "server" },
  },
  vite: {
    server: {
      // 8080 is taken by the Tablekeeper backend locally (the Lovable sandbox still forces its own port).
      port: 5173,
      strictPort: true,
      proxy: {
        // Dev-only: /backend/* -> Tablekeeper backend, prefix stripped. The /_test/* control
        // endpoints are never forwarded, so the customer UI cannot reset or import state.
        "/backend": {
          target: "http://127.0.0.1:8080",
          rewrite: (path) => path.replace(/^\/backend/, ""),
          bypass: (req) => (isTestControlPath(req.url ?? "") ? false : undefined),
        },
      },
    },
  },
});
