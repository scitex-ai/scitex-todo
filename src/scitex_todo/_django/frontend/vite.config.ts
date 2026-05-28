import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Build the board SPA into the Django app's static dir so Django staticfiles
// serves it. Output paths match the standalone.html template
// (assets/index.js + assets/index.css).
export default defineConfig({
  plugins: [react()],
  base: "/static/scitex_todo/",
  build: {
    outDir: "../static/scitex_todo",
    emptyOutDir: true,
    sourcemap: false,
    manifest: true,
    rollupOptions: {
      output: {
        entryFileNames: "assets/index.js",
        chunkFileNames: "assets/[name].js",
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
  server: {
    port: 3001,
    proxy: {
      // Proxy API calls to the Django backend during `vite dev`.
      "/graph": "http://127.0.0.1:8051",
      "/tasks": "http://127.0.0.1:8051",
      "/ping": "http://127.0.0.1:8051",
    },
  },
});
