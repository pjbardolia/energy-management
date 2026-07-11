import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Requests to /api/... in dev are forwarded to the cloud API.
      // The /api prefix is stripped before forwarding so the backend
      // sees the same paths it always does (e.g. /api/login → /login).
      "/api": {
        target: "http://165.22.247.235:8001",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
});
