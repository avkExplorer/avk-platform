# Auth starter (Spring Boot + Keycloak)

The login code every web app copies. It is the tested code from `hello` (the pilot app); `hello` is where it is exercised by real
browser tests (`avk auth test --env stg`, file `e2e-auth/tests/hello-login.spec.ts`).

## What it gives an app
- Login with the shared Keycloak: authorization code + **PKCE (S256)**, confidential client, exact redirect address.
- A server-side session in an **HttpOnly, SameSite=Lax** cookie (**Secure** unless `COOKIE_SECURE=false`, which stg uses), **CSRF** on.
- Keycloak realm roles mapped to `ROLE_USER` / `ROLE_ADMIN` (anything else is ignored). `/admin` is enforced **on the server**.
- Sign out that also ends the Keycloak session (POST with a CSRF token). `/api/v1/me`, `/profile`, `/account` (the Keycloak account console).
- `AccountDeletionHook`: implement it as a Spring bean to erase the app's own data; it runs from the app's "Delete my account" button,
  then the person is sent to Keycloak to delete their login.
- With no `OIDC_CLIENT_ID` the app runs without login (public pages work, everything else is 401), so the ci stack and
  `--migrate-only` are unaffected.

## Adopt it in an app
1. Copy `src/main/java/auth/*.java` into the app's package `...auth` (change the `package` line) and `src/test/java/auth/*.java` likewise.
2. `pom.xml`: add `spring-boot-starter-security-oauth2-client` and, for tests, `spring-boot-starter-security-oauth2-client-test`.
3. `application.properties`: add the `avk.auth.*`, `server.forward-headers-strategy` and `server.servlet.session.*` lines from `hello`.
4. Decide which paths are public in `SecurityConfig` (the list in `authorizeHttpRequests`); keep the health paths public.
5. Create the client and its secrets: `avk secrets init <app> --env stg`, `avk db create <app> --env stg`, `avk auth client <app> --env stg`
   (the same with `--env prod` when it is time). The values land in the app's secret file; add the `OIDC_*` names to `avk.yaml` `env`.
6. Run the app's tests; add a Playwright spec like `hello-login.spec.ts` to `e2e-auth/tests/` for the app.

## Traps already met (do not relearn them)
- **PKCE:** Keycloak enforces it on the client; Spring adds it only for public clients, so `SecurityConfig` adds it explicitly.
- **Issuer:** the ID token's issuer is the *public* address (`OIDC_ISSUER`); token, key and user-info calls go to the *internal* one
  (`OIDC_INTERNAL_ISSUER`). That is why the client registration lists explicit endpoints instead of using discovery.
- **Roles:** read them from the `Authentication`, not from the `OidcUser` (the principal keeps Spring's default authorities), and attach
  the mapper explicitly with `userInfoEndpoint(...userAuthoritiesMapper(...))`. Mocked tests (`oidcLogin()`) skip the mapper, so only a
  real login test proves it.
- **Unknown paths** answer 401 like protected ones; update any test that expected 404.
- **`?continue`:** Spring Security appends it to the URL after login; do not assert on an exact URL.

## Known gap
If a person deletes their login straight from the Keycloak account console, the app is not told. The app's own "Delete my account" button
covers the normal path; a Keycloak event listener that notifies apps is a later improvement.
