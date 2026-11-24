# Outbound mail provider (Resend) records for notify.<domain>. Created by Resend's Cloudflare "Auto configure"
# on 2026-11-24, then imported here so Terraform owns them. All values are public DNS data.
# Do not edit values by hand: copy them from the Resend dashboard if the domain is re-created.

resource "cloudflare_dns_record" "resend_dkim" {
  zone_id = var.zone_id
  name    = "resend._domainkey.notify.avkaiexplorer.com"
  type    = "TXT"
  content = "\"p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDc6obeiBf9HY+QujN7F4Bx24hRqrMhZ+zi+1IanN8cJgaC9X7PJqpDzbMBQMGj1dBk4UoVAA8Y3WGHU7hjEj9UII9F1HwPbsVF/N6z9wGfZPMBHZPFq6Hfd2wfLIkE+apjzaxUMwaae/TeJ/e46LcvDVi/h0rXAmbg/Olsztl34QIDAQAB\""
  proxied = false
  ttl     = 3600
  comment = "avk: Resend sending domain (managed by Terraform)"
}

resource "cloudflare_dns_record" "resend_rsend" {
  zone_id = var.zone_id
  name    = "rsend.notify.avkaiexplorer.com"
  type    = "CNAME"
  content = "rsend.forge.rmta.net"
  proxied = false
  ttl     = 3600
  comment = "avk: Resend sending domain (managed by Terraform)"
}

resource "cloudflare_dns_record" "resend_send" {
  zone_id = var.zone_id
  name    = "send.notify.avkaiexplorer.com"
  type    = "CNAME"
  content = "send.forge.rmta.net"
  proxied = false
  ttl     = 3600
  comment = "avk: Resend sending domain (managed by Terraform)"
}

# One-time imports of the records Resend created. Safe to delete after the first apply.
import {
  to = cloudflare_dns_record.resend_dkim
  id = "${var.zone_id}/d0caf8b5ab748fe525be86b811fbafe5"
}

import {
  to = cloudflare_dns_record.resend_rsend
  id = "${var.zone_id}/670dede024ffe4a194431c3a088d63a2"
}

import {
  to = cloudflare_dns_record.resend_send
  id = "${var.zone_id}/3e0636061ea4dd4ea460465442f67240"
}
