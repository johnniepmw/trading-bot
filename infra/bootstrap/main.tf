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
    "cloudscheduler.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "iap.googleapis.com", # the dashboard's sign-in gate (the owner's IAP script configures it)
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ]
  pool_principal_repo = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository_id/${var.github_repository_id}"
  # main only, and only for push, manual dispatch or schedule (never pull_request_target or comments)
  pool_principal_main = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.trusted_main/true"
  # the dashboard services' identities (dashboard plan, slice 1): read the ledger, create pause requests
  runtime_accounts = toset(["staging-job", "prod-job", "scheduler", "staging-dashboard", "prod-dashboard"])
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
  # The image a deploy replaced is tagged previous-<env> for `ops rollback` (ADR-0019):
  # keep it however old, so a rollback target is never cleaned up.
  cleanup_policies {
    id     = "keep-rollback"
    action = "KEEP"
    condition {
      tag_state    = "TAGGED"
      tag_prefixes = ["previous-"]
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
    "google.subject"          = "assertion.sub"
    "attribute.repository"    = "assertion.repository"
    "attribute.repository_id" = "assertion.repository_id"
    "attribute.ref"           = "assertion.ref"
    "attribute.trusted_main"  = "assertion.ref == 'refs/heads/main' && assertion.event_name in ['push', 'workflow_dispatch', 'schedule'] ? 'true' : 'false'"
  }
  # Only this repository can use the pool at all, matched by its permanent numeric IDs: a
  # name could be re-registered by someone else if the repository were renamed or deleted.
  attribute_condition = "assertion.repository_id == \"${var.github_repository_id}\" && assertion.repository_owner_id == \"${var.github_owner_id}\""
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

# Deployer: read Cloud Run jobs and executions project-wide (listing and waiting need
# project scope) and cancel an execution its own check left behind. Updating and running a
# job is granted on each job by the job module (roles/run.developer there), so the deployer
# can change nothing else in the project. Acting as each runtime identity is granted below.
resource "google_project_iam_custom_role" "job_deployer_reader" {
  project = var.project_id
  role_id = "jobDeployerReader"
  title   = "Read Cloud Run jobs, executions and services; cancel executions"
  permissions = [
    "run.jobs.get", "run.jobs.list",
    "run.services.get", "run.services.list", # finds the dashboard service (ADR-0020); updates need its own grant
    "run.executions.get", "run.executions.list", "run.executions.cancel",
    "run.operations.get", "run.operations.list",
    "run.locations.list",
  ]
}

resource "google_project_iam_member" "deployer" {
  project = var.project_id
  role    = google_project_iam_custom_role.job_deployer_reader.id
  member  = "serviceAccount:${google_service_account.ci["ci-deployer"].email}"
}

# The `ops` workflow may pause the bot's schedule (stop new runs), never resume it or change it.
resource "google_project_iam_custom_role" "scheduler_pauser" {
  project     = var.project_id
  role_id     = "schedulerPauser"
  title       = "Pause Cloud Scheduler jobs"
  permissions = ["cloudscheduler.jobs.get", "cloudscheduler.jobs.list", "cloudscheduler.jobs.pause"]
}

resource "google_project_iam_member" "deployer_pause" {
  project = var.project_id
  role    = google_project_iam_custom_role.scheduler_pauser.id
  member  = "serviceAccount:${google_service_account.ci["ci-deployer"].email}"
}

# Plans run from any branch of this repository, so this identity reads configuration only:
# no log entries and no bucket objects (account data lives there; ADR-0014), apart from the
# OpenTofu state bucket below.
resource "google_project_iam_custom_role" "infra_plan_reader" {
  project = var.project_id
  role_id = "infraPlanReader"
  title   = "Read infrastructure configuration (no data)"
  permissions = [
    "resourcemanager.projects.get",
    "serviceusage.services.get", "serviceusage.services.list",
    "storage.buckets.get", "storage.buckets.getIamPolicy", "storage.buckets.list",
    "run.jobs.get", "run.jobs.list", "run.jobs.getIamPolicy",
    "run.services.get", "run.services.list", "run.services.getIamPolicy",
    "run.executions.get", "run.executions.list",
    "cloudscheduler.jobs.get", "cloudscheduler.jobs.list",
    "secretmanager.secrets.get", "secretmanager.secrets.list", "secretmanager.secrets.getIamPolicy",
    "secretmanager.versions.get", "secretmanager.versions.list",
    "iam.serviceAccounts.get", "iam.serviceAccounts.list", "iam.serviceAccounts.getIamPolicy",
    "monitoring.alertPolicies.get", "monitoring.alertPolicies.list",
    "monitoring.notificationChannels.get", "monitoring.notificationChannels.list",
    "logging.logMetrics.get", "logging.logMetrics.list",
    "artifactregistry.repositories.get",
  ]
}

resource "google_project_iam_member" "infra_plan" {
  # static keys: the custom role's id is only known after apply
  for_each = { reader = google_project_iam_custom_role.infra_plan_reader.id, security = "roles/iam.securityReviewer" }

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.ci["ci-infra-plan"].email}"
}

