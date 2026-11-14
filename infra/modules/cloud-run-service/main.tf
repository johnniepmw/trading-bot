# One environment of the app: runtime identity, Cloud Run service, secrets (empty
# containers), flags bucket, IAM for CI identities, uptime check and alert.
#
# Ownership split (ADR-0004): OpenTofu owns the service's shape. The deploy pipeline owns
# the image, traffic and environment variables, so those are in ignore_changes.

locals {
  service_name = "${var.app_name}-${var.env}"
  secret_ids   = { for s in var.secrets : s => "${var.env}-${s}" }
}

resource "google_service_account" "runtime" {
  project      = var.project_id
  account_id   = "${var.app_name}-${var.env}-run"
  display_name = "${local.service_name} runtime"
}

# ---------- secrets: containers only, values are added by the owner (make secret-set) ----
resource "google_secret_manager_secret" "this" {
  for_each  = local.secret_ids
  project   = var.project_id
  secret_id = each.value
  replication {
    auto {}
  }
  labels = { app = var.app_name, env = var.env }
}

# A non-secret placeholder so the service can start before the owner adds the real value
# (make secret-set adds a newer version; mounts read "latest"). Apps must treat
# "__unset__" as missing and fail closed.
resource "google_secret_manager_secret_version" "placeholder" {
  for_each    = google_secret_manager_secret.this
  secret      = each.value.id
  secret_data = "__unset__"
  lifecycle {
    ignore_changes = all
  }
}

resource "google_secret_manager_secret_iam_member" "runtime_access" {
  for_each  = google_secret_manager_secret.this
  project   = var.project_id
  secret_id = each.value.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.runtime.email}"
}

# ---------- flags: remote overrides read by the app, written by the ops workflow ----------
resource "google_storage_bucket" "flags" {
  project                     = var.project_id
  name                        = "${var.project_id}-flags-${var.env}"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = !var.deletion_protection
  versioning {
    enabled = true
  }
}

resource "google_storage_bucket_iam_member" "flags_read" {
  bucket = google_storage_bucket.flags.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.runtime.email}"
}

resource "google_storage_bucket_iam_member" "flags_write" {
  bucket = google_storage_bucket.flags.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${var.deployer_sa}"
}

# ---------- the service -----------------------------------------------------------------
resource "google_cloud_run_v2_service" "this" {
  project             = var.project_id
  name                = local.service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL"
  deletion_protection = var.deletion_protection

  template {
    service_account = google_service_account.runtime.email
    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }
    containers {
      # Placeholder until the first deploy; the pipeline sets the real image.
      image = var.initial_image
      ports {
        container_port = 8080
      }
      resources {
        limits   = { cpu = var.cpu, memory = var.memory }
        cpu_idle = true
      }
      startup_probe {
        http_get {
          path = "/healthz"
        }
      }
      liveness_probe {
        http_get {
          path = "/healthz"
        }
      }
      dynamic "volume_mounts" {
        for_each = local.secret_ids
        content {
          name       = "secret-${volume_mounts.key}"
          mount_path = "/secrets/${volume_mounts.key}"
        }
      }
    }
    dynamic "volumes" {
      for_each = local.secret_ids
      content {
        name = "secret-${volumes.key}"
        secret {
          secret = google_secret_manager_secret.this[volumes.key].secret_id
          items {
            version = "latest"
            path    = "value"
          }
        }
      }
    }
  }

  lifecycle {
    ignore_changes = [
      template[0].containers[0].image,
      template[0].containers[0].env,
      template[0].revision,
      template[0].labels,
      template[0].annotations,
      client,
      client_version,
      traffic,
    ]
  }

  depends_on = [
    google_secret_manager_secret_iam_member.runtime_access,
    google_secret_manager_secret_version.placeholder,
  ]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  count    = var.public ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# CI identities that may deploy this service (the deployer always; the preview identity
# only for staging) must be able to act as the runtime identity.
resource "google_service_account_iam_member" "act_as" {
  for_each           = toset(concat([var.deployer_sa], var.extra_developer_sas))
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${each.value}"
}

resource "google_cloud_run_v2_service_iam_member" "developers" {
  for_each = toset(var.extra_developer_sas)
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this.name
  role     = "roles/run.developer"
  member   = "serviceAccount:${each.value}"
}

# ---------- monitoring ------------------------------------------------------------------
resource "google_monitoring_notification_channel" "email" {
  count        = var.alert_email == "" ? 0 : 1
  project      = var.project_id
  display_name = "${local.service_name} owner email"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

resource "google_monitoring_uptime_check_config" "health" {
  count        = var.public ? 1 : 0
  project      = var.project_id
  display_name = "${local.service_name} /healthz"
  timeout      = "10s"
  period       = "300s"

  http_check {
    path         = "/healthz"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }
  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = trimprefix(google_cloud_run_v2_service.this.uri, "https://")
    }
  }
}

resource "google_monitoring_alert_policy" "uptime" {
  count                 = var.public && var.alert_email != "" ? 1 : 0
  project               = var.project_id
  display_name          = "${local.service_name} is down"
  combiner              = "OR"
  notification_channels = [google_monitoring_notification_channel.email[0].id]

  conditions {
    display_name = "Uptime check failing"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.\"check_id\"=\"${google_monitoring_uptime_check_config.health[0].uptime_check_id}\""
      duration        = "600s"
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      aggregations {
        alignment_period     = "300s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.*"]
      }
      trigger {
        count = 1
      }
    }
  }
  alert_strategy {
    auto_close = "1800s"
  }
}
