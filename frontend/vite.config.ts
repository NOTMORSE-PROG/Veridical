import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    // BUG-003: pin this explicitly rather than inheriting whatever
    // frontend/.env (gitignored) happens to hold on the machine running
    // the suite. Every test using stubFetchByPath() matches fetch calls
    // by BARE pathname ("/check-runs/5"); CI has no .env file, so without
    // this override the suite ran against api/client.ts's real default
    // ("/api"), silently 404-ing nearly every stubbed request — a real
    // regression only CI caught (local runs had .env's explicit
    // http://localhost:8000 masking it). Tests that need a different
    // value stub it themselves (see src/api/client.test.ts).
    env: { VITE_API_BASE_URL: "" },
  },
});
