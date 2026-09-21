import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// Прокси /api → бэкенд my_agent (AGENT_API, по умолчанию 127.0.0.1:8321).
const target = process.env.AGENT_API ?? "http://127.0.0.1:8321";

export default defineConfig({
  plugins: [vue()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  server: {
    port: 5173,
    proxy: {
      "/api": { target, changeOrigin: true },
    },
  },
});
