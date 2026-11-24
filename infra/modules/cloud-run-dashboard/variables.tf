variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "app_name" {
  type = string
}

variable "env" {
  description = "staging or prod."
  type        = string
  validation {
    condition     = contains(["staging", "prod"], var.env)
    error_message = "env must be staging or prod."
  }
}

variable "runtime_sa" {
  description = "The dashboard's identity, created by the bootstrap (<app>-<env>-dashboard)."
  type        = string
}

variable "deployer_sa" {
  description = "CI's deploy identity: it updates the service's image (ADR-0020)."
  type        = string
}

variable "deployer_role" {
  description = "The bootstrap's dashboardDeployer custom role (update the image, never invoke)."
  type        = string
}

variable "ledger_bucket" {
  type = string
}

variable "lock_bucket" {
  type = string
}

variable "pause_bucket" {
  type = string
}

variable "initial_image" {
  description = "A placeholder until the first deploy (the deploy pipeline owns the image)."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}
