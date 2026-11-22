variable "project_id" {
  description = "Existing GCP project for this app (billing linked)."
  type        = string
}

variable "region" {
  type    = string
  default = "europe-west2"
}

variable "app_name" {
  description = "Short app name used for services (<app_name>-staging/-prod) and the image repository."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,19}$", var.app_name))
    error_message = "app_name: lowercase letters, digits and hyphens, 2-20 chars."
  }
}

variable "github_repository" {
  description = "owner/repo allowed to use workload identity federation."
  type        = string
}

variable "github_repository_id" {
  description = "Numeric ID of the repository (gh api repos/<owner>/<repo> --jq .id); matched instead of the name."
  type        = string
}

variable "github_owner_id" {
  description = "Numeric ID of the repository's owner (gh api repos/<owner>/<repo> --jq .owner.id)."
  type        = string
}

variable "alert_email" {
  description = "Owner email for uptime alerts (passed to environments)."
  type        = string
  default     = ""
}

variable "billing_account" {
  description = "Billing account ID for a budget alert (optional; empty to skip)."
  type        = string
  default     = ""
}

variable "budget_amount" {
  type    = number
  default = 50
}

variable "budget_currency" {
  type    = string
  default = "GBP"
}
