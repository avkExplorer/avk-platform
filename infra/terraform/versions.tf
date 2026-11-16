terraform {
  required_version = "= 1.17.1"

  required_providers {
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "= 5.28.1"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
  }

  # Local state outside the repo: it holds the tunnel secret. `avk tf` mounts %AVK_HOME%\tfstate at /state.
  # Covered by the encrypted backup from Phase 4.
  backend "local" {
    path = "/state/cloudflare.tfstate"
  }
}

# Reads CLOUDFLARE_API_TOKEN from the environment (passed in by `avk tf`, never on a command line).
provider "cloudflare" {}
