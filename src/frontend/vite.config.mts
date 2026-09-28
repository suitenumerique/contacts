import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import { visualizer } from "rollup-plugin-visualizer";
import path from "node:path";

export default defineConfig({
  plugins: [
    // See ./tsr.config.json for tanstackRouter config
    tanstackRouter(),
    react(),
    // Opt-in bundle analyzer: emits bundle-stats.html next to the project
    // root when ANALYZE=1. Run via `pnpm run analyze`.
    process.env.ANALYZE === "1" &&
      (visualizer({
        filename: "bundle-stats.html",
        gzipSize: true,
      }) as Plugin),
  ].filter(Boolean) as Plugin[],
  server: {
    host: "0.0.0.0",
    port: 8970,
    strictPort: true,
  },
  preview: {
    host: "0.0.0.0",
    port: 8970,
    strictPort: true,
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  build: {
    outDir: "dist",
    sourcemap: false,
  },
});
