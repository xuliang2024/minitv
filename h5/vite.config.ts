import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Independent of the marketing site on :5174.
// VITE_PROXY_TARGET lets a dev run read the production API through the proxy
// (same-origin, so no CORS) instead of the local uvicorn.
const target = process.env.VITE_PROXY_TARGET ?? "http://127.0.0.1:18110";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    proxy: {
      "/v1": { target, changeOrigin: true, secure: true },
    },
  },
});
