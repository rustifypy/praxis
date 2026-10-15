import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    // Into the Python package: uv_build ships only src/analyst_agent.
    outDir: "../analyst_agent/api/static",
    emptyOutDir: true,
    // The data page's chunk carries the Cube UI kit (~1 MB) and loads only when that page opens.
    // Splitting the kit with manualChunks drags React into it and makes the entry load it eagerly.
    chunkSizeWarningLimit: 1000,
  },
  // `npm run dev` hot-reloads against a running `analyst`.
  server: { proxy: { "/api": "http://127.0.0.1:8420" } },
});
