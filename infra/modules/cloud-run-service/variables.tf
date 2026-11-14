variable "project_id" {
  type = string
}

variable "region" {
  type = string
}

variable "app_name" {
  description = "Short app name; services are <app_name>-<env>."
  type        = string
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,19}$", var.app_name))
    error_message = "app_name: lowercase letters, digits and hyphens, 2-20 chars (service account IDs are limited to 30)."
  }
}

variable "env" {
  type = string
  validation {
    condition     = contains(["staging", "prod"], var.env)
    error_message = "env must be staging or prod."
  }
}

variable "public" {
  description = "Allow unauthenticated invocations (public web app). Enables the uptime check."
  type        = bool
  default     = true
}

variable "min_instances" {
  type    = number
  default = 0
}

variable "max_instances" {
  type    = number
  default = 3
}

variable "cpu" {
  type    = string
  default = "1"
}

variable "memory" {
  type    = string
  default = "512Mi"
}

variable "secrets" {
  description = "Secret names mounted at /secrets/<name>/value. Values are set with make secret-set before the first deploy."
  type        = list(string)
  default     = []
}

variable "deletion_protection" {
  type    = bool
  default = true
}

variable "initial_image" {
  description = "Image used only when the service is first created."
  type        = string
  default     = "us-docker.pkg.dev/cloudrun/container/hello"
}

variable "deployer_sa" {
  description = "Email of the CI deployer identity (main branch only)."
  type        = string
}

variable "extra_developer_sas" {
  description = "Other CI identities allowed to deploy revisions of this service (e.g. the PR preview identity on staging)."
  type        = list(string)
  default     = []
}

variable "alert_email" {
  description = "Owner email for uptime alerts; empty disables alerting."
  type        = string
  default     = ""
}
