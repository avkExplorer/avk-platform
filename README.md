# AVK app platform

A reusable deployment platform for apps on avkaiexplorer.com. Each app gets a local non-prod environment, a
prod hostname, a security-gated GitHub Actions pipeline, a database, login, business email and legal pages.

**Status:** Phase 0 (prerequisites) built; reboot test pending. Phases: foundation (DNS and tunnel), email, pipeline, runtime, auth,
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
| git, Docker pull, Terraform, cosign, restic, Python `avk`, pip | works as is | none |
| AWS CLI | fails (`CERTIFICATE_VERIFY_FAILED`) | user env var `AWS_CA_BUNDLE=%AVK_HOME%\certs\host-roots.pem` (set) |
| Inside containers | fails (`unable to get local issuer certificate`) | mount the PEM read-only and point the container's tool at it. Tested: curl with `--cacert` / `CURL_CA_BUNDLE` (note: this curl ignores `SSL_CERT_FILE`). Untested until needed: `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE` for Python, a truststore import for Java/Maven, `NODE_EXTRA_CA_CERTS` for Node |
| GitHub CLI | not yet tested (needs `gh auth login`) | none expected |

## PC settings (Phase 0)

- Sleep and hibernate on AC power: never. The screen turns off after 15 minutes.
- WSL 2 memory capped at 12 GB in `%USERPROFILE%\.wslconfig`. It takes effect at the next Docker or WSL
  restart.
- Docker Desktop's own "start at sign-in" setting is off; the logon task starts Docker Desktop itself when
  it is not running. Prod therefore comes back only after the owner logs in.
- Disk encryption: BitLocker reports no protection on C:. Check Settings > Privacy & security > Device
  encryption (owner decision; not changed).

## Ports

All bound to `127.0.0.1`. Platform: 4800 stg index, 4810 Keycloak (stg), 4811/4812 Mailpit, 4900 prod Traefik
(local), 4910 Keycloak (prod) admin, 4950 Grafana, 5440 pg-stg. One stg port per app from 4820-4899.

## Cost register

| Service | Free-tier limit that matters | Past the limit | Verified on |
|---|---|---|---|
| Domain | renewal yearly | site and email stop | |
| Cloudflare | 1 rate-limiting rule; first-level subdomain TLS | paid add-ons | |
| GitHub | free gates on public repos only | need paid plan | |
| (more rows added as each phase signs up for a service) | | | |
