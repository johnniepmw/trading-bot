# The owner's dashboard (ADR-0020): a Cloud Run service per environment, behind IAP. This module
# owns the service's shape, identity and storage access. IAP itself, its OAuth client, the
# invoker (IAP's service agent) and the owner's access are set by the owner's
# scripts/dashboard-iap.sh, never here: OpenTofu never manages the invoker authoritatively and
# never grants allUsers or allAuthenticatedUsers (tests/infra/test_dashboard_module.py).
locals {
  service_name = "${var.app_name}-dashboard-${var.env}"
}

resource "google_cloud_run_v2_service" "this" {
  project             = var.project_id
  name                = local.service_name
  location            = var.region
  ingress             = "INGRESS_TRAFFIC_ALL" # IAP fronts the run.app URL; no load balancer
  deletion_protection = var.env == "prod"

  invoker_iam_disabled = false # every request needs run.invoker: only IAP's agent holds it

  template {
    service_account                  = var.runtime_sa
    timeout                          = "30s"
    max_instance_request_concurrency = 10

    scaling {
      min_instance_count = 0
      max_instance_count = 1
    }

    containers {
      image = var.initial_image
      args  = ["serve"]

      resources {
        limits   = { cpu = "1", memory = "512Mi" }
        cpu_idle = true
      }

      env {
        name  = "APP_ENV"
        value = var.env
      }
      env {
        name  = "LEDGER_BUCKET"
        value = var.ledger_bucket
      }
      env {
        name  = "LOCK_BUCKET"
        value = var.lock_bucket
      }
      env {
        name  = "PAUSE_BUCKET"
        value = var.pause_bucket
      }
    }
  }

  lifecycle {
    # the deploy pipeline owns the image; the owner's script owns IAP
    ignore_changes = [template[0].containers[0].image, client, client_version, iap_enabled]
  }
}

# ---------- storage: read the ledger and lock buckets; list and create pause requests ----------
resource "google_storage_bucket_iam_member" "ledger_read" {
  bucket = var.ledger_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.runtime_sa}"
}

resource "google_storage_bucket_iam_member" "lock_read" {
  bucket = var.lock_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.runtime_sa}"
}

resource "google_storage_bucket_iam_member" "pause_read" {
  bucket = var.pause_bucket
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.runtime_sa}"
}

# create-only: objectCreator can't delete or overwrite, so the page can pause but never resume
resource "google_storage_bucket_iam_member" "pause_create" {
  bucket = var.pause_bucket
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${var.runtime_sa}"
}

# ---------- the deployer updates the image on this service only, and can't invoke it ---------
resource "google_cloud_run_v2_service_iam_member" "deployer" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.this.name
  role     = var.deployer_role # the bootstrap's dashboardDeployer: no run.routes.invoke
  member   = "serviceAccount:${var.deployer_sa}"
}
