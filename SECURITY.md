# Security

## Reporting a vulnerability

Email `security@avkaiexplorer.com`, or open a private security advisory on the GitHub repository concerned
(private vulnerability reporting is enabled on `avk-platform` and `hello`).

## What stops a bad change from reaching production

Every app pull request and every merge to `main` goes through the reusable workflow `ci.yml`. All gates run, and the
job fails at the end if any of them failed, so one run shows everything that is wrong.

| Gate | Tool and version | Fails when | Runs |
|---|---|---|---|
| Build, tests, coverage | Maven, JUnit, RestAssured, JaCoCo 0.8.16 | a test fails or line coverage is under 70% | in the runner |
| Secrets | gitleaks v8.30.1 | a secret appears in the commits this change adds | container, pinned by digest |
| Dependencies | Trivy 0.76.2 | a High or Critical vulnerability that has a fix | container, pinned by digest |
| Dockerfile and workflows | Checkov 3.4.10 | any failed check | container, pinned by digest |
| Licences | Trivy 0.76.2 | a forbidden licence (GPL or AGPL without an exception). LGPL and the GPL Classpath Exception are allowed | container, pinned by digest |
| Image | Trivy 0.76.2 | a High or Critical vulnerability with a fix in the built image | container, pinned by digest |
| Static analysis | CodeQL (action v4.39.5), Java | any error-level (high severity) finding | GitHub Actions |

After the gates, `release.yml` builds the image once, signs it, attaches an SBOM (Syft 1.57.2, CycloneDX), runs the exact
digest in a throwaway stack with PostgreSQL (Playwright 1.65 smoke, API and accessibility tests; ZAP 2.17.0 baseline scan,
fails on a High alert) and verifies the signature again with the exact identity the deployer will use.
Accessibility findings (axe-core) are reported but not enforced until Phase 6.

Hardening that applies to every workflow: empty top-level `permissions`, per-job grants only where needed, actions pinned
by commit SHA, scanners pinned by image digest, no `${{ }}` expression inside a `run:` script (values go through `env:`),
timeouts on every job, no `pull_request_target`, no self-hosted runners (`gh api repos/<owner>/<repo>/actions/runners`
reports 0 for both repositories), a read-only default token, and approval required for workflow runs from any outside
contributor's fork.

### Known limits

- The dependency, image and licence gates ignore vulnerabilities that have no fix yet (`--ignore-unfixed`). Unfixed
  findings stay visible in the scanner output but do not block a release.
- Checkov ships no policies for Cloudflare, so the Terraform in `infra/` is checked only by `terraform validate`, review
  and the plan the owner reads before every apply.
- CodeQL runs in build-less mode for Java. It found the planted flaw below, but build-less analysis can miss things.
- A scanner's own supply chain is trusted at the pinned digest. Digests are reviewed when updated.

## Gate proofs

Each row is a pull request on `hello` with one planted flaw. Each failed at the gate named, and the other gates passed
(the dependency proof also fails the image scan, correctly, because the library is inside the image). The pull requests
were closed without merging and the branches deleted. The runs below are the re-runs on the final workflow.

| Proof | Planted change | Failed at | Run |
|---|---|---|---|
| Secret | a generated, never-valid key in a form gitleaks recognises | gitleaks | https://github.com/avkExplorer/hello/actions/runs/45514871255 |
| Vulnerable dependency | `commons-text` 1.9 (Critical CVE, fixed in 1.10.0) | Trivy dependencies | https://github.com/avkExplorer/hello/actions/runs/45514875527 |
| Coverage | the API test class deleted | coverage gate (build step) | https://github.com/avkExplorer/hello/actions/runs/45514879093 |
| Image | an old base image (`21.0.1_12-jre-alpine`) | Trivy image | https://github.com/avkExplorer/hello/actions/runs/45514883567 |
| Static analysis | SQL built by string concatenation from a request parameter | CodeQL (`java/sql-injection`, high) | https://github.com/avkExplorer/hello/actions/runs/45514884430 |

GitHub's push protection did not block the planted key (it is a generic format), so gitleaks is the gate that stops it.
Two bugs in the pipeline itself were found and fixed by these proofs: gitleaks scanned every branch, so a leak on one
branch failed unrelated pull requests; and the CodeQL result check read severity from the wrong place and missed the
finding.

Green path: pull request https://github.com/avkExplorer/hello/pull/4 (all gates green) and release run
https://github.com/avkExplorer/hello/actions/runs/45513801212 (image, signature, SBOM, ci stack, approval).

## Deployer proofs (Phase 4)

| Proof | What happened | Evidence |
|---|---|---|
| Manual rollback | After release `33a7d7â€¦` went live on blue, `avk rollback hello` put the previous digest `e74a8bâ€¦` back on green in about 18 s. The public ping answered `pong` before and after. | `deployer.log` 2026-11-25 03:39 to 03:40 (`rollback: true`, `deploy_success`) |
| Automatic rollback | Release `c16183â€¦` (ping creates a temp table, forbidden for `hello_rw`) passed CI and was owner-approved. Trust checks passed, migration ran, then the pre-switch smoke test got `GET /api/v1/ping returned 500`. Traffic never moved. GitHub Deployment 8047786637 shows `failure`. | `deployer.log` 2026-11-25 03:49 to 03:50; run https://github.com/avkExplorer/hello/actions/runs/45541635726 |
| Zero downtime | A poller hit the public ping twice a second from before the merge until after the failed deploy: 857 requests, 857 returned 200, none failed. | poll log, 21:40:41 to 21:50:12 (UTC-6) |

