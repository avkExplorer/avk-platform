# legal-starter: the app side of the legal pack (a draft, not legal advice)

Copied from `hello`, where it is built and tested. Use it together with `python -m avk legal render <app>` (see `legal/README.md`).

## What is in here
- `contact/`: the contact form's server side. Validation, a hidden bot field, 5 messages per minute per address, an optional
  Cloudflare Turnstile check (set `TURNSTILE_SECRET`), a ticket id (`<PREFIX>-YYYYMMDD-XXXXXX`), a ticket mail to support and the
  acknowledgement mail rendered from the Phase 2 `support_ack` template. Without `SMTP_HOST` it answers 503 and points to the support address.
- `legacy/`: `RedirectsFilter` (reads `redirects.csv`, answers 301 before login; same-site paths only, bad lines stop start-up) and
  `SunsetHeaders` (RFC 8594 `Sunset`, `Deprecation` and `Link: rel=successor-version` for a deprecated API version).
- `accessibility-and-smoke.spec.ts.example`: the axe gate from hello's e2e tests (serious and critical WCAG 2.1 A/AA violations fail the run).

## Steps for a new Spring Boot app
1. Copy the `src/` files (change the package name), add `spring-boot-starter-mail` to `pom.xml`, and the `avk.contact.*` lines from hello's `application.properties`.
2. In `SecurityConfig` make `/legal/**`, `/.well-known/security.txt` and `/contact` public and ignore CSRF for `/contact`
   (the form is a static page; the handler is rate-limited and bot-checked instead).
3. Add a `legal:` block and the footer markers to `index.html`; add the `SMTP_*` names to `env:` in `avk.yaml`.
4. `python -m avk legal smtp <app> --env stg|prod`, then `python -m avk legal render <app>`.
5. Put the axe test in the app's `e2e/tests` and list the app's pages in it.

## Traps
- On this PC Norton re-signs outgoing TLS, so mail to Resend fails in Java unless the app has `host_truststore: true` in `avk.yaml` (the deployer then mounts the truststore built by `avk certs refresh`).
- The contact form cannot carry a CSRF token (static page), so anyone can make it send the acknowledgement to an address they type.
  The acknowledgement contains no message text, and the rate limit plus Turnstile bound the abuse. Turn Turnstile on before real traffic.
- A servlet filter must be `HIGHEST_PRECEDENCE` to run before Spring Security, or old addresses answer 401/login instead of 301.
- `GET /` in MockMvc forwards to the welcome page with an empty body; test `/index.html` instead.
