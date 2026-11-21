terraform {
  required_version = ">= 1.8.0"
  backend "gcs" {} # bucket and prefix are passed by scripts/tofu.sh
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.0"
    }
  }
}

variable "state_bucket" {
  description = "Set by scripts/tofu.sh (TF_VAR_state_bucket)."
  type        = string
}

data "terraform_remote_state" "bootstrap" {
  backend = "gcs"
  config = {
    bucket = var.state_bucket
    prefix = "bootstrap"
  }
}

locals {
  b = data.terraform_remote_state.bootstrap.outputs
}

provider "google" {}

module "service" {
  source = "../../modules/cloud-run-service"

  project_id          = local.b.project_id
  region              = local.b.region
  app_name            = local.b.app_name
  env                 = "staging"
  public              = true
  min_instances       = 0
  max_instances       = 2
  secrets             = [] # e.g. ["broker-api-key"]; set values with make secret-set
  deletion_protection = false
  deployer_sa         = local.b.sa_deployer
  alert_email         = local.b.alert_email
}

output "service_url" {
  value = module.service.url
}

output "flags_bucket" {
  value = module.service.flags_bucket
}
