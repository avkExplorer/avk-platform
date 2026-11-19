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
