# Architecture

Status: Phase 1 (foundation). Later phases are marked *planned*.

```
 Owner's PC (Windows 11, Docker Desktop)                      Outside services (free tiers)
 +---------------------------------------------+
 | stg stack (127.0.0.1 only)           planned|   GitHub: repos, Actions, GHCR, prod approval  planned
 | prod stack (no public ports)                |
 |   cloudflared --> traefik --> apps          |   Cloudflare: DNS, edge TLS, WAF, Tunnel,
 |                                             |--outbound-->  Pages (apex, status), Email Routing planned
 | AVK_HOME (C:\avk): secrets, state, tfstate  |   tunnel
 +---------------------------------------------+
```

## Request path in prod

Browser -> Cloudflare edge (TLS, WAF, rate limit) -> tunnel -> `cloudflared` container -> `traefik` on the
private Docker network (routes by Host header) -> the app. Nothing listens on the router, and the home IP
is in no DNS record.

## What lives where

| Piece | Where | Notes |
|---|---|---|
| Public hosts (`<app>.avkaiexplorer.com`) | Tunnel to the PC | one DNS level only (free TLS certificate coverage) |
| Apex and `status.` placeholders | Cloudflare Pages (`avk-landing`) | stay up when the PC is off |
| Cloudflare config | `infra/terraform` | provider pinned; run in Docker via `avk tf` |
| Prod containers | `runtime/compose.prod.yaml` | digest-pinned, read-only, no capabilities, memory limits |
| Secrets and Terraform state | `%AVK_HOME%` outside the repo | never committed |
| App registry | `platform.yaml` | adding a host adds its DNS record and tunnel route |

## Trust rules (enforced from Phase 3)

A prod deploy needs a signed build, a signed owner approval for that exact image digest, and a registered
app. A GitHub Deployment event alone never starts a deploy. See the plan for the full chain.

## Limits

The PC is a single point of failure: prod is down when it is off, asleep or offline. This is a learning and
demo platform, not a paid-service host.
