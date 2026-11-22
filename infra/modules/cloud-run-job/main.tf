# One environment of the bot as a scheduled Cloud Run job (ADR-0011, ADR-0013, ADR-0017):
# the job, its buckets (ledger, lock, flags), its schedule (created paused) and IAM.
#
# Identities come from the bootstrap; nothing here creates a service account. Secret
# containers are created here without versions (the owner adds values with `make
# secret-set`), so OpenTofu state never holds a secret value and CI's plan identity never
# needs to read one. The owner-only live switch is created by the bootstrap.
#
# Ownership split for the job: OpenTofu owns its shape, including the per-environment wiring
# env vars; the deploy pipeline changes only the image (step 6; ADR-0011's amendment there).

locals {
  job_name      = "${var.app_name}-${var.env}"
  ledger_bucket = "${var.project_id}-ledger-${var.env}" # the job refuses buckets not named -<env>
  lock_bucket   = "${var.project_id}-locks-${var.env}"
  flags_bucket  = "${var.project_id}-flags-${var.env}"
  soft_delete   = var.soft_delete_days * 86400
  secret_ids    = { for name in var.secrets : name => "${var.env}-${name}" }
  mounts        = merge({ for name, secret in google_secret_manager_secret.this : name => secret.secret_id }, var.owner_secrets)
}

# ---------- secrets: containers only (no version: the owner adds values) ---------------------
resource "google_secret_manager_secret" "this" {
  for_each  = local.secret_ids
  project   = var.project_id
  secret_id = each.value
  replication {
    auto {}
  }
  labels = { app = var.app_name, env = var.env }
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
# objectUser: the job overwrites the ledger (an overwrite needs delete permission) and
# creates and deletes the lock; no object or managed-folder IAM. Deleted versions stay
# recoverable (soft delete), and setup counts soft-deleted ones as "a ledger existed".
resource "google_storage_bucket_iam_member" "runtime_ledger" {
  bucket = google_storage_bucket.ledger.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${var.runtime_sa}"
}

resource "google_storage_bucket_iam_member" "runtime_locks" {
  bucket = google_storage_bucket.locks.name
  role   = "roles/storage.objectUser"
  member = "serviceAccount:${var.runtime_sa}"
}

resource "google_storage_bucket_iam_member" "runtime_flags" {
  bucket = google_storage_bucket.flags.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.runtime_sa}"
}

resource "google_secret_manager_secret_iam_member" "runtime_secrets" {
  for_each  = google_secret_manager_secret.this
  project   = var.project_id
  secret_id = each.value.secret_id
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

# ---------- alerts (Phase 2 step 7a; monitoring AC5) ------------------------------------------
# The bot puts `alert: <kind>` on every ERROR line and on chosen WARNINGs (drawdown crossings).
# This policy matches that field only, never Cloud Run's own "task failed" entries, so a dry
# run that stops correctly (WARNING) pages nobody. The notification names the kind; the
# line itself (which may hold account data) stays in Cloud Logging (ADR-0014).
resource "google_monitoring_notification_channel" "email" {
  project      = var.project_id
  display_name = "${local.job_name} owner email"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

resource "google_monitoring_alert_policy" "bot_alert" {
  project               = var.project_id
  display_name          = "${local.job_name}: the bot needs attention"
  combiner              = "OR"
  notification_channels = concat([google_monitoring_notification_channel.email.id], var.push_channels)

  conditions {
    display_name = "A bot log line with an alert kind"
    condition_matched_log {
      filter           = "resource.type=\"cloud_run_job\" AND resource.labels.job_name=\"${local.job_name}\" AND jsonPayload.alert:*"
      label_extractors = { kind = "EXTRACT(jsonPayload.alert)" }
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
    auto_close = "1800s"
  }

  documentation {
    mime_type = "text/markdown"
    content   = "The ${local.job_name} job logged an alert (kind in the incident's labels). Read it with `make logs ENV=${var.env}`; runbooks in docs/runbooks/. New orders stop on their own after any stop; nothing needs doing to stay safe."
  }
}

resource "google_monitoring_alert_policy" "schedule_failed" {
  project               = var.project_id
  display_name          = "${local.job_name}: the schedule failed to start the job"
  combiner              = "OR"
  notification_channels = concat([google_monitoring_notification_channel.email.id], var.push_channels)

  conditions {
    display_name = "Cloud Scheduler reported an error"
    condition_matched_log {
      filter = "resource.type=\"cloud_scheduler_job\" AND resource.labels.job_id=\"${local.job_name}-daily\" AND severity>=ERROR"
    }
  }

  alert_strategy {
    notification_rate_limit {
      period = "300s"
    }
    auto_close = "1800s"
  }

  documentation {
    mime_type = "text/markdown"
    content   = "Cloud Scheduler could not start ${local.job_name}: today's run is missing. Check the schedule and the job in the console."
  }
}
