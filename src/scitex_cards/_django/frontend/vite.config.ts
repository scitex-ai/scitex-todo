import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Build the board SPA into the Django app's static dir so Django staticfiles
// serves it. Output paths match the standalone.html template
// (assets/index.js + assets/index.css).
//
// IMPORTANT — bundle/template food (board v0.5.4 fix, PR #105): the previous
// config wrote into `../static/scitex_cards` with `emptyOutDir: true`, which
// wiped the SIBLINGS of `assets/` on every rebuild — favicon.svg,
// `board_v3/*.css`, and `board_v3/searchQuery.js`/`searchSuggest.js` are all
// tracked-in-git static assets consumed by the live `board_v3.html` template.
// A vite rebuild during PR #104 took those out from under board_v3 → cards
// rendered as empty pills (no card.css), search broke (no searchQuery.js),
// favicon 404. We now scope the outDir to the `assets/` subdir + drop the
// `manifest: true` (Django doesn't read it) so a rebuild only ever touches
// the React SPA bundle and never the board_v3 statics.
export default defineConfig({
  plugins: [react()],
  base: "/static/scitex_cards/",
  build: {
    outDir: "../static/scitex_cards/assets",
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      output: {
        entryFileNames: "index.js",
        chunkFileNames: "[name].js",
        assetFileNames: "[name][extname]",
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
