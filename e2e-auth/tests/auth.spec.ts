import { expect, test } from '@playwright/test';
import {
  ACCOUNT, GENERIC_ERROR, GOOD_PASSWORD, admin, clearMail, createUser, deleteUserByEmail, expectLoggedInAs, findUserId,
  firstLink, freshTotp, login, logout, totp, uniqueEmail, waitForMail,
} from './helpers';

const created: string[] = [];
const track = (email: string) => { created.push(email); return email; };

test.beforeEach(async () => { await clearMail(); });
test.afterAll(async () => { for (const e of created) await deleteUserByEmail(e); });

async function register(page: import('@playwright/test').Page, email: string, password: string) {
  await page.goto(ACCOUNT);
  await page.getByRole('link', { name: /register/i }).click();
  await page.locator('#firstName').fill('Test');
  await page.locator('#lastName').fill('Person');
  await page.locator('#email').fill(email);
  await page.locator('#password').fill(password);
  await page.locator('#password-confirm').fill(password);
  await page.getByRole('button', { name: /register/i }).click();
}

test('sign up, verify the email, log in, see the profile, log out', async ({ page }) => {
  const email = track(uniqueEmail('signup'));
  await register(page, email, GOOD_PASSWORD);
  await expect(page.locator('body')).toContainText(/verify/i);

  const mail = await waitForMail(email);
  expect(mail.from).toBe('noreply@notify.avkaiexplorer.com');
  expect(mail.subject).toBe('Verify your AVK Explorer email');
  expect(mail.html).toContain('AVK');                       // branded wrapper
  await page.goto(firstLink(mail));
  await expect(page.locator('body')).toContainText(new RegExp(`verified|Personal info|${email}`, 'i'));

  await page.goto(ACCOUNT);
  await expectLoggedInAs(page, email);

  await logout(page);
  await page.goto(ACCOUNT);
  await expect(page.locator('#username')).toBeVisible();       // session is really gone
});

test('the login page is the AVK theme with Terms and Privacy links', async ({ page }) => {
  await page.goto(ACCOUNT);
  await expect(page.locator('.avk-brand')).toBeVisible();
  await expect(page.getByRole('link', { name: 'Terms' })).toHaveAttribute('href', /\/terms$/);
  await expect(page.getByRole('link', { name: 'Privacy' })).toHaveAttribute('href', /\/privacy$/);
  // phone width: no sideways scrolling
  await page.setViewportSize({ width: 375, height: 700 });
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(0);
});

test('weak passwords are refused: too short, and equal to the email address', async ({ page }) => {
  const email = track(uniqueEmail('weak'));
  await register(page, email, 'short1');
  await expect(page.locator('body')).toContainText(/invalid password|at least 12/i);
  await page.goto(ACCOUNT);
  await register(page, email, email);                          // 20+ characters, so only notEmail can catch it
  await expect(page.locator('body')).toContainText(/invalid password|not be equal|email/i);
  expect(await findUserId(email)).toBeNull();
});

test('passwords are stored with Argon2', async () => {
  const email = track(uniqueEmail('argon'));
  const id = await createUser(email);
  const creds = await (await admin(`/users/${id}/credentials`)).json();
  const password = creds.find((c: { type: string }) => c.type === 'password');
  expect(JSON.parse(password.credentialData).algorithm).toBe('argon2');
});

test('wrong passwords give the same generic error for real and unknown accounts, then lock out', async ({ page }) => {
  const email = track(uniqueEmail('lock'));
  await createUser(email);

  await page.goto(ACCOUNT);
  await page.locator('#username').fill(uniqueEmail('nobody'));
  await page.locator('#password').fill('Wrong-Password-1');
  await page.locator('#kc-login').click();
  const errorText = async () => (await page.getByText(GENERIC_ERROR).first().innerText()).trim();
  const unknownText = await errorText();
  expect(unknownText).toMatch(GENERIC_ERROR);

  let realText = '';
  for (let i = 0; i < 5; i++) {
    await page.goto(ACCOUNT);
    await page.locator('#username').fill(email);
    await page.locator('#password').fill(`Wrong-Password-${i}`);
    await page.locator('#kc-login').click();
    realText = await errorText();
  }
  expect(realText).toBe(unknownText);                          // an attacker cannot tell the accounts apart

  // The right password no longer works while the account is temporarily locked.
  await login(page, email, GOOD_PASSWORD);
  await expect(page.locator('#username')).toBeVisible();
  await expect(page.locator('body')).toContainText(GENERIC_ERROR);
});

test('forgot password: email arrives, new password works', async ({ page, browser }) => {
  const email = track(uniqueEmail('reset'));
  await createUser(email);
  await page.goto(ACCOUNT);
  await page.getByRole('link', { name: /forgot/i }).click();
  await page.locator('#username').fill(email);
  await page.getByRole('button', { name: /submit/i }).click();

  const mail = await waitForMail(email);
  expect(mail.subject).toBe('Reset your AVK Explorer password');
  await page.goto(firstLink(mail));
  const newPassword = 'Another-Strong-Pass-7';
  await page.locator('#password-new').fill(newPassword);
  await page.locator('#password-confirm').fill(newPassword);
  await page.getByRole('button', { name: /submit/i }).click();

  const fresh = await (await browser.newContext()).newPage();
  await login(fresh, email, newPassword);
  await expectLoggedInAs(fresh, email);
});

test('an admin without OTP is forced to enrol, and is asked for a code afterwards', async ({ page }) => {
  const email = track(uniqueEmail('admin'));
  await createUser(email, { admin: true });

  await login(page, email, GOOD_PASSWORD);
  await expect(page.locator('body')).toContainText(/authenticator|mobile/i);       // setup page, not the account
  await page.getByRole('link', { name: /unable to scan/i }).click();
  const secret = (await page.locator('#kc-totp-secret-key').innerText()).replace(/\s/g, '');
  await page.locator('#totp').fill(totp(secret));
  await page.locator('#userLabel').fill('test device').catch(() => undefined);
  await page.getByRole('button', { name: /submit|save/i }).click();
  await expectLoggedInAs(page, email);

  await logout(page);
  await login(page, email, GOOD_PASSWORD);
  await expect(page.locator('#otp')).toBeVisible();                                  // now asks for a code every time
  await page.locator('#otp').fill(await freshTotp(secret));                          // the enrolment code is never reused
  await page.getByRole('button', { name: /sign in|submit|log in/i }).click();
  await expectLoggedInAs(page, email);
});

test('a normal user is not asked for OTP', async ({ page }) => {
  const email = track(uniqueEmail('plain'));
  await createUser(email);
  await login(page, email, GOOD_PASSWORD);
  await expectLoggedInAs(page, email);
});

test('a user can delete their own account', async ({ page }) => {
  const email = track(uniqueEmail('delete'));
  await createUser(email);
  await login(page, email, GOOD_PASSWORD);
  await page.getByRole('button', { name: 'Delete account' }).click();            // opens the warning
  await page.getByRole('button', { name: 'Delete', exact: true }).click();       // the red button
  // Keycloak may ask for the password again (re-authentication) before it asks for the final confirmation.
  const password = page.locator('#password');
  if (await password.isVisible({ timeout: 3_000 }).catch(() => false)) {
    await password.fill(GOOD_PASSWORD);
    await page.locator('#kc-login').click();
  }
  await page.getByRole('button', { name: /confirm/i }).click();
  await expect.poll(async () => findUserId(email), { timeout: 15_000 }).toBeNull();
});
