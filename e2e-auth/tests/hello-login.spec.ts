import { expect, test, type Page } from '@playwright/test';
import { GOOD_PASSWORD, KC, clearMail, createUser, deleteUserByEmail, freshTotp, uniqueEmail } from './helpers';

// The pilot app (hello) wired to the shared login, in the stg stack: http://127.0.0.1:4820 -> Keycloak :4810 -> back.
const HELLO = process.env.HELLO_URL ?? 'http://127.0.0.1:4820';

const created: string[] = [];
const track = (email: string) => { created.push(email); return email; };
test.afterAll(async () => { for (const e of created) await deleteUserByEmail(e); });
test.beforeEach(async () => { await clearMail(); });

/** Go to a hello page; if the login is needed, sign in at Keycloak and land back on that page. */
async function open(page: Page, path: string, email: string, password = GOOD_PASSWORD) {
  await page.goto(HELLO + path);
  if (page.url().startsWith(KC)) {
    await page.locator('#username').fill(email);
    await page.locator('#password').fill(password);
    await page.locator('#kc-login').click();
  }
}

async function meOf(page: Page) {
  const response = await page.request.get(`${HELLO}/api/v1/me`);
  expect(response.status()).toBe(200);
  return response.json() as Promise<{ email: string; roles: string[]; subject: string }>;
}

test('public pages work without a login', async ({ page }) => {
  expect((await page.request.get(`${HELLO}/`)).status()).toBe(200);
  const ping = await page.request.get(`${HELLO}/api/v1/ping`);
  expect(await ping.json()).toEqual({ message: 'pong' });
});

test('an anonymous visitor is sent to the login, and protected pages are not served', async ({ page }) => {
  await page.goto(`${HELLO}/admin`);
  await expect(page).toHaveURL(new RegExp(`^${KC}/realms/avk/`));
  await expect(page.locator('.avk-brand')).toBeVisible();
  const direct = await page.request.get(`${HELLO}/api/v1/me`, { maxRedirects: 0 });
  expect(direct.status()).toBe(302);                         // never a page or JSON without a session
});

test('log in through hello: back on hello, the profile knows the person and their role', async ({ page }) => {
  const email = track(uniqueEmail('hello'));
  await createUser(email);
  await open(page, '/profile', email);
  await expect(page).toHaveURL(/^http:\/\/127\.0\.0\.1:\d+\/profile(\?continue)?$/);
  await expect(page.locator('body')).toContainText(email);
  const me = await meOf(page);
  expect(me.email).toBe(email);
  expect(me.roles).toEqual(['ROLE_USER']);                   // roles come from the realm roles in the ID token
});

test('the session cookie is HttpOnly and SameSite=Lax', async ({ page, context }) => {
  const email = track(uniqueEmail('cookie'));
  await createUser(email);
  await open(page, '/profile', email);
  const session = (await context.cookies(HELLO)).find((c) => c.name === 'JSESSIONID');
  expect(session, 'session cookie').toBeTruthy();
  expect(session!.httpOnly).toBe(true);
  expect(session!.sameSite).toBe('Lax');                     // Secure is on in prod; stg is plain http on 127.0.0.1
});

test('a normal user gets 403 on /admin by direct URL', async ({ page }) => {
  const email = track(uniqueEmail('noadmin'));
  await createUser(email);
  await open(page, '/profile', email);
  const response = await page.request.get(`${HELLO}/admin`);
  expect(response.status()).toBe(403);
  const sub = await page.request.get(`${HELLO}/admin/anything`);
  expect(sub.status()).toBe(403);
});

test('an admin must enrol OTP, then reaches /admin', async ({ page }) => {
  const email = track(uniqueEmail('hadmin'));
  await createUser(email, { admin: true });
  await open(page, '/admin', email);
  await expect(page.locator('body')).toContainText(/authenticator|mobile/i);          // forced enrolment on the way in
  await page.getByRole('link', { name: /unable to scan/i }).click();
  const secret = (await page.locator('#kc-totp-secret-key').innerText()).replace(/\s/g, '');
  const { totp } = await import('./helpers');
  await page.locator('#totp').fill(totp(secret));
  await page.locator('#userLabel').fill('test device').catch(() => undefined);
  await page.getByRole('button', { name: /submit|save/i }).click();
  await expect(page).toHaveURL(/^http:\/\/127\.0\.0\.1:\d+\/admin(\?continue)?$/);
  expect(await page.locator('body').innerText()).toContain('admin');
  expect((await meOf(page)).roles).toEqual(['ROLE_ADMIN', 'ROLE_USER']);
  void freshTotp;
});

test('sign out ends the Keycloak session too, and needs a CSRF token', async ({ page }) => {
  const email = track(uniqueEmail('signout'));
  await createUser(email);
  await open(page, '/profile', email);

  const forged = await page.request.post(`${HELLO}/logout`, { maxRedirects: 0 });
  expect(forged.status()).toBe(403);                         // a forged cross-site POST is refused

  await page.getByRole('button', { name: 'Sign out' }).click();
  await page.goto(`${HELLO}/profile`);
  await expect(page.locator('#username')).toBeVisible();     // asked to log in again: the Keycloak session is gone as well
});

test('delete my account runs the app step, then goes to Keycloak to delete the login', async ({ page }) => {
  const email = track(uniqueEmail('hdelete'));
  await createUser(email);
  await open(page, '/profile', email);
  const forged = await page.request.post(`${HELLO}/account/delete`, { maxRedirects: 0 });
  expect(forged.status()).toBe(403);
  await page.getByRole('button', { name: 'Delete my account' }).click();
  await expect(page).toHaveURL(new RegExp(`^${KC}/realms/avk/account/`));
});
