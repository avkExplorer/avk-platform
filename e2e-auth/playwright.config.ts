import { defineConfig } from '@playwright/test';

// Runs against the stg Keycloak (http://127.0.0.1:4810) and Mailpit (http://127.0.0.1:4811) started by `avk stg up`.
// `avk auth test --env stg` sets the environment variables below; never put real credentials in this folder.
export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  retries: 0,
  workers: 1,                       // the tests share one Mailpit inbox and one realm
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: process.env.KC_URL ?? 'http://127.0.0.1:4810',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
});
