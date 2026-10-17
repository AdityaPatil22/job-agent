import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";

const backendOrigin = "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: backendOrigin,
        changeOrigin: true,
        configure(proxy) {
          // The Python backend allows only same-origin or localhost requests.
          // Rewrite the development-server origin only on this local proxy hop.
          proxy.on("proxyReq", (proxyReq, request) => {
            if (request.headers.origin) {
              proxyReq.setHeader("origin", backendOrigin);
            }
          });
        },
      },
    },
  },
  build: {
    outDir: fileURLToPath(new URL("../src/job_agent/static/react-build", import.meta.url)),
    emptyOutDir: true,
    assetsDir: "assets",
  },
});
