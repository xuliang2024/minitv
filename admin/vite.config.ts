import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ command }) => ({
  // admin.minitv.us rewrites only "/" to the R2 object /admin/index.html.
  // Built assets must therefore live under /admin/ as well.
  // Dev stays at "/" so http://127.0.0.1:5173/dramas works; Vite otherwise
  // rejects any path outside the public base and prints the error the user hit.
  base: command === "build" ? "/admin/" : "/",
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/admin/v1": "http://127.0.0.1:18110",
      "/v1": "http://127.0.0.1:18110",
    },
  },
}));
