import { createHmac, randomBytes } from 'node:crypto';
import { expect, type Page } from '@playwright/test';

export const KC = process.env.KC_URL ?? 'http://127.0.0.1:4810';
export const MAILPIT = process.env.MAILPIT_URL ?? 'http://127.0.0.1:4811';
export const REALM = 'avk';
export const GOOD_PASSWORD = 'Correct-Horse-Battery-9';
export const GENERIC_ERROR = /invalid username or password/i;

// ------------------------------------------------------------------------------------------------ test users
export function uniqueEmail(tag: string): string {
  return `${tag}-${randomBytes(4).toString('hex')}@example.test`;
}

// ------------------------------------------------------------------------------------------------ admin API
// The CLI's own admin user (never a person's account); credentials come from `avk auth test`, not from files here.
async function adminToken(): Promise<string> {
  const user = process.env.KC_ADMIN_USER;
  const pass = process.env.KC_ADMIN_PASS;
  if (!user || !pass) throw new Error('KC_ADMIN_USER / KC_ADMIN_PASS missing: run these tests with `avk auth test --env stg`');
  const res = await fetch(`${KC}/realms/master/protocol/openid-connect/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ grant_type: 'password', client_id: 'admin-cli', username: user, password: pass }),
  });
  if (!res.ok) throw new Error(`admin login failed: HTTP ${res.status}`);
  return (await res.json()).access_token as string;
}

export async function admin(path: string, init: { method?: string; body?: unknown } = {}): Promise<Response> {
  const token = await adminToken();
  return fetch(`${KC}/admin/realms/${REALM}${path}`, {
    method: init.method ?? 'GET',
    headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: init.body === undefined ? undefined : JSON.stringify(init.body),
  });
}

export async function createUser(email: string, opts: { password?: string; admin?: boolean } = {}): Promise<string> {
  const res = await admin('/users', {
    method: 'POST',
    body: {
      username: email, email, firstName: 'Test', lastName: 'User', enabled: true, emailVerified: true,
      credentials: [{ type: 'password', value: opts.password ?? GOOD_PASSWORD, temporary: false }],
    },
  });
  expect(res.status, 'create user').toBe(201);
  const id = (res.headers.get('location') ?? '').split('/').pop() as string;
  if (opts.admin) {
    const role = await (await admin('/roles/admin')).json();
    const mapped = await admin(`/users/${id}/role-mappings/realm`, { method: 'POST', body: [role] });
    expect(mapped.status, 'give admin role').toBe(204);
  }
  return id;
}

export async function findUserId(email: string): Promise<string | null> {
  const found = await (await admin(`/users?email=${encodeURIComponent(email)}&exact=true`)).json();
  return found.length ? found[0].id : null;
}

export async function deleteUserByEmail(email: string): Promise<void> {
  const id = await findUserId(email);
  if (id) await admin(`/users/${id}`, { method: 'DELETE' });
}

// ------------------------------------------------------------------------------------------------ Mailpit
export async function clearMail(): Promise<void> {
  await fetch(`${MAILPIT}/api/v1/messages`, { method: 'DELETE' });
}

export interface Mail { id: string; subject: string; from: string; html: string; text: string }

export async function waitForMail(to: string, timeoutMs = 20_000): Promise<Mail> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const list = await (await fetch(`${MAILPIT}/api/v1/search?query=${encodeURIComponent(`to:${to}`)}`)).json();
    if (list.messages?.length) {
      const id = list.messages[0].ID as string;
      const m = await (await fetch(`${MAILPIT}/api/v1/message/${id}`)).json();
      return { id, subject: m.Subject, from: m.From.Address, html: m.HTML ?? '', text: m.Text ?? '' };
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`no email to ${to} within ${timeoutMs} ms`);
}

export function firstLink(mail: Mail): string {
  const m = mail.html.match(/href="([^"]+)"/) ?? mail.text.match(/(https?:\/\/\S+)/);
  if (!m) throw new Error('no link in the email');
  return m[1].replace(/&amp;/g, '&');
}

// ------------------------------------------------------------------------------------------------ TOTP (RFC 6238)
function base32Decode(s: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const c of s.replace(/[\s=]/g, '').toUpperCase()) bits += alphabet.indexOf(c).toString(2).padStart(5, '0');
  const bytes: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

export function totp(secret: string, stepOffset = 0): string {
  const counter = Math.floor(Date.now() / 1000 / 30) + stepOffset;
  const msg = Buffer.alloc(8);
  msg.writeBigUInt64BE(BigInt(counter));
  const h = createHmac('sha1', base32Decode(secret)).update(msg).digest();
  const o = h[h.length - 1] & 0x0f;
  const code = ((h[o] & 0x7f) << 24) | (h[o + 1] << 16) | (h[o + 2] << 8) | h[o + 3];
  return String(code % 1_000_000).padStart(6, '0');
}

/** A code for the NEXT 30-second step: waits for it, so the code that enrolled the device is never reused. */
export async function freshTotp(secret: string): Promise<string> {
  const used = Math.floor(Date.now() / 1000 / 30);
  while (Math.floor(Date.now() / 1000 / 30) === used) await new Promise((r) => setTimeout(r, 500));
  return totp(secret);
}

// ------------------------------------------------------------------------------------------------ browser steps
export const ACCOUNT = `${KC}/realms/${REALM}/account/`;

export async function login(page: Page, email: string, password: string): Promise<void> {
  await page.goto(ACCOUNT);
  await page.locator('#username').fill(email);
  await page.locator('#password').fill(password);
  await page.locator('#kc-login').click();
}

/** The account console's Personal info page shows the signed-in user's email in a (disabled) field. */
export async function expectLoggedInAs(page: Page, email: string): Promise<void> {
  await expect(page.getByRole('textbox', { name: 'Email' })).toHaveValue(email, { timeout: 15_000 });
}

export async function logout(page: Page): Promise<void> {
  await page.goto(`${KC}/realms/${REALM}/protocol/openid-connect/logout`);
  const confirm = page.getByRole('button', { name: /logout|sign out/i });
  if (await confirm.isVisible().catch(() => false)) await confirm.click();
}
