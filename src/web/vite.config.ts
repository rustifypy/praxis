import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The build output lands inside the Python package, not here. uv_build only ships
// src/analyst_agent, so anything left in src/web would be missing from a wheel and
// `analyst` would serve a 503. Source stays separate; the artifact crosses over.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../analyst_agent/api/static",
    emptyOutDir: true,
  },
  server: {
    // `npm run dev` for hot reload, with the API still coming from `analyst`.
    proxy: {
      "/api": "http://127.0.0.1:8420",
    },
  },
});
