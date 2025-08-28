import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Vitest configuration for the BatVault frontend. It reuses the Vite React
// plugin and sets up a jsdom environment so that React Testing Library can
// render components. A global setup file is specified to extend expect with
// jest-dom matchers.
export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    // Point to the correct setup file. The tests live under src/tests,
    // so using a relative path from the project root avoids Vitest
    // trying to load a non-existent top-level tests/setup.ts file.
    setupFiles: "src/tests/setup.ts",
    css: true,
  },
});