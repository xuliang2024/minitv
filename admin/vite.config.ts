import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/admin/v1": "http://127.0.0.1:18110",
      "/v1": "http://127.0.0.1:18110",
    },
  },
});
