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
