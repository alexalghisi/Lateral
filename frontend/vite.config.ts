import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// During `npm run dev`, the browser talks to Vite on :5173. We proxy the API
// and health surface to the full stack (Nginx on :8080 by default) so the app
// always uses same-origin relative URLs — there is no CORS to configure, in
// development or in production, where Nginx serves the built assets directly.
const API_TARGET = process.env.VITE_API_TARGET ?? "http://localhost:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: API_TARGET, changeOrigin: true },
      "/health": { target: API_TARGET, changeOrigin: true },
    },
  },
});
