import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      // Forward API calls to the Flask backend during development,
      // so the frontend can call /api/v1/* without CORS juggling.
      "/api": {
        target: "http://localhost:5000",
        changeOrigin: true,
      },
      // Live attendance Socket.IO (phase 1.2b). ws:true upgrades the
      // handshake, so the panel talks WebSocket same-origin in dev.
      "/socket.io": {
        target: "http://127.0.0.1:5000",
        changeOrigin: true,
        ws: true,
      },
    },
  },
});
