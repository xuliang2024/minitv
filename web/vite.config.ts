import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const page = (name: string) => fileURLToPath(new URL(`./${name}`, import.meta.url));

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      // Two pages: the React site plus a standalone legal page served at /privacy.html.
      // R2 custom domains serve exact keys, so a flat .html file is what makes the
      // privacy policy reachable without adding a URL rewrite rule.
      input: {
        main: page("index.html"),
        privacy: page("privacy.html"),
      },
    },
  },
  server: {
    port: 5174,
    proxy: { "/v1": "http://127.0.0.1:18110" },
  },
});
