terraform {
  required_version = ">= 1.8.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.0, < 8.0"
    }
  }
}

provider "google" {
  # Owner's credentials: gcloud auth application-default login
  user_project_override = true
  billing_project       = var.project_id
}