| Reboot | Windows restarted twice. Second boot (22:14): logon task started Docker Desktop, engine ready in 11 s, `avk prod up` exit 0, the deploy agent ran its next tick, and the public ping answered `pong`, with no manual step. First boot: production came back by itself, but the Docker engine API hung for 10+ minutes and froze `avk prod up` and the agent task. Docker Desktop was restarted by hand. Fix: every Docker call in `prod-at-logon.ps1` and `run-avk-task.ps1` now has a time limit, and the logon script restarts Docker Desktop once if `prod up` hangs. | `C:\avk\logs\prod-at-logon.log`, `task-deploy-agent.log` |

The broken change was merged to prove this path. Production stayed on `e74a8bâ€¦`. The next real release replaces the
broken code on `main`.

## How a release reaches production (trust chain)

The PC only ever pulls. A GitHub Deployment is a trigger, never evidence: every merge produces a signed image before
anyone approves it, so approval is proven by a signature that only the post-approval job can make.

1. `release.yml` (identity A) signs the image digest keyless and verifies it.
2. The owner approves the `prod` environment (required reviewer: the owner; only `main` may deploy to it).
3. `approve.yml` (identity B) signs an approval attestation for that exact digest, predicate type
   `https://avkaiexplorer.com/attestations/prod-approval/v1`, content `{app, digest, commit, run_url, approved_at}`, then creates
   the GitHub Deployment whose only job is to wake the deployer.
4. The deployer (Phase 4) deploys a digest only if the build signature verifies for identity A, the approval attestation
   verifies for identity B with matching app and digest, the digest pulled equals both, and the app is registered.

The identities are stored in the `signing:` block of `platform.yaml` and nowhere else. Identity B is a different workflow
file from identity A, so the build workflow cannot produce an approval. The certificate identity carries the ref the
caller used (`@refs/heads/main` today; pinned to a commit SHA in Phase 8).

Verified from the owner's PC on 2026-11-25 against digest `sha256:e74a8b1fâ€¦09312` (released and approved) and
`sha256:5488770dâ€¦30409` (merged but never approved):

| Check | Result |
|---|---|
| anonymous `docker pull` of the digest | works |
| `cosign verify` with identity A | passes |
| `cosign verify-attestation` with identity B and the predicate type, approved digest | passes; predicate matches app, digest and commit |
| same, never-approved digest | fails (only a build signature and an SBOM exist) |
| approval type checked with identity A | fails (the build workflow cannot make an approval) |
| a different identity on `cosign verify` | fails |
| `prod` deployments | exactly one, created by the workflow, none by GitHub itself |

Residual trust: the owner's GitHub account (it can approve and change workflows) and Cloudflare account. Both must have
two-factor authentication; GitHub is enabled, Cloudflare uses email for now and is to move to an authenticator app.

## Authentication (Phase 5: shared Keycloak)

One Keycloak (26.8.2, image built locally from a digest-pinned base) serves every app through the realm `avk`. Apps never
store passwords. Stg: `http://127.0.0.1:4810`. Prod: `https://auth.avkaiexplorer.com`.

**Exposure.** Publicly, Traefik answers 404 for `/admin`, the `master` realm, `/metrics` and `/health`. The admin console is
published only on `127.0.0.1:4910` and has its own hostname setting. Keycloak's management port (health, metrics) is never
published. Responses carry HSTS, no-sniff, no-framing and a strict referrer policy.

**Realm settings (file: `runtime/keycloak/realm-avk.json`, applied by `avk auth apply`, safe to run again).**
- Sign-up with email as the username, email verification required, forgot-password on.
- Passwords: at least 12 characters, not the username, not the email, stored with **Argon2** (checked by a test that reads
  the stored credential).
- Brute-force protection: five failures lock the account temporarily (growing wait, never permanent). The error text is the
  same for an unknown account, a wrong password and a locked account (tested).
- Access tokens last 5 minutes; refresh tokens rotate and cannot be reused; sessions idle out after 30 minutes and end after 10 hours.
- OTP: optional for everyone; **required for the `admin` role** (an admin without OTP must enrol at the next login; an admin is
  asked for a code every login). Everyone else is prompted only if they enrolled.
- Users can delete their own account in the account console.
- Login and admin events are kept for 30 days and also written to the container log as JSON.

**Clients.** `avk auth client <app> --env <env>`: confidential client, Authorization Code with PKCE (S256, enforced by Keycloak:
a request without a challenge is refused), exact redirect addresses, no implicit flow, no password grant, no service account.

