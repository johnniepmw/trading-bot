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

module "job" {
  source = "../../modules/cloud-run-job"

  project_id   = local.b.project_id
  region       = local.b.region
  app_name     = local.b.app_name
  env          = "staging"
  runtime_sa   = local.b.sa_runtime["staging-job"]
  scheduler_sa = local.b.sa_runtime["scheduler"]
  deployer_sa  = local.b.sa_deployer
  plan_sa      = local.b.sa_infra_plan
  alert_email  = local.b.alert_email
  secrets      = [] # staging runs on fakes: no keys
}

output "job_name" {
  value = module.job.job_name
}

output "scheduler_job" {
  value = module.job.scheduler_job
}

output "ledger_bucket" {
  value = module.job.ledger_bucket
}

output "lock_bucket" {
  value = module.job.lock_bucket
}

output "flags_bucket" {
  value = module.job.flags_bucket
}
