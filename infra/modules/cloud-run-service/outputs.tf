output "url" {
  value = google_cloud_run_v2_service.this.uri
}

output "service_name" {
  value = google_cloud_run_v2_service.this.name
}

output "runtime_service_account" {
  value = google_service_account.runtime.email
}

output "flags_bucket" {
  value = google_storage_bucket.flags.name
}

output "secret_ids" {
  value = values(local.secret_ids)
}
