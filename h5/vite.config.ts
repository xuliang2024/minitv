import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Independent of the marketing site on :5174. Same local API.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5175,
    proxy: { "/v1": "http://127.0.0.1:18110" },
  },
});
