import { defineConfig } from "vite";
import solid from "vite-plugin-solid";

const appBasePath = normalizeBasePath(process.env.VITE_APP_BASE_PATH || "/v2/");

export default defineConfig({
  plugins: [solid()],
  base: appBasePath,
  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
    watch: {
      usePolling: true,
      interval: 250,
    },
    hmr: {
      clientPort: 2026,
      host: "localhost",
      path: `${appBasePath}/__vite_ws`,
      protocol: "ws",
    },
  },
  preview: {
    host: "0.0.0.0",
    port: 4173,
    strictPort: true,
  },
});

function normalizeBasePath(path: string) {
  const withLeadingSlash = path.startsWith("/") ? path : `/${path}`;
  return withLeadingSlash.endsWith("/") ? withLeadingSlash : `${withLeadingSlash}/`;
}
