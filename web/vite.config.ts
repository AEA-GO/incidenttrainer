/// <reference types="vitest/config" />
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` talks to the real site (API + gateway), so a laptop needs no backend.
// Override with ITR_SITE=https://... if the site moves.
const site = process.env.ITR_SITE ?? "https://drer7le45ultl.cloudfront.net";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/api": { target: site, changeOrigin: true },
      "/s": { target: site, changeOrigin: true, ws: true },
    },
  },
  build: { sourcemap: false },
  test: { environment: "jsdom", include: ["src/**/*.test.{ts,tsx}"] },
});
