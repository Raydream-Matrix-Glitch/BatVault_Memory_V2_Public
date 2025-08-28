import { defineConfig } from '@playwright/test';

// Playwright configuration for end-to-end tests. It points to the e2e
// directory and sets a sensible timeout and viewport. The baseURL points
// to the Vite dev server; adjust if running on a different port.
export default defineConfig({
  testDir: './e2e',
  timeout: 30000,
  use: {
    baseURL: 'http://localhost:5173',
    headless: true,
    viewport: { width: 1280, height: 720 },
  },

  webServer: {
    command: 'npm run dev',
    url: 'http://localhost:5173',
    reuseExistingServer: true,
    timeout: 120000,
  },
});