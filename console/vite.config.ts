import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev (`npm run dev`) the console proxies to a locally running stack, like nginx does in Docker.
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: { manualChunks: { react: ["react", "react-dom", "react-router-dom"], charts: ["recharts"], markdown: ["react-markdown", "remark-gfm"] } },
    },
  },
  server: {
    port: 3000,
    proxy: {
      "/api": { target: "http://localhost:8000", rewrite: (p) => p.replace(/^\/api/, "") },
      "/pulse": { target: "http://localhost:8100", rewrite: (p) => p.replace(/^\/pulse/, "") },
    },
  },
});
