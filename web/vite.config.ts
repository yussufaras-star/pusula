import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

import { mockApi } from "./mock/api";

// `npm run dev`      → /api isteklerini yerel FastAPI'ye (8000) yönlendirir.
// `npm run dev:mock` → veritabanı olmadan örnek veriyle çalışır (yalnız geliştirme).
function mockPlugin(): Plugin {
  return {
    name: "pusula-mock-api",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const body = req.url ? mockApi(req.method ?? "GET", req.url) : undefined;
        if (body === undefined) return next();
        res.setHeader("Content-Type", "application/json");
        res.end(JSON.stringify(body));
      });
    },
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), ...(mode === "mock" ? [mockPlugin()] : [])],
  build: { outDir: "../public", emptyOutDir: true },
  server: mode === "mock" ? {} : { proxy: { "/api": "http://127.0.0.1:8000" } },
}));
