import { test, expect } from '@playwright/test';

test.describe('Memory page E2E', () => {
  test('loads and shows the interface', async ({ page }) => {
    await page.goto('/memory');
    await expect(page.locator('text=BatVault Memory Interface')).toBeVisible();
  });

  // Additional e2e tests could intercept network requests and mock streaming
  // responses to verify full flows (query submission, streaming tokens,
  // evidence rendering, audit drawer interactions). For brevity, only
  // navigation is tested here.
});