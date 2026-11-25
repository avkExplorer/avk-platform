# AVK app platform

A reusable deployment platform for apps on avkaiexplorer.com. Each app gets a local non-prod environment, a
prod hostname, a security-gated GitHub Actions pipeline, a database, login, business email and legal pages.

**Status:** Phases 0 to 3 built: tunnel and placeholder pages, business email, and the security-gated pipeline
(signed images, owner approval) proven on the pilot service [hello](https://github.com/avkExplorer/hello). The runtime
(deployer, databases, backups) is next. Phases: foundation (DNS and tunnel), email, pipeline, runtime, auth,
legal, observability, onboarding, AWS lab.

## How it is shaped

- **Prod** runs in Docker on the owner's PC and is published through a Cloudflare Tunnel: no inbound ports,
  no home IP in DNS. Hostnames are `<app>.avkaiexplorer.com`, one level deep.
- **Non-prod** runs on the same PC at `http://127.0.0.1:<port>` only.
- **Pipeline:** public GitHub repos, trunk-based. Each merge to `main` builds one signed image; prod runs
  that exact digest, and only after the owner approves it.
- **Deploys are pulled** by a deployer on the PC that verifies the build signature and a signed approval
  attestation. GitHub never connects to the PC, and there are no self-hosted runners.
- **AWS lab:** the same image can be deployed on demand to ECS + RDS and torn down the same day.

## Decisions

| Topic | Decision |
|---|---|
| Hosting | Home PC behind a Cloudflare Tunnel; AWS only for lab sessions |
| Repos | Public on GitHub |
| Template stack | Java 21, Spring Boot |
| Identity | Keycloak (self-hosted OIDC) |
| Budget | About $1/month in subscriptions; AWS lab under $5/month |
| After a reboot | Prod returns once the owner logs in to Windows (logon task, below) |

## Quick start (this PC)

```powershell
$avk = "$env:AVK_HOME\venv\Scripts\python.exe"
& $avk -m avk doctor      # is the PC ready? exit 1 on any failure
& $avk -m avk validate    # checks platform.yaml
cd cli; & $avk -m pytest -q
```

Set up once (done in Phase 0):

1. Create `AVK_HOME`, a user environment variable, default `C:\avk`. Restrict its permissions to the owner
   and SYSTEM.
2. Create the venv: `python -m venv C:\avk\venv`, then `C:\avk\venv\Scripts\python.exe -m pip install -e cli[test]`.
3. Run `tools\register-logon-task.ps1`. At logon it waits for Docker, then runs `avk prod up`. Log:
   `%AVK_HOME%\logs\prod-at-logon.log`.

## Tools (installed 2026-11-14 with `winget install --source winget`)

| Tool | Version | Needed from |
|---|---|---|
| git | 2.56.1 | Phase 0 |
| Docker Desktop | 29.9.3 | Phase 0 |
| Python | 3.14.10 (avk needs 3.12+) | Phase 0 |
| GitHub CLI | 2.104.0 | Phase 3 |
| Terraform | 1.17.1 | Phase 1 |
| cosign | 3.1.4 | Phase 3 |
| restic | 0.19.1 | Phase 4 |
| AWS CLI | 2.40.2 | Phase 9 |

cosign and restic ship with versioned exe names. Hard links named `cosign.exe` and `restic.exe` live in
`C:\avk\bin`, which is on the user PATH. **After `winget upgrade` of either tool, re-create the hard link.**

## TLS on this machine

Norton re-signs HTTPS with its own root certificate, which only the Windows certificate store trusts.
`avk certs refresh` exports the Windows roots to `%AVK_HOME%\certs\host-roots.pem`.

| Tool | Result | Fix |
|---|---|---|
| git, Docker pull, cosign, restic, Python `avk`, pip, GitHub CLI | works as is | none |
| Terraform on the host | downloads work, but **every provider fails to load**: Norton intercepts Terraform's localhost TLS to its plugins (`x509: certificate signed by unknown authority`) | run Terraform in Docker: `avk tf ...` uses `hashicorp/terraform:1.17.1` with the CA bundle mounted |
| AWS CLI | fails (`CERTIFICATE_VERIFY_FAILED`) | user env var `AWS_CA_BUNDLE=%AVK_HOME%\certs\host-roots.pem` (set) |
| Inside containers | fails (`unable to get local issuer certificate`) | mount the PEM read-only and point the container's tool at it. Tested: curl with `--cacert` / `CURL_CA_BUNDLE` (note: this curl ignores `SSL_CERT_FILE`). Untested until needed: `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` for Python, a truststore import for Java/Maven, `NODE_EXTRA_CA_CERTS` for Node |
| GitHub CLI | works as is (signed in, scopes include `workflow`) | none |

## PC settings (Phase 0)

- Sleep and hibernate on AC power: never. The screen turns off after 15 minutes.
- WSL 2 memory capped at 12 GB in `%USERPROFILE%\.wslconfig`. It takes effect at the next Docker or WSL
  restart.
- Docker Desktop's own "start at sign-in" setting is off; the logon task starts Docker Desktop itself when
  it is not running. Prod therefore comes back only after the owner logs in.
- Disk encryption: BitLocker reports no protection on C:. Check Settings > Privacy & security > Device
  encryption (owner decision; not changed).

## Cloudflare edge (`infra/terraform`)

Terraform manages the zone settings (HTTPS only, TLS 1.2+, short HSTS), the `avk-prod` tunnel and its
ingress, one proxied CNAME per host in `platform.yaml`, the `www` → apex redirect, and the one free
rate-limiting rule. State lives in `%AVK_HOME%\tfstate` because it holds the tunnel secret.

```powershell
& $avk -m avk tf plan          # saves a plan; review it
& $avk -m avk tf apply --yes   # applies exactly that plan (refused if older than 1 hour)
```

Secrets: `%AVK_HOME%\secrets\platform\cloudflare.env` (keys in `runtime/secrets.example/`). After an apply,
the tunnel token is written to `%AVK_HOME%\secrets\platform\tunnel.token`.

## Prod stack (`runtime/compose.prod.yaml`)

`avk prod up|down|status` runs `cloudflared` (outbound tunnel, token read from a secret file), `traefik`
(routes by Host header, file provider, no Docker socket) and `pg-prod` (Postgres) on the private `avk-prod`
network. Images are pinned by digest; containers are read-only, drop all capabilities and have memory limits.
Traefik is published only on `127.0.0.1:4900`. Routes live in the Docker volume `avk-prod-routes`, written by the deployer.
Checked 2026-11-17: tunnel healthy with 4 connections; `https://hello.avkaiexplorer.com` answers from this PC;
HTTP redirects to HTTPS; `www` redirects to the apex keeping path and query; DNS shows only Cloudflare addresses.

## Ports

All bound to `127.0.0.1`. Platform: 4800 stg index, 4810 Keycloak (stg), 4811/4812 Mailpit, 4900 prod Traefik
(local), 4910 Keycloak (prod) admin, 4950 Grafana, 5440 pg-stg. One stg port per app from 4820-4899.

## Cost register

| Service | Free-tier limit that matters | Past the limit | Verified on |
|---|---|---|---|
| Domain | renewal yearly | site and email stop | |
| Cloudflare | 1 rate-limiting rule; first-level subdomain TLS | paid add-ons | |
| GitHub | free gates on public repos only | need paid plan | |
| Cloudflare Email Routing | free; 200 rules and 200 destination addresses | n/a at this scale | 2026-11-24 |
| Resend (outbound mail) | free: 3,000 mails/month, 100/day, 3 domains | Pro $20/month | 2026-11-24 |
| (more rows added as each phase signs up for a service) | | | |

## Email

Inbound business addresses forward through Cloudflare Email Routing; app mail is sent through Resend from
`notify.avkaiexplorer.com`. `avk mail check` validates MX, SPF, DKIM and DMARC. Templates live in `email/templates/`
(Jinja2, transactional only). Follow-up: add a bounce/complaint webhook receiver with the first app that sends mail.
