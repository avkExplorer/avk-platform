# Managed WAF (free plan) and the off-box placeholder sites on Cloudflare Pages (apex and status).
# Pages serves these even when the PC is off. Content is uploaded by `avk landing deploy`, not by Terraform.

# ---------------------------------------------------------------------------------------- managed WAF

resource "cloudflare_ruleset" "waf_managed" {
  zone_id = var.zone_id
  name    = "avk managed WAF"
  kind    = "zone"
  phase   = "http_request_firewall_managed"

  rules = [{
    description = "Cloudflare Free Managed Ruleset"
    expression  = "true"
    action      = "execute"
    enabled     = true
    action_parameters = {
      id = "77454fe2d30c4220b5701f6fdfb893ba" # Cloudflare Free Managed Ruleset
    }
  }]
}

# ------------------------------------------------------------------------------------ Pages placeholders

resource "cloudflare_pages_project" "landing" {
  account_id        = var.account_id
  name              = "avk-landing"
  production_branch = "main"
}

resource "cloudflare_pages_domain" "apex" {
  account_id   = var.account_id
  project_name = cloudflare_pages_project.landing.name
  name         = local.domain
}

resource "cloudflare_pages_domain" "status" {
  account_id   = var.account_id
  project_name = cloudflare_pages_project.landing.name
  name         = "status.${local.domain}"
}

resource "cloudflare_dns_record" "apex" {
  zone_id = var.zone_id
  name    = local.domain
  type    = "CNAME" # flattened by Cloudflare; coexists with the Namecheap MX records until Phase 2
  content = "${cloudflare_pages_project.landing.name}.pages.dev"
  proxied = true
  ttl     = 1
  comment = "avk: landing placeholder on Pages (managed by Terraform)"
}

resource "cloudflare_dns_record" "status" {
  zone_id = var.zone_id
  name    = "status.${local.domain}"
  type    = "CNAME"
  content = "${cloudflare_pages_project.landing.name}.pages.dev"
  proxied = true
  ttl     = 1
  comment = "avk: status placeholder on Pages until Phase 7 (managed by Terraform)"
}
