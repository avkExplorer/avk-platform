import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { MAILPIT, clearMail, waitForMail } from './helpers';

// Phase 6 on the stg copy of hello: policy pages, cookie behaviour, contact form with tickets and rate limiting.
const HELLO = process.env.HELLO_URL ?? 'http://127.0.0.1:4820';

test.beforeEach(async () => { await clearMail(); });

test('every policy page is public, has the DRAFT banner, and the footer links reach them', async ({ page }) => {
  await page.goto(HELLO + '/');
  const footer = page.getByRole('navigation', { name: 'Legal and support' });
  for (const name of ['Terms of Service', 'Privacy Policy', 'Cookie Notice', 'Accessibility Statement', 'Contact']) {
    await footer.getByRole('link', { name }).click();
    await expect(page.getByRole('heading', { level: 1, name })).toBeVisible();
    await expect(page.getByText('DRAFT – needs review by a qualified attorney/CPA')).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Legal and support' })).toBeVisible();   // footer on every page
    await page.goto(HELLO + '/');
  }
  const security = await page.request.get(HELLO + '/.well-known/security.txt');
  expect(security.status()).toBe(200);
  expect(await security.text()).toContain('mailto:security@avkaiexplorer.com');
});

test('no cookie is set before or after the notice; the choice is kept in local storage and can be changed', async ({ page, context }) => {
  await page.goto(HELLO + '/');
  const before = { cookies: await context.cookies(), storage: await page.evaluate(() => Object.keys(window.localStorage)) };
  console.log('BEFORE choice: cookies =', JSON.stringify(before.cookies.map(c => c.name)), 'localStorage =', JSON.stringify(before.storage));
  expect(before.cookies).toEqual([]);                 // a plain visit sets no cookie at all (login cookies only appear after sign-in)
  expect(before.storage).toEqual([]);
  const box = page.getByRole('dialog', { name: 'Cookie choices' });
  await expect(box).toBeVisible();
  await box.getByRole('button', { name: 'OK' }).click();
  await expect(box).toBeHidden();
  const after = { cookies: await context.cookies(), storage: await page.evaluate(() => ({ ...window.localStorage })) };
  console.log('AFTER choice:  cookies =', JSON.stringify(after.cookies.map(c => c.name)), 'localStorage =', JSON.stringify(after.storage));
  expect(after.cookies).toEqual([]);
  expect(after.storage).toEqual({ avk_consent: 'noticed' });
  await page.reload();
  await expect(box).toBeHidden();                      // remembered
  await page.getByRole('link', { name: 'Cookie settings' }).click();
  await expect(box).toBeVisible();                     // reopened from the footer
});

test('optional scripts stay off until Accept, and Accept and Reject look the same (consent mode)', async ({ page }) => {
  await page.goto(HELLO + '/legal/terms.html');
  await page.evaluate(() => {
    window.localStorage.clear();
    (window as any).tracker = 0;
    const s = document.createElement('script');
    s.type = 'text/plain'; s.setAttribute('data-consent', 'analytics'); s.text = 'window.tracker = 1';
    document.body.appendChild(s);
    const c = document.createElement('script');
    c.src = '/legal/consent.js'; c.setAttribute('data-mode', 'consent');
    document.body.appendChild(c);
  });
  const box = page.getByRole('dialog', { name: 'Cookie choices' });
  await expect(box.getByRole('button', { name: 'Accept' })).toBeVisible();
  await expect(box.getByRole('button', { name: 'Reject' })).toBeVisible();
  const a = await box.getByRole('button', { name: 'Accept' }).boundingBox();
  const r = await box.getByRole('button', { name: 'Reject' }).boundingBox();
  expect(Math.abs(a!.width - r!.width)).toBeLessThan(40);          // equal weight: same height and style, similar width
  expect(a!.height).toBe(r!.height);
  expect(await page.evaluate(() => (window as any).tracker)).toBe(0);
  await box.getByRole('button', { name: 'Accept' }).click();
  expect(await page.evaluate(() => (window as any).tracker)).toBe(1);
});

test('bad input and bots', async ({ request }) => {
  expect((await request.post(HELLO + '/contact', { form: { name: 'A', email: 'nope', message: 'long enough message' } })).status()).toBe(400);
  const bot = await request.post(HELLO + '/contact', { form: { name: 'Bot', email: 'bot@example.test', message: 'long enough message', website: 'http://spam.test' } });
  expect(bot.status()).toBe(200);
});

test('the contact form sends a ticket to support and an acknowledgement to the visitor; the 6th message in a minute is limited', async ({ page, request }) => {
  test.setTimeout(120_000);
  await page.waitForTimeout(61_000);                  // let the earlier bad-input posts leave the one-minute window
  const email = `visitor-${Date.now()}@example.test`;
  await page.goto(HELLO + '/legal/contact.html');
  await page.getByLabel('Your name').fill('Ann Visitor');
  await page.getByLabel('Your email').fill(email);
  await page.getByLabel('Message').fill('Hello, I would like to ask a question.');
  await page.getByRole('button', { name: 'Send' }).click();
  const ticket = (await page.getByText(/HEL-\d{8}-[A-Z2-9]{6}/).first().textContent())!.match(/HEL-\d{8}-[A-Z2-9]{6}/)![0];

  const ack = await waitForMail(email);
  expect(ack.text).toContain(ticket);
  expect(ack.text).toContain('Hi Ann');
  const list = await (await request.get(`${MAILPIT}/api/v1/search?query=${encodeURIComponent('to:support@avkaiexplorer.com')}`)).json();
  expect(list.messages.some((m: any) => m.Subject.includes(ticket))).toBe(true);

  const form = { name: 'Ann Visitor', email, message: 'Another message to test limits.' };
  const statuses: number[] = [200];                    // the first message above already counted
  for (let i = 0; i < 5; i++) {
    statuses.push((await request.post(HELLO + '/contact', { form })).status());
  }
  console.log('statuses:', statuses.join(' '));
  expect(statuses.slice(0, 5)).toEqual([200, 200, 200, 200, 200]);
  expect(statuses[5]).toBe(429);
});

// The shared login page lives on the auth host, so its accessibility check runs here against the stg Keycloak.
test('accessibility: the login and registration pages have no serious or critical WCAG 2.1 A/AA violations', async ({ page }) => {
  for (const path of ['login']) {
    await page.goto(HELLO + '/profile');                 // sends us to the login page
    const blocking = async () => (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze())
      .violations.filter(v => v.impact === 'serious' || v.impact === 'critical').map(v => `${v.id} (${v.impact})`);
    await expect(page.getByRole('button', { name: /sign in/i })).toBeVisible();
    expect(await blocking(), `login page ${path}`).toEqual([]);
    await page.getByRole('link', { name: /register/i }).click();
    await expect(page.getByRole('heading', { name: /register/i })).toBeVisible();
    expect(await blocking(), 'registration page').toEqual([]);
  }
});
