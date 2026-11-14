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
  env                 = "prod"
  public              = true
  min_instances       = 0 # raise to 1 to avoid cold starts
  max_instances       = 5
  secrets             = [] # e.g. ["broker-api-key"]; set values with make secret-set
  deletion_protection = true
  deployer_sa         = local.b.sa_deployer
  extra_developer_sas = [] # the PR preview identity never touches prod
  alert_email         = local.b.alert_email
}

output "service_url" {
  value = module.service.url
}

output "flags_bucket" {
  value = module.service.flags_bucket
}
