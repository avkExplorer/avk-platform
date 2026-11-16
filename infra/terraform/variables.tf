variable "account_id" {
  description = "Cloudflare account id (from secrets/platform/cloudflare.env)."
  type        = string
}

variable "zone_id" {
  description = "Cloudflare zone id for the domain (from secrets/platform/cloudflare.env)."
  type        = string
}

variable "hsts_max_age" {
  description = "HSTS max-age in seconds. Start short; raise to 31536000 once HTTPS is proven everywhere. No preload without the owner's yes."
  type        = number
  default     = 300
}

variable "rate_limit_requests" {
  description = "Requests per 10 s per IP allowed on login/signup/reset/contact paths (free plan: one rule, 10 s period)."
  type        = number
  default     = 20
}
