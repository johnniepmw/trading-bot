variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "app_name" {
  description = "Short app name; the job is <app_name>-<env>."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,19}$", var.app_name))
    error_message = "app_name: lowercase letters, digits and hyphens, 2-20 chars."
  }
}

variable "env" {
  type = string
  validation {
    condition     = contains(["staging", "prod"], var.env)
    error_message = "env must be staging or prod."
  }
}

variable "runtime_sa" {
  description = "Email of the job's runtime identity, created by the bootstrap (never here)."
  type        = string
}

variable "scheduler_sa" {
  description = "Email of the identity Cloud Scheduler uses to start the job (bootstrap)."
  type        = string
}

variable "deployer_sa" {
  description = "Email of the CI deployer identity (main branch only)."
  type        = string
}

variable "schedule" {
  description = "Cron schedule in Europe/London. Keep in step with schedule_time in src/tradingbot/config/bot.toml."
  type        = string
  default     = "15 7 * * 1-5"
}

variable "timeout_seconds" {
  description = "Job timeout. Must stay below the ledger lock's STALE_AFTER (1 hour, ADR-0017); a test checks it."
  type        = number
  default     = 1800
  validation {
    condition     = var.timeout_seconds > 0 && var.timeout_seconds < 3600
    error_message = "timeout_seconds must be below 3600 (the ledger lock's STALE_AFTER)."
  }
}

variable "secrets" {
  description = "Secret names the job reads. This module creates each container (<env>-<name>, no version: the owner adds the value with `make secret-set` before the job needs it), grants the job access and mounts it at /secrets/<name>/value."
  type        = list(string)
  default     = []
}

variable "owner_secrets" {
  description = "Owner-only secrets (the live switch) as mount name => secret ID, mounted the same way; created and access-granted by the bootstrap, because CI can't change them (ADR-0009)."
  type        = map(string)
  default     = {}
  validation {
    condition     = alltrue([for name in keys(var.owner_secrets) : !contains(var.secrets, name)])
    error_message = "a secret name can't be both CI-managed and owner-only: the mount would be ambiguous."
  }
}

variable "plan_sa" {
  description = "Email of CI's plan identity: it may read the flags object (no account data) so plans can refresh it."
  type        = string
}

variable "initial_image" {
  description = "Image used only when the job is first created; the deploy pipeline sets the real one."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/job:latest"
}

variable "soft_delete_days" {
  description = "How long deleted or overwritten ledger and lock objects stay recoverable."
  type        = number
  default     = 30
  validation {
    condition     = var.soft_delete_days >= 7 && var.soft_delete_days <= 90
    error_message = "Cloud Storage allows soft delete of 7 to 90 days; this module doesn't allow turning it off."
  }
}
