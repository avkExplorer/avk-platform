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

## Rotate a secret

```powershell
& $avk -m avk secrets init hello --env prod --rotate runtime     # or migrator, or ro; values are never printed
& $avk -m avk db create hello --env prod                         # puts the new password into the database role
& $avk -m avk deploy hello --force                               # fresh containers pick up the new env file
```
Do the same with `--env stg` for stg. The tunnel token, Cloudflare token, mail key and GitHub token are rotated with
their own scripts (`tools\set-*.ps1`); each section above says which. After rotating the backup password, make a new
backup immediately and update the copy in your password manager first.

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

## Rebuild the platform on a new machine

1. Install git, Docker Desktop, Python 3.12+, restic, cosign, GitHub CLI; clone `avk-platform`; create `C:\avk` and the venv
   (see the README quick start).
2. Plug in the backup drive, or set up R2 access, and put the restic password (from your password manager) in
   `secrets\platform\backup.env` as `RESTIC_PASSWORD=...` (plus the `R2_*` lines for off-site).
3. Restore the files: `restic -r <repo> restore latest --tag avk --target C:\restore`, then copy `files\secrets`,
   `files\tfstate`, `files\state` and `files\private` from the restored tree back into `C:\avk`.
4. `avk prod up` creates the stack; then load the databases:
   `docker cp ...dump avk-prod-pg-prod-1:/tmp/` and `pg_restore -U postgres -d <db> --create ...` (or use
   `avk backup restore <app> --from local --to prod --confirm ...` once the roles exist: restore `globals.sql` first
   with `psql -U postgres -f globals.sql`).
5. Re-register the scheduled tasks (`tools\register-logon-task.ps1`, `tools\register-tasks.ps1`) and re-run
   `tools\set-github-token.ps1` (the token is in the backup, but a fresh one is cleaner).
6. `avk deploy-agent` and `avk prod status`; the tunnel token in `secrets\platform\tunnel.token` reconnects Cloudflare.

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