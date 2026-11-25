# Runbook

All commands are PowerShell from the repo root. Set `$avk = "$env:AVK_HOME\venv\Scripts\python.exe"` first.

## Start and stop prod

```powershell
& $avk -m avk prod status   # cloudflared and traefik should say healthy
& $avk -m avk prod up       # start (also runs at logon via the scheduled task "AVK prod at logon")
& $avk -m avk prod down     # stop; apex and status pages stay up (they are on Cloudflare Pages)
```

If `up` fails: start Docker Desktop, wait until it reports running, retry. Logon log:
`%AVK_HOME%\logs\prod-at-logon.log`.

## Rotate the Cloudflare API token

1. Cloudflare dashboard > My Profile > API Tokens: create a new token with the same permissions as the old one
   (zone DNS, zone settings, zone WAF, account tunnel, account Pages).
2. `tools\set-cloudflare-secrets.ps1` stores it without echoing it to the screen.
3. `& $avk -m avk tf plan` must succeed (and show no changes).
4. Delete the old token in the dashboard.

## Change Cloudflare settings

```powershell
& $avk -m avk tf plan          # read the plan; it saves it
& $avk -m avk tf apply --yes   # applies exactly that plan; refused if older than 1 hour
```

## Redeploy the placeholder pages

Edit `landing/`, then `& $avk -m avk landing deploy` (uploads to the `avk-landing` Pages project).

## The tunnel is down but containers are healthy

`docker logs avk-prod-cloudflared-1 --tail 50`. A bad or rotated tunnel token shows as an auth error: re-run
`avk tf apply` (rewrites `secrets\platform\tunnel.token`), then `avk prod up`.

## Deploy an approved release

The normal path needs nothing from you except the approval on GitHub. Merge to `main`, wait for the release run to stop
at `prod`, review it (commit, digest, test totals) and approve it. Within 5 minutes the deploy agent on this PC sees the
Deployment and deploys it.

```powershell
& $avk -m avk deploy hello            # same path right now, instead of waiting for the next poll
& $avk -m avk deploy-agent            # what the scheduled task runs every 5 minutes
Get-Content $env:AVK_HOME\logs\deployer.log -Tail 20      # one JSON line per step
Get-Content $env:AVK_HOME\logs\alerts.log -Tail 10        # refusals and failures
```

What the agent does, in order: (1) reads the newest `prod` Deployment with no final status. This is only a trigger.
(2) proves the four trust checks itself: the app is registered, the build signature is from the app's repository, the
owner's approval attestation matches this exact digest, and the pulled image carries that digest. (3) runs the database
migration as a one-shot container with the migrator credentials. (4) starts the idle colour (`hello-blue` or
`hello-green`). (5) pre-switch smoke test against the idle colour. (6) switches Traefik atomically. (7) post-switch smoke
test through local Traefik and the public URL. (8) records the state and stops the old colour after 10 minutes.

Where it can stop, and what that means (the GitHub Deployment shows `failure` with the step):

| Step shown | Meaning | Production |
|---|---|---|
| `trust:registered`, `trust:signed-build`, `trust:approved`, `trust:same-digest` | not signed, not approved, or not this app | untouched, nothing pulled or started |
| `contract`, `secrets` | `avk.yaml` unreadable at that commit, or secret files missing | untouched |
| `pull`, `migration`, `start`, `readiness` | the image or its database step failed | untouched (the idle colour is removed) |
| `pre-switch-smoke` | the new version failed its checks | untouched: live traffic never reached it |
| `post-switch-smoke` | the new version failed after the switch | route restored to the previous colour |

A refused deploy is the system working. Read `alerts.log` for the reason, fix the release or approve the right one.

## Roll back

