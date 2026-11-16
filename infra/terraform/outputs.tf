output "tunnel_id" {
  value = cloudflare_zero_trust_tunnel_cloudflared.prod.id
}

output "tunnel_hosts" {
  value = sort(tolist(local.tunnel_hosts))
}

# Read by `avk tf apply` and written to %AVK_HOME%\secrets\platform\tunnel.token for the cloudflared container.
output "tunnel_token" {
  value     = data.cloudflare_zero_trust_tunnel_cloudflared_token.prod.token
  sensitive = true
}