**Admin accounts.** The temporary bootstrap admin is deleted on first use. Two permanent master-realm accounts replace it:
`avk-admin` (the owner; must enrol OTP at first login) and `avk-cli` (non-human, used only by the `avk` command; it has no
OTP because a script cannot type one, and the admin API is not reachable from the internet).

**Browser tests.** `avk auth test --env stg` runs nine scenarios against the real login: sign-up, verification email,
login, profile, logout, theme and footer, weak passwords, Argon2, lockout and identical errors, password reset, forced admin
OTP, no OTP prompt for a normal user, account deletion.

**Known gaps.**
- No breached-password check (Keycloak has none built in; the length and Argon2 rules are the compensation).
- The master-realm CLI user has no second factor; it is protected by the loopback-only admin port and a 64-character secret.
- The Terms and Privacy links in the login footer point at pages that arrive in Phase 6.
- Social login is off. Turnstile (bot check) is not enabled; the edge rate limit covers the login, registration and reset paths.
- The CI stack has no Keycloak, so the pipeline does not run the real browser login tests; those run by hand against stg
  (17 tests) and `hello`'s unit tests cover the role rules. Deferred by the owner on 2026-11-25; build it at the start of
  Phase 8 (`avk new-app` needs a standard test setup anyway), or sooner if a second app or developer appears.
- Norton's Mail Shield re-signs the mail TLS to Resend; prod Keycloak trusts the exported Windows roots as a workaround.
- An account deleted directly in the Keycloak console is not announced to the apps.

## Legal pack and accessibility gate (Phase 6)

Policy pages, the cookie notice, the contact page and `security.txt` are generated from shared templates (`legal/`) and are a **draft, not legal
advice**: each page shows "DRAFT – needs review by a qualified attorney/CPA" until the owner sets `legal.reviewed: true`. Templates never invent
facts; missing inputs render as `[TO COMPLETE]` and `avk legal render --check` fails for a live app while any remain.

- **Cookies:** a plain visit sets no cookie at all; the notice choice lives in local storage (`avk_consent`). Optional scripts (`type="text/plain" data-consent`) stay inert until Accept. Proven in the stg browser tests (cookies `[]` before and after).
- **Contact form:** server-side validation, hidden-field bot trap, 5 messages per minute per address (6th answers 429), optional Cloudflare Turnstile, nothing typed by the visitor is logged. The form is a static page and cannot carry a CSRF token, so `/contact` ignores CSRF; abuse is bounded by the rate limit. Known limit: anyone can make the form send the (message-free) acknowledgement to an address they type; turn Turnstile on before real traffic.
- **Redirects:** `redirects.csv` accepts only same-site paths and refuses loops; bad lines stop start-up (no open redirect).
- **Accessibility gate:** axe-core (WCAG 2.1 A/AA) runs in the release run on the home page, contact page and every policy page, and in the platform's stg tests on the login and registration pages. Any serious or critical violation fails the run.
  - Proof (2026-11-25, local run of the same Playwright test used in the release): with an image without alt text and an unlabelled input planted on the contact page, `accessibility: /legal/contact.html` failed with `image-alt (critical): 1 element(s)` and `label (critical): 1 element(s)` while the other five pages passed; the clean pages all pass.
  - Pipeline proof (2026-11-25): `hello` PR #18 planted the same two defects on the contact page and was merged to `main`. Release run [45670427512](https://github.com/avkExplorer/hello/actions/runs/45670427512) failed at "verify digest in ci stack" with `accessibility: /legal/contact.html` reporting `image-alt (critical)` and `label (critical)`; the other five pages passed and the run never reached the approval step. PR #19 reverted it, and the clean release run [45672316313](https://github.com/avkExplorer/hello/actions/runs/45672316313) passed all steps.
- **Mail TLS and Norton:** Norton re-signs the mail connection to Resend with its own root, which Java in a container does not trust. An app whose `avk.yaml` says `host_truststore: true` gets a read-only Java truststore built from this PC's Windows roots (`avk certs refresh` writes `C:\avk\certs\host-truststore.p12`), mounted at `/certs/host-truststore.p12` with `JAVA_TOOL_OPTIONS` pointing at it. This is the one host file an app container sees; it holds public certificates only. Trade-off: the app then also trusts Norton's root for every outgoing TLS connection, and the file must be refreshed if Norton changes its root. It disappears as a need if Norton's TLS scanning is switched off (I could not find the switch: turning off Email Protection did not stop it).

## Infrastructure posture

- No inbound ports; the tunnel is outbound only. Cloudflare terminates TLS (1.2 minimum, HTTPS enforced).
- HSTS is short (300 s) until HTTPS is proven everywhere; no preload.
- Free managed WAF ruleset and one rate-limit rule on login-like paths.
- Secrets and Terraform state stay in `%AVK_HOME%`, outside git. The tunnel token is read by a container from a secret
  file, not from the environment.

## Accepted risks

- Single PC: no redundancy; apps are down when it is off.
- Disk encryption is the owner's decision (not enabled when last checked).
- The platform repo's `main` allows the owner to push directly (administrator bypass) so a broken pipeline can be fixed.
  Pull requests are still required for everyone else.
