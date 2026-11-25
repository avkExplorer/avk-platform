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
| Manual rollback | After release `33a7d7…` went live on blue, `avk rollback hello` put the previous digest `e74a8b…` back on green in about 18 s. The public ping answered `pong` before and after. | `deployer.log` 2026-11-25 03:39 to 03:40 (`rollback: true`, `deploy_success`) |
| Automatic rollback | Release `c16183…` (ping creates a temp table, forbidden for `hello_rw`) passed CI and was owner-approved. Trust checks passed, migration ran, then the pre-switch smoke test got `GET /api/v1/ping returned 500`. Traffic never moved. GitHub Deployment 8047786637 shows `failure`. | `deployer.log` 2026-11-25 03:49 to 03:50; run https://github.com/avkExplorer/hello/actions/runs/45541635726 |
| Zero downtime | A poller hit the public ping twice a second from before the merge until after the failed deploy: 857 requests, 857 returned 200, none failed. | poll log, 21:40:41 to 21:50:12 (UTC-6) |

The broken change was merged to prove this path. Production stayed on `e74a8b…`. The next real release replaces the
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

Verified from the owner's PC on 2026-11-25 against digest `sha256:e74a8b1f…09312` (released and approved) and
`sha256:5488770d…30409` (merged but never approved):

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