```powershell
& $avk -m avk rollback hello      # back to the previous digest in state\hello.json
```
It re-runs all four trust checks (the previous digest's approval attestation still exists), starts the other colour,
smoke-tests it and switches. It does **not** run down-migrations, so schema changes must stay backward-compatible with
the previous image (an app rule in `specs/00-architecture.md`).

## Rotate each secret

| Secret | How | Then |
|---|---|---|
| Cloudflare API token | `tools\set-cloudflare-secrets.ps1` | `avk tf plan` works |
| GitHub token | `tools\set-github-token.ps1` | `avk deploy-agent` prints `hello: nothing to deploy` |
| Mail key (Resend) | `tools\set-mail-secret.ps1` (see Business email) | `avk mail test-send --to <you> --yes` |
| An app's database passwords | `avk secrets init <app> --env prod --rotate runtime` (or `migrator`, `ro`), `avk db create <app> --env prod`, `avk deploy <app> --force` | the app answers `pong` |
| Keycloak client secret | `avk auth client <app> --env prod` (see Login), then `avk deploy <app> --force` | log in once |
| Keycloak admin passwords | change in the console, then update `secrets\prod\keycloak-admin.env` | `avk auth apply --env prod` |
| Backup (restic) password | new password in the password manager first, then `restic key passwd` on both repositories, update `backup.env` | `avk backup run` then `verify` |
| Heartbeat URLs | recreate the heartbeat in Better Stack, `tools\set-heartbeat-urls.ps1` | the next run pings |
| Grafana admin | delete `secrets\prod\grafana.env`, `avk obs down`, `avk obs up` | log in |

Do the same with `--env stg` for stg. A container reads its env file only when it is created, so after rotating, redeploy (`avk deploy <app> --force`).

## Backups

Nightly at 02:30 (and whenever you run it): `& $avk -m avk backup run`. One restic snapshot holds a dump of every prod
database, the database roles, and copies of `secrets`, `tfstate`, `state` and `private`. It goes to the backup drive
(`D:\development_backup\avk\restic`) and, once `tools\set-r2-secrets.ps1` has been run, to Cloudflare R2. Both are
encrypted with the restic password in `secrets\platform\backup.env`. **Keep a copy of that password outside this PC:
without it no backup can be restored.** Retention: 7 daily, 4 weekly, 6 monthly.

If the drive is unplugged the run fails, writes `logs\alerts.log` and `state\backup-last.json` (`"ok": false`).
**Recovery point: up to 24 hours of data** (nightly dumps). Point-in-time recovery is not set up.

Every Sunday 03:30, and on demand, `& $avk -m avk backup verify` runs `restic check`, restores the newest snapshot into a
throwaway Postgres on its own network (`avk-restore`, no host port, temporary volume), compares row counts with prod and
removes the container and volume. Prod data never enters stg.

## Restore a database

```powershell
& $avk -m avk backup restore hello --from local --to scratch     # into a throwaway container to look at it
& $avk -m avk backup restore hello --from offsite --to scratch   # the same from R2
& $avk -m avk backup restore hello --from local --to prod --confirm "restore hello to prod"
```
`--to prod` replaces the live database. It first saves a fresh dump of the current data
(`%AVK_HOME%\backups\pre-restore-<app>.dump`), restores with `--clean`, then re-applies the role privileges. The typed
phrase is required so it cannot run by accident.

## Recover after a reboot or a power cut

Log in. The logon task starts Docker Desktop, waits for the engine, runs `avk prod up` (tunnel, Traefik, pg-prod) and
starts the live colour of each app; the deploy agent resumes within 5 minutes. Prod is back about a minute after login.
Check: `& $avk -m avk prod status` and `curl https://hello.avkaiexplorer.com/api/v1/ping`. Nothing runs while you are
logged out, by design. If Docker Desktop did not start: open it once, then `& $avk -m avk prod up`.

## The PC is dead: rebuild on a new machine

**Goal:** prod back on a new Windows PC from the off-site backup. Expect 2 to 3 hours. **Data loss: up to 24 hours** (the nightly
dump). Login accounts, hello's data, every secret and the platform state come back; logs, dashboards and Docker volumes do not
(by design: they can be rebuilt).

### Keep these OUTSIDE the PC (password manager), or none of the rest is possible
1. The restic backup password (`RESTIC_PASSWORD` in `secrets\platform\backup.env`).
2. The Cloudflare R2 backup access: account id, bucket name, access key id and secret (`R2_*` in the same file). *The drill found this was
   not written down anywhere else: the backup holds its own keys, which are useless if the PC is gone.*
3. Logins (with 2FA recovery codes) for GitHub, Cloudflare, Resend, Better Stack and the domain registrar.
4. A printed or offline copy of this section.

### Steps
1. **Tools.** `winget install --source winget Git.Git Docker.DockerDesktop Python.Python.3.12 GitHub.cli Hashicorp.Terraform restic.restic sigstore.cosign EclipseAdoptium.Temurin.21.JDK`.
   Put `restic.exe` and `cosign.exe` on the PATH (`C:\avk\bin`). In `%USERPROFILE%\.wslconfig` put `[wsl2]` and `memory=12GB`, then
   `wsl --shutdown`. Start Docker Desktop once and turn on "Start Docker Desktop when you sign in".
2. **Code and venv.** `git clone https://github.com/avkExplorer/avk-platform`, set the user variable `AVK_HOME=C:\avk`,
   `python -m venv C:\avk\venv`, then `C:\avk\venv\Scripts\python.exe -m pip install -e cli[test]`. `$avk = "$env:AVK_HOME\venv\Scripts\python.exe"`.
3. **Backup access.** Write `C:\avk\secrets\platform\backup.env` with `RESTIC_PASSWORD=...` and the four `R2_*` lines (or plug the backup drive in
   and leave the R2 lines out).
4. **Restore the files.** Local drive: `restic -r D:\development_backup\avk\restic restore latest --tag avk --target C:\restore`.
   Off-site: set `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `RESTIC_PASSWORD`, then
   `restic -r s3:https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com/<R2_BUCKET>/avk restore latest --tag avk --target C:\restore`.
   Copy `files\secrets`, `files\tfstate`, `files\state` and `files\private` from under `C:\restore` into `C:\avk`.
5. **Start prod.** `& $avk -m avk certs refresh` (builds the PC's certificate file and Java truststore), then `& $avk -m avk prod up`. This builds the
   Keycloak image, starts the tunnel, Traefik, pg-prod and Keycloak (on an empty login database for now). The tunnel token from the backup
   reconnects Cloudflare. (`prod up` also creates the certificate file itself if it is missing, so Docker never makes a folder of it.)
6. **Restore the data.** The database roles come back by themselves (their passwords are in the restored secret files). For each database:
   `& $avk -m avk db create hello --env prod`, then
   `& $avk -m avk backup restore hello --from offsite --to prod --confirm "restore hello to prod"` and
   `& $avk -m avk backup restore keycloak --from offsite --to prod --confirm "restore keycloak to prod"` (use `--from local` with the drive).
   Then `docker restart avk-prod-keycloak-1` and `& $avk -m avk auth apply --env prod`.
7. **Apps.** `& $avk -m avk deploy hello --force` redeploys the digest recorded in the restored `state\hello.json` (all trust checks run again).
8. **Automation.** `powershell -ExecutionPolicy Bypass -File tools\register-logon-task.ps1`, then `tools\register-tasks.ps1`, then
   `tools\set-github-token.ps1` (a fresh token is cleaner than the restored one).
9. **Check.** `curl https://hello.avkaiexplorer.com/api/v1/ping`, log in at `https://hello.avkaiexplorer.com/profile`, `& $avk -m avk doctor`,
   `& $avk -m avk backup run` then `backup verify`. Heartbeat URLs came back in `secrets\platform\heartbeats.env`, so the Better Stack monitors
   and heartbeats keep working without changes. Norton's TLS scanning may not exist on the new PC; then `certs refresh` simply exports fewer roots.

### What the drill found (2026-11-25, against the real snapshot)
| Gap | Fix |
|---|---|
| The R2 keys and restic password lived only inside the backup | Write them in the password manager (list above) |
| `avk backup restore keycloak` re-applied the wrong roles (it treated the login database like an app) | Fixed in code and tested: it re-applies the single `keycloak` owner role |
| prod's Keycloak mounts `C:\avk\certs\host-roots.pem`; on a new PC Docker would create a folder with that name and the login service would not start | Fixed: `prod up` creates the file first |
| Not in the backup, so redo by hand: Docker Desktop and WSL settings, scheduled tasks, Norton, `gh auth login`, installed tools | Steps 1 and 8 above |
| Not in the backup by design: Grafana/Prometheus/Loki data, `logs\` (alert history), Traefik route volume, the Keycloak image | Rebuilt by `prod up`, `obs up` and the deployer |
| The truststore code reached prod only through a side branch, so a later deploy ran without it | Found on 2026-11-25 and fixed: the live container is redeployed and the code is on `main` (PR below); `avk deploy` always uses the checked-out code, so keep the platform folder on `main` |
| Restoring the login and hello databases | **Proven 2026-11-25:** both restored into throwaway containers from the local snapshot and from R2 (realms `avk` and `master`, users present) |

## Stg

```powershell
& $avk -m avk stg up hello --build   # build from ..\hello and start: http://127.0.0.1:4820/ (index on :4800, mail on :4811)
& $avk -m avk stg up hello --digest sha256:...    # run a published image instead
& $avk -m avk seed hello             # synthetic data into stg only (refuses prod)
& $avk -m avk stg down
```

## Business email

Inbound: Cloudflare Email Routing forwards `hello`, `support`, `billing`, `security`, `privacy`, `admin`,
`postmaster`, `abuse` and `dmarc` to the owner's mailbox; any other address is dropped. Outbound for apps:
Resend, from `noreply@notify.avkaiexplorer.com`. Check the DNS side any time: `& $avk -m avk mail check`.

### Rotate the mail credentials

1. Resend dashboard > API Keys: create a new key with **Sending access** limited to `notify.avkaiexplorer.com`.
2. Run `tools\set-mail-secret.ps1` (hidden input; writes `%AVK_HOME%\secrets\prod\platform-mail.env`).
3. Send one test (`avk mail test-send --to <your address> --yes`) and confirm it arrives.
4. Delete the old key in Resend.

### DMARC timeline

The record is `p=none` (monitor only) with reports to `dmarc@`. Tighten it only after reports show real mail passing.

| Step | When | Check before moving on |
|---|---|---|
| `p=none` | now | `avk mail check` ok; test mail shows spf, dkim and dmarc = pass |
| `p=quarantine; pct=25`, then 100 | after 2 to 4 weeks of clean reports | every legitimate sender (Cloudflare forwarding, Resend, any new tool) passes SPF or DKIM aligned |
| `p=reject` | after 2 more clean weeks | same check; keep `rua` reports flowing |

Edit the `_dmarc` record in `infra/terraform/email.tf`, `avk tf plan`, apply. Adding any new sender later needs its
SPF include or DKIM first, or its mail will be quarantined.

### Suppression list

Resend automatically stops sending to addresses that bounced or complained. View them in the Resend dashboard
(Suppressions). Never re-add one by hand. A webhook receiver for bounces is a follow-up for the first app that
sends mail.

## Login (Keycloak)

URLs: stg `http://127.0.0.1:4810` (Mailpit on :4811 shows the emails), prod `https://auth.avkaiexplorer.com` for users and
`http://127.0.0.1:4910` for the admin console (this PC only). Admin passwords are in
`%AVK_HOME%\secrets\<env>\keycloak-admin.env` (`AVK_ADMIN_PASSWORD`); keep a copy in the password manager.

```powershell
& $avk -m avk auth build              # build the Keycloak image (first time, or after a theme or version change)
& $avk -m avk auth init --env prod    # secrets + database (also done by `avk prod up` / `avk stg up`)
& $avk -m avk auth apply --env prod   # apply realm-avk.json; a second run says "no changes"
& $avk -m avk auth client hello --env prod
& $avk -m avk auth test --env stg     # browser tests (stg only: they create and delete users)
```

### First login as the console admin
1. Open `http://127.0.0.1:4910/admin/master/console/`, user `avk-admin`, the password from `keycloak-admin.env`.
2. Keycloak makes you enrol an authenticator app: scan the QR code, type the code. Save the recovery steps in the password manager.

If the console says "Timeout when waiting for 3rd party check iframe message", the master realm's frontend URL is not the local
address (the console asks the public host, which answers 404 on purpose). Run `avk auth apply --env prod`: it sets the master
realm's frontend URL to `http://127.0.0.1:4910`. Always open the console at `http://127.0.0.1:4910`, never at the `auth.` name.

### Add an admin (a user who may open an app's `/admin`)
Console > realm `avk` > Users > the person > Role mapping > Assign role > `admin`. At their next login they are forced to enrol OTP.

### Reset a user's MFA
Console > realm `avk` > Users > the person > Credentials > delete the "otp" credential. A normal user can then log in with a
password only; an admin must enrol again at the next login.

### Rotate a client secret
`avk auth client <app> --env <env> --rotate` writes a new secret to `secrets\<env>\<app>.env`; then
`avk deploy <app> --force` so the app picks it up (see "Rotate a secret").

### Change the login branding
Colour: the variables at the top of `runtime/keycloak/themes/avk/login/resources/css/avk.css` and `.../account/resources/css/avk.css`.
Logo: replace `.../account/resources/img/logo.svg`; for the login page set `--avk-logo-url` and `--avk-logo-display: block`.
Then `avk auth build`, restart Keycloak (`docker compose -p avk-prod -f runtime/compose.prod.yaml up -d --force-recreate keycloak`
or `avk stg up`), and run `avk auth apply`.

### Upgrade Keycloak
1. Read the release notes (security fixes land in patch releases). 2. Change the digest in `runtime/keycloak/Dockerfile` and
`KEYCLOAK_VERSION` in `cli/avk/auth.py` and the `image:` tags in both compose files. 3. `avk auth build`, then
`avk stg up` and `avk auth test --env stg`. 4. Run a backup (`avk backup run`), then `avk prod up` (Keycloak migrates its own
database on start). 5. `avk auth apply --env prod` must say "no changes" or list sensible ones.

### Keycloak locked out (the CLI cannot log in)
Stop Keycloak (`docker stop avk-prod-keycloak-1`), then create a temporary admin with a one-off container that has the same
image, environment and database, running `bootstrap-admin user` (see the Keycloak "bootstrap-admin-recovery" guide), start
Keycloak again and put the new login in `keycloak-admin.env`.

## Legal pages (draft, not legal advice)
- **Add or change the pages for an app:** edit the `legal:` block in the app's `avk.yaml`, then `python -m avk legal render <app>` and commit the generated files in the app repo. `--check` fails if they are stale.
- **See what is missing:** `render` lists every `[TO COMPLETE]` input (for `hello`: `postal_address`).
- **Remove the DRAFT banner:** only after an attorney/CPA has reviewed the pages, set `legal.reviewed: true` and render again. Do not set it earlier.
- **Contact form mail:** `python -m avk legal smtp <app> --env stg|prod` writes the SMTP settings into the app's secrets (then redeploy: `python -m avk deploy <app> --force`). Stg goes to Mailpit; prod uses Resend.
- **A page moved:** add `old,new` to the app's `redirects.csv`.
- **Retire an API version:** call `SunsetHeaders.apply(...)` in the old version's handlers, announce the date, remove it after.
- **Owner checklist:** `docs/compliance-checklist.md`.

## Docker Desktop stopped (prod is down)
- **Automatic:** the deploy-agent task (every 5 minutes) calls `avk docker-watch` when `docker info` does not answer. If Docker Desktop is not running it starts it (at most once per 10 minutes); if it is running but silent for 15 minutes it stops and starts it once. The containers restart by themselves. Every attempt is written to `C:\avk\logs\alerts.log` and `C:\avk\logs	ask-deploy-agent.log`.
- **By hand:** `python -m avk docker-watch`, or start Docker Desktop yourself. Check `curl https://hello.avkaiexplorer.com/api/v1/ping`.
- **After a recovery:** if a deploy was refused while Docker was down, the agent will not retry it; run `python -m avk deploy <app> --digest <sha256:...>` (same trust checks).
- Phase 7 adds the email alert and the uptime monitor; until then read `alerts.log`.

## Dashboards, metrics and logs (on demand)
- **Start / stop:** `python -m avk obs up` (needs prod up), `python -m avk obs down` (data is kept). Alerts never depend on this stack. Idle cost: about 0.5 GB RAM.
- **Grafana:** http://127.0.0.1:4950, user `admin`, password in `C:\avk\secrets\prod\grafana.env`. Dashboard "App overview" with an `App` selector: request rate, error rate, p50/p95 latency, database connections, JVM memory, Traefik and PostgreSQL panels and the app's logs.
- **After a deploy (new colour):** `python -m avk obs refresh`; the stack picks the new container up within 30 seconds.
- **One request:** every response has an `X-Request-Id` header (Cloudflare's ray id when the caller sent none). `python -m avk logs hello --request-id <id> --since 2h` prints every log line of that request; without `--request-id` it prints the recent lines (`--env stg` for the test copy).
- **What reads what:** Prometheus scrapes each app's `:8081/actuator/prometheus` (private port, never published), Traefik `:8083`, and a read-only Postgres role `avk_monitor`; Alloy tails the container log files (no Docker socket) into Loki (14 days). Prometheus keeps 15 days.
- **Broken?** `python -m avk obs status`; `docker logs avk-obs-<name>-1`.

## Add a new app (by hand until Phase 8 automates it)
1. New repo from the template (`app-template/`), add it to `platform.yaml` (`avk validate`), give it a `wall.json`.
2. `avk secrets init <app> --env stg` and `--env prod`, `avk db create <app> --env stg|prod`.
3. Login: `avk auth client <app> --env stg|prod`, copy `app-template/auth-starter/`. Policies: a `legal:` block, `avk legal render <app>`, `avk legal smtp <app> --env ...`, `app-template/legal-starter/`.
4. `avk stg up <app> --build`; push; the release workflow builds and signs; approve `prod`; `avk deploy-agent`.
5. Add a Better Stack monitor for its `/healthz` and, if it runs scheduled jobs, a heartbeat. `avk tf plan` for its DNS.

## Move prod to a VPS
Only three things change: where `avk prod up` runs, where the deployer runs, and the tunnel connector. (1) Make a small Linux VPS with Docker; clone the repo and
restore `files\secrets|state|tfstate` as in "The PC is dead". (2) Run `avk prod up` and the deploy agent there (a cron job or systemd timer replaces the Windows
tasks; paths use `AVK_HOME`). (3) Stop `cloudflared` on the PC and start it on the VPS with the same tunnel token (Cloudflare sends traffic to whichever connector
is up). Backups, heartbeats and alerts work unchanged; drop the truststore setting if the VPS has no TLS-scanning antivirus.
**Do this before the first real payment or stored customer record** (`docs/compliance-checklist.md`).

## Incident response
1. **Detect.** An alert email, a Better Stack down message, or a user report. `avk health`, `curl https://hello.avkaiexplorer.com/api/v1/ping`, `avk prod status`.
2. **Contain.** Stop the damage first: `avk prod down` for a security problem; `avk rollback <app>` for a bad release; rotate any secret that may have leaked (table above).
3. **Communicate.** Post on the Better Stack status page ("investigating", then updates). For personal-data exposure see `docs/compliance-checklist.md` and tell affected users promptly.
4. **Fix.** `avk logs <app> --request-id <id>`, Grafana (`avk obs up`), then a normal pull-request release.
5. **Write up.** A short note in `private/incidents/<date>.md`: what happened, timeline, cause, what changed so it cannot repeat.

## Alerts: what emails you, and how to test them
- **Who sends:** `avk alert` and every deployer/backup alert email your mailbox through Resend. The same subject is sent at most once an hour. A "resolved" mail follows a health problem when it clears.
- **Local checks (every 5 minutes, in the deploy-agent task):** `python -m avk health` runs them now. They cover disks above 80% and 90%, the backup drive, and prod containers that are stopped, unhealthy or restarting. A container that does not exist at all means prod was taken down on purpose (`avk prod down`) and is not an alert.
- **Outside the PC (Better Stack):** 3 monitors (hello, login, apex) email when a site is down; 3 heartbeats (nightly backup, weekly verify, the deploy agent every 5 minutes with 25 minutes of grace) email when a job goes silent. The public status page is https://avkai-explorer.betteruptime.com/.
- **Tests that were run (2026-11-25):** missed heartbeat (task paused, Better Stack emailed), unplugged backup drive (`backup run` exits 1 and emails, nothing is left in the staging folder; the health check emails once and then "resolved"), manual backup and verify (both heartbeats ping).
- **Repeat a test:** unplug the drive and `python -m avk backup run`; or `Disable-ScheduledTask -TaskPath '\AVK' -TaskName 'AVK deploy agent'` (and Enable it again after the email).