# The state from the first bootstrap keys this binding by role name; rename it in place
# rather than destroy and re-create the same binding (the two could race).
moved {
  from = google_project_iam_member.infra_plan["roles/iam.securityReviewer"]
  to   = google_project_iam_member.infra_plan["security"]
}

resource "google_project_iam_member" "infra_apply" {
  for_each = toset([
    "roles/viewer",
    "roles/run.admin",
    "roles/storage.admin",
    "roles/monitoring.editor",
    "roles/cloudscheduler.admin",
    "roles/logging.configWriter",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.ci["ci-infra-apply"].email}"
}

# Secret Manager for every secret except the owner-only `prod-operator` (ADR-0009: no CI path
# may switch on live trading or change capital): CI can't add versions to it, change its
# access, update or delete it. This only holds while CI can't act as another identity that
# can write secrets: CI acts only as the runtime identities below (no project-wide actAs or
# service-account admin), and the owner checks that no service account in the project holds
# Owner, Editor or a Secret Manager role (runbook first-time-setup.md).
resource "google_project_iam_member" "infra_apply_secrets" {
  project = var.project_id
  role    = "roles/secretmanager.admin"
  member  = "serviceAccount:${google_service_account.ci["ci-infra-apply"].email}"
  condition {
    title       = "not-the-operator-secret"
    description = "Every secret except prod-operator (owner only, ADR-0009)"
    expression  = "!resource.name.startsWith(\"projects/${data.google_project.this.number}/secrets/prod-operator\")"
  }
}

# ---------- runtime identities (owner-created) and the live switch (ADR-0009) --------------
# The jobs', the scheduler's and the dashboards' identities are created here, so CI needs no
# service-account admin and no project-wide actAs: it may act only as these five.
resource "google_service_account" "runtime" {
  for_each     = local.runtime_accounts
  project      = var.project_id
  account_id   = "${var.app_name}-${each.value}"
  display_name = "${var.app_name} ${each.value}"
  depends_on   = [google_project_service.apis]
}

resource "google_service_account_iam_member" "ci_act_as_runtime" {
  for_each = {
    for pair in setproduct(["ci-deployer", "ci-infra-apply"], tolist(local.runtime_accounts)) :
    "${pair[0]}:${pair[1]}" => { ci = pair[0], runtime = pair[1] }
  }
  service_account_id = google_service_account.runtime[each.value.runtime].name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.ci[each.value.ci].email}"
}

# The operator secret is created here, by the owner, so that no CI identity ever has write
# access to it. It starts as "__unset__" (paper trading); only the owner adds versions
# (make secret-set NAME=operator ENV=prod). The prod job's identity can read it.
resource "google_secret_manager_secret" "operator" {
  project   = var.project_id
  secret_id = "prod-operator"
  replication {
    auto {}
  }
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [google_project_service.apis]
}

resource "google_secret_manager_secret_version" "operator_placeholder" {
  secret      = google_secret_manager_secret.operator.id
  secret_data = "__unset__"
  lifecycle {
    ignore_changes = all # the owner's own versions supersede it; never rewritten
  }
}

resource "google_secret_manager_secret_iam_member" "operator_reader" {
  project   = var.project_id
  secret_id = google_secret_manager_secret.operator.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime["prod-job"].email}"
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
