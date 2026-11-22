# One environment of the bot as a scheduled Cloud Run job (ADR-0011, ADR-0013, ADR-0017):
# the job, its buckets (ledger, lock, flags), its schedule (created paused) and IAM.
#
# Identities come from the bootstrap; nothing here creates a service account. Secret
# containers and their versions are created outside OpenTofu (the bootstrap or
# `make secret-set`), so CI's plan identity never needs to read a secret value.
#
# Ownership split for the job (ADR-0004, amended for the job in Phase 2): OpenTofu owns its
# shape, including the per-environment wiring env vars; the deploy pipeline owns the image.

locals {
  job_name      = "${var.app_name}-${var.env}"
  ledger_bucket = "${var.project_id}-ledger-${var.env}" # the job refuses buckets not named -<env>
  lock_bucket   = "${var.project_id}-locks-${var.env}"
  flags_bucket  = "${var.project_id}-flags-${var.env}"
  soft_delete   = var.soft_delete_days * 86400
  mounts        = merge(var.secrets, var.owner_secrets)
}

# ---------- buckets ----------------------------------------------------------------------
# The ledger: every version kept, deleted versions recoverable for soft_delete_days. Never
# destroyed by OpenTofu (a lost ledger lifts the drawdown stop's memory; ADR-0017).
resource "google_storage_bucket" "ledger" {
  project                     = var.project_id
  name                        = local.ledger_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  versioning {
    enabled = true
  }
  soft_delete_policy {
    retention_duration_seconds = local.soft_delete
  }
  lifecycle {
    prevent_destroy = true
  }
}

# The run lock and the reference mark, apart from the ledger (ADR-0017).
resource "google_storage_bucket" "locks" {
  project                     = var.project_id
  name                        = local.lock_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  soft_delete_policy {
    retention_duration_seconds = local.soft_delete
  }
  lifecycle {
    prevent_destroy = true
  }
}

resource "google_storage_bucket" "flags" {
  project                     = var.project_id
  name                        = local.flags_bucket
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = var.env != "prod"
  versioning {
    enabled = true
  }
}

# Seeded once with no overrides (the registry defaults apply); never overwritten, because
# the `ops` workflow writes kill overrides here and a re-apply must not undo them.
resource "google_storage_bucket_object" "flags_seed" {
  bucket       = google_storage_bucket.flags.name
  name         = "flags.json"
  content      = jsonencode({ overrides = {} })
  content_type = "application/json"
  lifecycle {
    ignore_changes = all
  }
}

# ---------- IAM: the runtime identity -------------------------------------------------------
# objectAdmin: the job overwrites the ledger (an overwrite needs delete permission) and
# creates and deletes the lock. Deleted versions stay recoverable (soft delete).
resource "google_storage_bucket_iam_member" "runtime_ledger" {
  bucket = google_storage_bucket.ledger.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.runtime_sa}"
}

resource "google_storage_bucket_iam_member" "runtime_locks" {
  bucket = google_storage_bucket.locks.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.runtime_sa}"
}

resource "google_storage_bucket_iam_member" "runtime_flags" {
  bucket = google_storage_bucket.flags.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.runtime_sa}"
}

resource "google_secret_manager_secret_iam_member" "runtime_secrets" {
  for_each  = var.secrets
  project   = var.project_id
  secret_id = each.value
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${var.runtime_sa}"
}

# Plans refresh the seeded flags object; overrides are on/off switches, not account data.
resource "google_storage_bucket_iam_member" "plan_flags" {
  bucket = google_storage_bucket.flags.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.plan_sa}"
}

# The `ops` workflow's kill switch writes overrides here.
resource "google_storage_bucket_iam_member" "deployer_flags" {
  bucket = google_storage_bucket.flags.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.deployer_sa}"
}

# ---------- the job ------------------------------------------------------------------------
resource "google_cloud_run_v2_job" "this" {
  project             = var.project_id
  name                = local.job_name
  location            = var.region
  deletion_protection = var.env == "prod"

  template {
    task_count  = 1
    parallelism = 1
    template {
      service_account = var.runtime_sa
      timeout         = "${var.timeout_seconds}s"
      max_retries     = 0 # a retry would be a new attempt with a new run ID; the next day's run is the retry
      containers {
        image = var.initial_image
        args  = ["run"]
        resources {
          limits = { cpu = "1", memory = "512Mi" }
        }
        env {
          name  = "APP_ENV"
          value = var.env
        }
        env {
          name  = "LEDGER_BUCKET"
          value = google_storage_bucket.ledger.name
        }
        env {
          name  = "LOCK_BUCKET"
          value = google_storage_bucket.locks.name
        }
        env {
          name  = "FLAGS_BUCKET"
          value = google_storage_bucket.flags.name
        }
        dynamic "volume_mounts" {
          for_each = local.mounts
          content {
            name       = "secret-${volume_mounts.key}"
            mount_path = "/secrets/${volume_mounts.key}"
          }
        }
      }
      dynamic "volumes" {
        for_each = local.mounts
        content {
          name = "secret-${volumes.key}"
          secret {
            secret = volumes.value
            items {
              version = "latest"
              path    = "value"
            }
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].template[0].containers[0].image, # the deploy pipeline's
      template[0].labels,
      template[0].annotations,
      client,
      client_version,
    ]
  }

  depends_on = [
    google_storage_bucket_iam_member.runtime_ledger,
    google_storage_bucket_iam_member.runtime_locks,
    google_storage_bucket_iam_member.runtime_flags,
    google_secret_manager_secret_iam_member.runtime_secrets,
  ]
}

# ---------- the schedule (created paused; only the owner turns it on) ------------------------
resource "google_cloud_run_v2_job_iam_member" "scheduler_runs" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_job.this.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${var.scheduler_sa}"
}

resource "google_cloud_scheduler_job" "daily" {
  project     = var.project_id
  region      = var.region
  name        = "${local.job_name}-daily"
  description = "Starts ${local.job_name} on weekdays; created paused (Phase 2 plan, rollout)."
  schedule    = var.schedule
  time_zone   = "Europe/London"
  paused      = true

  retry_config {
    retry_count = 0 # a missed start is noticed by the daily-glance check, not retried blindly
  }

  http_target {
    http_method = "POST"
    uri         = "https://run.googleapis.com/v2/projects/${var.project_id}/locations/${var.region}/jobs/${google_cloud_run_v2_job.this.name}:run"
    oauth_token {
      service_account_email = var.scheduler_sa
      scope                 = "https://www.googleapis.com/auth/cloud-platform"
    }
  }

  lifecycle {
    ignore_changes = [paused] # turning it on (or `ops` pausing it) is never undone by an apply
  }

  depends_on = [google_cloud_run_v2_job_iam_member.scheduler_runs]
}
