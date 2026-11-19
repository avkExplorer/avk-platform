# Security notes

## Reporting

A `security@` address arrives with Phase 2 (email). Until then, open a private security advisory on the
GitHub repository once it exists.

## Scans

| Scan | Date | Result |
|---|---|---|
| Checkov 3.4.5 on `infra/` (terraform) and the whole repo (secrets) | 2026-11-18 | 0 passed, 0 failed, 0 skipped. Checkov ships no policies for Cloudflare resources and found no secrets, so this is "nothing applicable", not proof of safety. |
| Compose file | 2026-11-18 | Checkov has no compose framework. Covered by a unit test (every image pinned by digest) and by review: read-only filesystems, all capabilities dropped, no-new-privileges, memory limits, Traefik published on 127.0.0.1 only. |

Stronger checks (Trivy for images and config, CodeQL, secret scanning) arrive with the Phase 3 pipeline.

## Posture today

- No inbound ports; the tunnel is outbound only. Cloudflare terminates TLS (1.2 minimum, HTTPS enforced).
- HSTS is short (300 s) until HTTPS is proven everywhere; no preload.
- Free managed WAF ruleset and one rate-limit rule on login-like paths.
- Secrets and Terraform state stay in `%AVK_HOME%`, outside git. The tunnel token is read by a container
  from a secret file, not from the environment.

## Accepted risks

- Single PC: no redundancy; apps are down when it is off.
- Disk encryption is the owner's decision (not enabled when last checked).
