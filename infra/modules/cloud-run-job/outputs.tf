output "job_name" {
  value = google_cloud_run_v2_job.this.name
}

output "scheduler_job" {
  value = google_cloud_scheduler_job.daily.name
}

output "ledger_bucket" {
  value = google_storage_bucket.ledger.name
}

output "lock_bucket" {
  value = google_storage_bucket.locks.name
}

output "flags_bucket" {
  value = google_storage_bucket.flags.name
}
