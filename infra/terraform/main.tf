# Cloudflare edge for avkaiexplorer.com: zone settings, the prod tunnel, one DNS record per public host,
# the www -> apex redirect and the single free rate-limiting rule.
#
# Not managed here yet: the apex record (Phase 1 step 5, Cloudflare Pages) and the Namecheap email-forwarding
# MX/SPF records, which Phase 2 replaces with Cloudflare Email Routing.

locals {
  platform = yamldecode(file("${path.module}/../../platform.yaml"))
  domain   = local.platform.domain

  # Every public host of every non-retired app, plus platform hosts added in later phases.
  app_hosts = toset(flatten([
    for app in coalesce(local.platform.apps, []) : coalesce(app.hosts, []) if try(app.status, "stg") != "retired"
  ]))
  platform_hosts = toset(try(local.platform.platform_hosts, []))
  tunnel_hosts   = setunion(local.app_hosts, local.platform_hosts)

  # Paths that get the strict edge rate limit (Keycloak and app auth/contact endpoints).
  rate_limited_paths = ["/login", "/signup", "/register", "/reset", "/forgot", "/contact",
  "/realms/avk/protocol/openid-connect/auth", "/realms/avk/login-actions"]
}

# ------------------------------------------------------------------------------------------ zone settings

resource "cloudflare_zone_setting" "always_use_https" {
  zone_id    = var.zone_id
  setting_id = "always_use_https"
  value      = "on"
}

resource "cloudflare_zone_setting" "min_tls_version" {
  zone_id    = var.zone_id
  setting_id = "min_tls_version"
  value      = "1.2"
}

resource "cloudflare_zone_setting" "ssl" {
  zone_id    = var.zone_id
  setting_id = "ssl"
  # Edge certificate to visitors; the tunnel itself is encrypted, so no origin certificate is needed.
  value = "full"
}

resource "cloudflare_zone_setting" "hsts" {
  zone_id    = var.zone_id
  setting_id = "security_header"
  value = {
    strict_transport_security = {
      enabled            = true
      max_age            = var.hsts_max_age
      include_subdomains = false
      preload            = false
      nosniff            = true
    }
  }
}

# ------------------------------------------------------------------------------------------------ tunnel

resource "random_id" "tunnel_secret" {
  byte_length = 32
}

resource "cloudflare_zero_trust_tunnel_cloudflared" "prod" {
  account_id    = var.account_id
  name          = "avk-prod"
  config_src    = "cloudflare"
  tunnel_secret = random_id.tunnel_secret.b64_std
}

resource "cloudflare_zero_trust_tunnel_cloudflared_config" "prod" {
  account_id = var.account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.prod.id

  config = {
    # Every listed host goes to Traefik on the prod Docker network; Traefik routes by Host header.
    ingress = concat(
      [for host in sort(tolist(local.tunnel_hosts)) : { hostname = host, service = "http://traefik:80" }],
      [{ service = "http_status:404" }],
    )
  }
}

data "cloudflare_zero_trust_tunnel_cloudflared_token" "prod" {
  account_id = var.account_id
  tunnel_id  = cloudflare_zero_trust_tunnel_cloudflared.prod.id
}

resource "cloudflare_dns_record" "tunnel_host" {
  for_each = local.tunnel_hosts

  zone_id = var.zone_id
  name    = each.value
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.prod.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
  comment = "avk: tunnel avk-prod (managed by Terraform)"
}

# ------------------------------------------------------------------------------------------- www redirect

resource "cloudflare_dns_record" "www" {
  zone_id = var.zone_id
  name    = "www.${local.domain}"
  type    = "AAAA"
  content = "100::" # discard prefix: the redirect rule answers before any origin is contacted
  proxied = true
  ttl     = 1
  comment = "avk: www -> apex redirect (managed by Terraform)"
}

resource "cloudflare_ruleset" "redirects" {
  zone_id = var.zone_id
  name    = "avk redirects"
  kind    = "zone"
  phase   = "http_request_dynamic_redirect"

  rules = [{
    description = "www to apex"
    expression  = "(http.host eq \"www.${local.domain}\")"
    action      = "redirect"
    action_parameters = {
      from_value = {
        status_code           = 301
        preserve_query_string = true
        target_url = {
          expression = "concat(\"https://${local.domain}\", http.request.uri.path)"
        }
      }
    }
  }]
}

# ------------------------------------------------------------------------------------------ rate limiting

resource "cloudflare_ruleset" "rate_limit" {
  zone_id = var.zone_id
  name    = "avk rate limits"
  kind    = "zone"
  phase   = "http_ratelimit"

  rules = [{
    description = "auth and contact paths"
    expression  = join(" or ", [for p in local.rate_limited_paths : "starts_with(http.request.uri.path, \"${p}\")"])
    action      = "block"
    ratelimit = {
      characteristics     = ["cf.colo.id", "ip.src"]
      period              = 10
      requests_per_period = var.rate_limit_requests
      mitigation_timeout  = 10
    }
  }]
}
