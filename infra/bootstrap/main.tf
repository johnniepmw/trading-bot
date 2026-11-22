# One-time, owner-run foundation for an app's GCP project (make infra-bootstrap):
# APIs, OpenTofu state bucket, Artifact Registry, Workload Identity Federation for GitHub
# Actions, and three CI identities:
#   ci-deployer    main branch only          → push images, deploy staging and prod, ops workflow
#   ci-infra-plan  any branch of this repo   → read-only plans and drift checks
#   ci-infra-apply main branch only          → apply infra/envs
# No service-account keys are created, ever.

locals {
  apis = [
    "artifactregistry.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ]
  pool_principal_repo = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repository}"
  pool_principal_main = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.ref/refs/heads/main"
}

resource "google_project_service" "apis" {
  for_each           = toset(concat(local.apis, var.billing_account == "" ? [] : ["billingbudgets.googleapis.com"]))
  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

data "google_project" "this" {
  project_id = var.project_id
}

# ---------- state ------------------------------------------------------------------------
resource "google_storage_bucket" "state" {
  project                     = var.project_id
  name                        = "${var.project_id}-tfstate"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  versioning {
    enabled = true
  }
  lifecycle_rule {
    condition {
      num_newer_versions = 20
    }
    action {
      type = "Delete"
    }
  }
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [google_project_service.apis]
}

# ---------- images -----------------------------------------------------------------------
resource "google_artifact_registry_repository" "images" {
  project       = var.project_id
  location      = var.region
  repository_id = var.app_name
  format        = "DOCKER"
  description   = "Container images for ${var.app_name}"

  # Delete images older than 30 days, except the 30 most recent (rollback targets).
  cleanup_policy_dry_run = false
  cleanup_policies {
    id     = "delete-old"
    action = "DELETE"
    condition {
      tag_state  = "ANY"
      older_than = "2592000s" # 30 days
    }
  }
  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"
    most_recent_versions {
      keep_count = 30
    }
  }
  depends_on = [google_project_service.apis]
}

# ---------- workload identity federation -------------------------------------------------
resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC"
  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }
  # Only this repository can use the pool at all.
  attribute_condition = "assertion.repository == \"${var.github_repository}\""
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# ---------- CI identities ----------------------------------------------------------------
resource "google_service_account" "ci" {
  for_each     = toset(["ci-deployer", "ci-infra-plan", "ci-infra-apply"])
  project      = var.project_id
  account_id   = each.value
  display_name = "GitHub Actions: ${each.value}"
  depends_on   = [google_project_service.apis] # the IAM API must be on first (a new project's first run)
}

resource "google_service_account_iam_member" "wif_any_ref" {
  service_account_id = google_service_account.ci["ci-infra-plan"].name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.pool_principal_repo
}

resource "google_service_account_iam_member" "wif_main_only" {
  for_each           = toset(["ci-deployer", "ci-infra-apply"])
  service_account_id = google_service_account.ci[each.value].name
  role               = "roles/iam.workloadIdentityUser"
  member             = local.pool_principal_main
}

resource "google_artifact_registry_repository_iam_member" "push" {
  project    = var.project_id
  location   = var.region
  repository = google_artifact_registry_repository.images.name
  role       = "roles/artifactregistry.writer"
  member     = "serviceAccount:${google_service_account.ci["ci-deployer"].email}"
}

# Deployer: manage Cloud Run revisions and traffic project-wide (revision listing needs
# project scope). Acting as each runtime identity is granted per environment.
resource "google_project_iam_member" "deployer" {
  project = var.project_id
  role    = "roles/run.developer"
  member  = "serviceAccount:${google_service_account.ci["ci-deployer"].email}"
}

resource "google_project_iam_member" "infra_plan" {
  for_each = toset(["roles/viewer", "roles/iam.securityReviewer"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.ci["ci-infra-plan"].email}"
}

resource "google_project_iam_member" "infra_apply" {
  for_each = toset([
    "roles/viewer",
    "roles/run.admin",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.serviceAccountUser",
    "roles/secretmanager.admin",
    "roles/storage.admin",
    "roles/monitoring.editor",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.ci["ci-infra-apply"].email}"
}

resource "google_storage_bucket_iam_member" "state_read" {
  bucket = google_storage_bucket.state.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.ci["ci-infra-plan"].email}"
}

# ---------- cost guard ---------------------------------------------------------------------
resource "google_billing_budget" "monthly" {
  count           = var.billing_account == "" ? 0 : 1
  billing_account = var.billing_account
  display_name    = "${var.app_name} monthly budget"
  budget_filter {
    projects = ["projects/${data.google_project.this.number}"]
  }
  amount {
    specified_amount {
      currency_code = var.budget_currency
      units         = tostring(var.budget_amount)
    }
  }
  threshold_rules {
    threshold_percent = 0.5
  }
  threshold_rules {
    threshold_percent = 0.9
  }
  threshold_rules {
    threshold_percent = 1.0
  }
  depends_on = [google_project_service.apis]
}
