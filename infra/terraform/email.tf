# Inbound business email with Cloudflare Email Routing: aliases forward to the owner's mailbox; everything
# else is dropped. The mailbox address is private (var.forward_to_mailbox, from secrets/platform/owner.tfvars).
# Outbound mail (Resend) is added separately once the owner has created the provider account.

locals {
  mail_aliases = ["hello", "support", "billing", "security", "privacy", "admin", "postmaster", "abuse", "dmarc"]
}

variable "forward_to_mailbox" {
  description = "Mailbox that receives all business aliases. Private: never commit its value."
  type        = string
  sensitive   = true
}

# Turns Email Routing on for the apex; Cloudflare adds its MX records and SPF include.
resource "cloudflare_email_routing_settings" "root" {
  zone_id = var.zone_id

  lifecycle {
    ignore_changes = [support_subaddress] # provider reports false then null: perpetual diff otherwise
  }
}

resource "cloudflare_email_routing_address" "owner" {
  account_id = var.account_id
  email      = var.forward_to_mailbox
}

resource "cloudflare_email_routing_rule" "alias" {
  for_each = toset(local.mail_aliases)

  zone_id = var.zone_id
  name    = "avk: ${each.key}@ to owner"
  enabled = true
  matchers = [{
    type  = "literal"
    field = "to"
    value = "${each.key}@${local.domain}"
  }]
  actions = [{
    type  = "forward"
    value = [var.forward_to_mailbox]
  }]

  depends_on = [cloudflare_email_routing_settings.root]
}

resource "cloudflare_email_routing_catch_all" "drop" {
  zone_id = var.zone_id
  name    = "avk: drop unknown addresses"
  enabled = true
  matchers = [{
    type = "all"
  }]
  actions = [{
    type = "drop"
  }]

  depends_on = [cloudflare_email_routing_settings.root]
}

# DMARC starts in monitor mode; reports arrive at dmarc@ (see the RUNBOOK timeline to tighten it).
resource "cloudflare_dns_record" "dmarc" {
  zone_id = var.zone_id
  name    = "_dmarc.${local.domain}"
  type    = "TXT"
  content = "\"v=DMARC1; p=none; rua=mailto:dmarc@${local.domain}; adkim=s; aspf=s\""
  ttl     = 3600
  comment = "avk: DMARC monitor mode (managed by Terraform)"
}
